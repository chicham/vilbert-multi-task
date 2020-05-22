FROM nvcr.io/nvidia/cuda:10.1-cudnn7-devel-ubuntu18.04

ENV PYTHONDONTWRITEBYTECODE=true
ARG WORKSPACE=/workspace
ARG PROJECT_DIR=${WORKSPACE}/vilbert
COPY . ${PROJECT_DIR}
WORKDIR ${PROJECT_DIR}

RUN apt-get update --fix-missing \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y wget ca-certificates git bzip2 \
    && rm -rf /var/lib/apt/lists/* \
    && wget --quiet https://repo.continuum.io/miniconda/Miniconda3-latest-Linux-x86_64.sh -O ~/miniconda.sh \
    && /bin/bash ~/miniconda.sh -b -p /opt/conda \
    && rm ~/miniconda.sh \
    && /opt/conda/bin/conda init bash

#Add this line to load dependencies from an environment file
# Else be sure to add the dependencies of your project
RUN /opt/conda/bin/conda env update -n base conda --file ${PROJECT_DIR}/environment.yaml \
    && /opt/conda/bin/conda clean -afy \
    && find /opt/conda/ -follow -type f -name '*.a' -delete \
    && find /opt/conda/ -follow -type f -name '*.pyc' -delete \
    && find /opt/conda/ -follow -type f -name '*.js.map' -delete
