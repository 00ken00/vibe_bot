FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV PYTHONPATH=/workspace/vibe_bot/src

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        less \
        openssh-client \
        procps \
        tini \
        vim \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-dev.lock /tmp/requirements-dev.lock

RUN sed '/^-e file:\\.$/d' /tmp/requirements-dev.lock > /tmp/requirements-docker.lock \
    && python3 -m pip install --upgrade pip \
    && python3 -m pip install -r /tmp/requirements-docker.lock \
    && rm -f /tmp/requirements-dev.lock /tmp/requirements-docker.lock

WORKDIR /workspace/vibe_bot

EXPOSE 8888 8765 8766

ENTRYPOINT ["tini", "--"]
CMD ["jupyter", "lab", "--ip=0.0.0.0", "--port=8888", "--no-browser", "--allow-root"]
