FROM python:3.12-slim

WORKDIR /app

# Pinned for reproducible builds.
RUN pip install --no-cache-dir tapo==0.8.7 prometheus-client==0.21.1

COPY tapo-exporter.py /app/tapo-exporter.py

# Run as a non-root user.
RUN useradd -r -u 10001 exporter
USER 10001

EXPOSE 9801
CMD ["python3", "/app/tapo-exporter.py"]
