FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system f117 && useradd --system --gid f117 --home-dir /app f117

COPY --chown=f117:f117 pyproject.toml README.md alembic.ini ./
COPY --chown=f117:f117 alembic ./alembic
COPY --chown=f117:f117 config ./config
COPY --chown=f117:f117 f117 ./f117

RUN python -m pip install --no-cache-dir .

USER f117

CMD ["radar", "scheduler"]
