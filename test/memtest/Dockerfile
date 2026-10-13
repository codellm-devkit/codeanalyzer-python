# syntax=docker/dockerfile:1
# Memory/time benchmark: the local canpy on Odoo 20.0, Python 3.12.
# Build from the repo root:  docker build -f test/memtest/Dockerfile -t canpy-memtest .
# Run:  docker run --rm --env-file test/memtest/.env -v "$PWD/test/memtest/out:/out" canpy-memtest
FROM python:3.12-slim

ARG ODOO_REF=20.0
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
ENV CANPY_LEVEL=4
ENV CANPY_ARGS=-v
ENV CANPY_RAY=1
# time: peak-RSS reporting. git: clone Odoo. The -dev libs are needed for
# Odoo's sdist-only deps (psycopg2, python-ldap, lxml fallbacks).
RUN apt-get update && apt-get install -y --no-install-recommends \
        time git build-essential libpq-dev libldap2-dev libsasl2-dev \
        libxml2-dev libxslt1-dev libjpeg-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Odoo source (shallow clone)
RUN git clone --depth 1 --branch "$ODOO_REF" https://github.com/odoo/odoo.git /odoo \
    && rm -rf /odoo/.git

# Pre-build canpy's analysis venv at the path it expects
# (<cache>/<project_dir.name>/virtualenv), so a --lazy run reuses it and
# dependency installation stays out of the measurement.
RUN python -m venv /cache/odoo/virtualenv \
    && /cache/odoo/virtualenv/bin/pip install -r /odoo/requirements.txt

# canpy from the parent repo, installed in its own venv
COPY pyproject.toml README.md LICENSE /src/
COPY codeanalyzer /src/codeanalyzer
RUN python -m venv /opt/canpy && /opt/canpy/bin/pip install /src
ENV PATH=/opt/canpy/bin:$PATH

# Overridable from test/memtest/.env (docker run --env-file); extra
# `docker run` args are appended to the canpy command line.
ENV CANPY_LEVEL=4 CANPY_ARGS=""
WORKDIR /out
ENTRYPOINT ["sh", "-c", "exec /usr/bin/time -v canpy -i /odoo -o /out -c /cache -a \"$CANPY_LEVEL\" $CANPY_ARGS \"$@\"", "canpy"]
