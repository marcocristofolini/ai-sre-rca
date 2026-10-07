FROM python:3.14-alpine@sha256:9e9fde4d32eedce0b661d9ab91e826b62dddf28e928c230ec55f1866cac66b01
RUN pip install --no-cache-dir \
    opentelemetry-sdk==1.37.0 \
    opentelemetry-exporter-otlp-proto-http==1.37.0
WORKDIR /app
COPY app.py /app/app.py
COPY redis_scenario.py /app/redis_scenario.py
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["python", "/app/app.py"]
