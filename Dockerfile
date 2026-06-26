FROM python:3.12-slim

RUN pip install --no-cache-dir tapo prometheus-client

COPY tapo-exporter.py /app/tapo-exporter.py

EXPOSE 9801
CMD ["python3", "/app/tapo-exporter.py"]
