# Copyright (C) 2024 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

From ubuntu:22.04
ARG DEBIAN_FRONTEND=noninteractive

ENV GLIBC_TUNABLES glibc.cpu.x86_shstk=permissive

COPY comps /root/comps
COPY neural_speed-0.1.dev97+gfdae9d6.d20240806-cp310-cp310-linux_x86_64.whl /root/
COPY bert-q8j-g-1-cint8.bin /root/
#COPY bce-embedding-base_v1 /root/
COPY requirements-neural-speed.txt /root/

RUN apt update && apt install -y python3 python3-pip 
# RUN pip3 install torch==2.2.2 torchvision --index-url https://download.pytorch.org/whl/cpu 
# RUN pip3 install intel-extension-for-pytorch==2.2.0
#RUN pip3 install transformers
RUN pip3 install -r /root/requirements-neural-speed.txt
RUN pip3 install llmspec mosec msgspec httpx
RUN pip3 install /root/neural_speed-0.1.dev97+gfdae9d6.d20240806-cp310-cp310-linux_x86_64.whl

RUN cd /root/ && export HF_ENDPOINT=https://hf-mirror.com && huggingface-cli download --resume-download BAAI/bge-large-zh-v1.5 --local-dir /root/bge-large-zh-v1.5

ENV EMB_MODEL="/root/bge-large-zh-v1.5/"

WORKDIR /root/comps/embeddings/langchain-mosec/mosec-docker

CMD ["python3", "server-ipex.py"]
