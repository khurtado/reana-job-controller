# -*- coding: utf-8 -*-
#
# This file is part of REANA.
# Copyright (C) 2019 CERN.
#
# REANA is free software; you can redistribute it and/or modify it
# under the terms of the MIT License; see LICENSE file for more details.

"""HTCondor VC3 Job Manager."""

import logging
import traceback
import uuid
import htcondor
import classad
import os
import re
import shutil
import filecmp
import pwd

from retrying import retry
from reana_job_controller.variables import (MAX_JOB_RESTARTS, SHARED_VOLUME_PATH_ROOT)

from kubernetes.client.rest import ApiException
from reana_commons.config import K8S_DEFAULT_NAMESPACE
from reana_db.database import Session
from reana_db.models import Workflow

from reana_job_controller.job_manager import JobManager

def detach(f):
    """Decorator for creating a forked process"""
    def fork(*args, **kwargs):
        r, w = os.pipe()
        pid = os.fork()
        if pid: # parent
            os.close(w)
            r = os.fdopen(r)
            fout = r.read()
            return fout
        else: # child
            try:
                os.close(r)
                w = os.fdopen(w, 'w')
                #os.setuid(int(os.environ.get('VC3USERID')))
                user_id = pwd.getpwnam(os.getenv('USER')).pw_uid
                os.setuid(user_id)
                out = f(*args, **kwargs)
                w.write(str(out))
                w.close()
            finally:
                os._exit(0)

    return fork

@retry(stop_max_attempt_number=MAX_JOB_RESTARTS)
#@detach
def submit(schedd, sub):
    try:
        with schedd.transaction() as txn:
            clusterid = sub.queue(txn)
    except Exception as e:
        logging.debug("Error submission: {0}".format(e))
        raise e

    return clusterid

def get_input_files(workflow_workspace):
    """Get files from workflow space
    :param workflow_workspace: Workflow directory
    """
    # First, get list of input files
    input_files = []
    for root, dirs, files in os.walk(workflow_workspace):
        for filename in files:
           input_files.append(os.path.join(root, filename))
    
    return ",".join(input_files)

def get_schedd():
    """Find and return the HTCondor sched.
    :returns: htcondor schedd object."""

    # Getting remote scheduler
    schedd_ad = classad.ClassAd()
    schedd_ad["MyAddress"] = os.environ.get("REANA_JOB_CONTROLLER_VC3_HTCONDOR_ADDR", None) 
    schedd = htcondor.Schedd(schedd_ad)
    return schedd

def get_wrapper(workflow_workspace):
    """Get wrapper for job. Transfer if it does not exist.
    :param shared_path: Shared FS directory, e.g.: /var/reana."""
    
    wrapper = os.path.join(workflow_workspace, 'wrapper', 'job_wrapper.sh')
    local_wrapper = '/code/files/job_wrapper.sh'
    if os.path.exists(wrapper) and filecmp.cmp(local_wrapper, wrapper):
        return wrapper
    try:
        if not os.path.isdir(os.path.dirname(wrapper)):
            os.mkdir(os.path.dirname(wrapper))
        shutil.copy('/code/files/job_wrapper.sh', wrapper)
    except Exception as e:
        logging.debug("Error transfering wrapper : {0}.".format(e))
        logging.debug("user: {0}".format(pwd.getpwuid(os.getuid()).pw_name))
        raise e
    
    return wrapper

class HTCondorJobManagerVC3(JobManager):
    """HTCondor VC3 job management."""

    def __init__(self, docker_img=None, cmd=None, prettified_cmd=None,
                 env_vars=None, workflow_uuid=None, workflow_workspace=None,
                 cvmfs_mounts='false', shared_file_system=False,
                 job_name=None, kerberos=False, kubernetes_uid=None):
        """Instantiate HTCondorVC3 job manager.

        :param docker_img: Docker image.
        :type docker_img: str
        :param cmd: Command to execute.
        :type cmd: list
        :param prettified_cmd: pretified version of command to execute.
        :type prettified_cmd: str
        :param env_vars: Environment variables.
        :type env_vars: dict
        :param workflow_id: Unique workflow id.
        :type workflow_id: str
        :param workflow_workspace: Workflow workspace path.
        :type workflow_workspace: str
        :param cvmfs_mounts: list of CVMFS mounts as a string.
        :type cvmfs_mounts: str
        :param shared_file_system: if shared file system is available.
        :type shared_file_system: bool
        :param job_name: Name of the job
        :type job_name: str
        """
        self.docker_img = docker_img or ''
        self.cmd = cmd or ''
        self.env_vars = env_vars or {}
        self.workflow_uuid = workflow_uuid
        self.compute_backend = "HTCondorVC3"
        self.workflow_workspace = workflow_workspace
        self.cvmfs_mounts = cvmfs_mounts
        self.shared_file_system = shared_file_system
        self.schedd = get_schedd()
        self.wrapper = get_wrapper(workflow_workspace)
        self.job_name = job_name
        self.kerberos = kerberos
        self.prettified_cmd = prettified_cmd


    @JobManager.execution_hook
    def execute(self):
        """Execute / submit a job with HTCondor."""
        sub = htcondor.Submit()
        sub['executable'] = self.wrapper
        # condor arguments require double quotes to be escaped
        #sub['arguments'] = 'exec --home .{0}:{0} docker://{1} {2}'.format(self.workflow_workspace,
        #        self.docker_img, re.sub(r'"', '\\"', self.cmd))
        sub['arguments'] = "{0} {1} {2}".format(self.workflow_workspace,self.docker_img,
                re.sub(r'"', '\\"', self.cmd))
        sub['Output'] = '/tmp/$(Cluster)-$(Process).out'
        sub['Error'] = '/tmp/$(Cluster)-$(Process).err'
        #sub['transfer_input_files'] = get_input_files(self.workflow_workspace)
        sub['InitialDir'] = '/tmp'
        sub['+WantIOProxy'] = 'true'
        job_env = 'reana_workflow_dir={0}'.format(self.workflow_workspace)
        for key, value in self.env_vars.items():
            job_env += '; {0}={1}'.format(key, value)
        sub['environment'] = job_env
        sub['on_exit_remove'] = '(ExitBySignal == False) && ((ExitCode == 0) || (ExitCode !=0 && NumJobStarts > {0}))'.format(MAX_JOB_RESTARTS)
        clusterid = submit(self.schedd, sub)
        logging.warning("Submitting job clusterid: {0}".format(clusterid))
        return str(clusterid)


    def stop(self, backend_job_id, asynchronous=True):
        """Stop HTCondor job execution.

        :param backend_job_id: HTCondor job id.
        :param asynchronous: Ignored.
        """
        self.schedd.act(htcondor.JobAction.Remove, 'ClusterId==%d' % backend_job_id)


    def add_shared_volume(self, job):
        """Add shared CephFS volume to a given job.
        """
        pass #Not Implemented yet


    def condor_delete_job(job, asynchronous=True):
        """Delete HTCondor job.
    
        :param job: The :string: HTCondor cluster ID of the job to be removed.
        :param asynchronous: Place holder for comparison to k8s.
        """
    
        self.schedd.act(htcondor.JobAction.Remove, 'ClusterID==%d' % job)

