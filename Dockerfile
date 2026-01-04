FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    HF_HOME=/opt/hf-cache \
    TRANSFORMERS_CACHE=/opt/hf-cache

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        ffmpeg \
        git \
        ca-certificates && \
    apt-get install -y --only-upgrade \
        openssl \
        libssl3 \
        libsqlite3-0 \
        libkrb5-3 \
        libgssapi-krb5-2 \
        krb5-locales && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/hf-cache

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir \
        "pip==25.3" \
        "setuptools==78.1.1" \
        "wheel==0.43.0" \
        "filelock==3.20.1"

# CUDA 12.4 wheels for torch/torchaudio (CVE-2025-32434 mitigation via torch >= 2.6)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124 \
    && pip install --no-cache-dir \
        numpy==1.26.4 \
        pandas==2.2.3 \
        transformers==4.30.2 \
        tqdm==4.66.5 \
        nltk==3.9.1 \
        soundfile \
        librosa \
        scipy \
        pyarrow \
        ctranslate2==4.5.0 \
        faster-whisper==1.1.0 \
        pyannote.audio==3.3.1 \
        pyannote.pipeline==3.0.1 \
    && pip install --no-cache-dir --no-deps git+https://github.com/m-bain/whisperx.git hf-transfer


VOLUME ["/opt/hf-cache", "/data"]

WORKDIR /data

ENTRYPOINT ["whisperx"]