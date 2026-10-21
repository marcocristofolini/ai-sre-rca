# syntax=docker/dockerfile:1
FROM python:3.14-slim
WORKDIR /app
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install .
COPY app ./app
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --no-deps .
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
