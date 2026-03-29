FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY nebula_copilot ./nebula_copilot
COPY scripts ./scripts

RUN python -m pip install --upgrade pip \
    && python -m pip install .

RUN useradd -m -u 10001 appuser
USER appuser

ENTRYPOINT ["python", "-m", "nebula_copilot.cli"]
CMD ["--help"]
