FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock /app/
COPY src /app/src

RUN python -m pip install --no-cache-dir uv \
    && uv sync --frozen --no-dev --extra prf-sidecar

EXPOSE 8741

CMD ["uv", "run", "--frozen", "--no-dev", "--extra", "prf-sidecar", "seektalent-prf-sidecar"]
