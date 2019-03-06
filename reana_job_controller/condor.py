# -*- coding: utf-8 -*-
#
# This file is part of REANA.
# Copyright (C) 2017, 2018 CERN.
#
# REANA is free software; you can redistribute it and/or modify it
# under the terms of the MIT License; see LICENSE file for more details.

"""HTCondor wrapper. Utilize libfactory's htcondorlib for job submission"""

import json
import logging
import os
import time
import threading
import traceback
import htcondor
import classad
from subprocess import check_output

from flask import current_app as app
from reana_db.database import Session
from reana_db.models import Job

from reana_job_controller import config, volume_templates
from reana_job_controller.errors import ComputingBackendSubmissionError

def get_schedd():
    """Find and return the HTCondor sched.
    :returns: htcondor schedd object."""

    # Getting remote scheduler
    schedd_ad = classad.ClassAd()
    schedd_ad["MyAddress"] = os.environ.get("HTCONDOR_ADDR", None) 
    schedd = htcondor.Schedd(schedd_ad)
    return schedd

def condor_instantiate_job(job_id, workflow_workspace, docker_img, cmd, 
                           cvmfs_mounts, env_vars, shared_file_system, job_type,
                           namespace='default'):
    """Create condor job.

    :param job_id: Job uuid from reana perspective.
    :param docker_img: Docker / singularity  image to run the job.
    :param cmd: Command provided to the container.
    :param cvmfs_repos: List of CVMFS repository names.
    :param env_vars: Dictionary representing environment variables
        as {'var_name': 'var_value'}.
    :param namespace: Job's namespace.
    :shared_file_system: Boolean which represents whether the job
        should have a shared file system mounted.
    :returns: cluster_id of htcondor job.
    """

    schedd = get_schedd()
    sub = htcondor.Submit()
    sub['executable'] = '/usr/bin/singularity'
    sub['arguments'] = "exec docker://{0} {1}".format(docker_img,cmd)
    with schedd.transaction() as txn:
        clusterid = sub.queue(txn,1)

    return clusterid

def condor_watch_jobs(job_db):
    """Watch currently running HTCondor jobs.
    :param job_db: Dictionary which contains all current jobs.
    """
    while True:
        #logging.debug('Starting a new stream request to watch Condor Jobs')
        time.sleep(15) # Not implemented yet


def condor_delete_job(job, asynchronous=True):
    """Delete HTCondor job.

    :param job: The :string: HTCondor cluster ID of the job to be removed.
    :param asynchronous: Place holder for comparison to k8s.
    """

    schedd = get_schedd()
    schedd.act(htcondor.JobAction.Remove, 'ClusterID==%d' % job)

def start_watch_jobs_thread(JOB_DB):
    """Watch changes on jobs within HTCondor."""

    job_event_reader_thread = threading.Thread(target=condor_watch_jobs,
                                               args=(JOB_DB,))
    job_event_reader_thread.daemon = True
    job_event_reader_thread.start()

