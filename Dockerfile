# This file is part of REANA.
# Copyright (C) 2017, 2018, 2019 CERN.
#
# REANA is free software; you can redistribute it and/or modify it
# under the terms of the MIT License; see LICENSE file for more details.

FROM python:3.6-slim

ENV TERM=xterm

# Hardcoded, this needs to be replaced
# by setting these attributes  
# in the kubernetes cluster configuration
ENV VC3USERID=1002
ENV HTCONDOR_ADDR="<128.135.158.176:9618?addrs=128.135.158.176-9618+[--1]-9618&noUDP&sock=954400_984a_3>"

RUN useradd -u $VC3USERID vc3user

RUN apt-get update && \
    apt-get install -y vim-tiny && \
    pip install --upgrade pip && \
    pip install htcondor==8.9.1 retrying

COPY CHANGES.rst README.rst setup.py /code/
COPY reana_job_controller/version.py /code/reana_job_controller/
COPY reana_job_controller/htcondor_submit.py /code/htcondor_submit.py
WORKDIR /code
RUN pip install requirements-builder && \
    requirements-builder -e all -l pypi setup.py | pip install -r /dev/stdin && \
    pip uninstall -y requirements-builder

COPY . /code

# Debug off by default
ARG DEBUG=false
RUN if [ "${DEBUG}" = "true" ]; then pip install -r requirements-dev.txt; pip install -e .; else pip install .; fi;

# Building with locally-checked-out shared modules?
RUN if test -e modules/reana-commons; then pip install modules/reana-commons --upgrade; fi
RUN if test -e modules/reana-db; then pip install modules/reana-db --upgrade; fi

EXPOSE 5000

ENV FLASK_APP reana_job_controller/app.py

CMD ["flask", "run", "-h", "0.0.0.0"]
