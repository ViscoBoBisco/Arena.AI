FROM python:3.11-slim
WORKDIR /app
COPY unified/ ./unified/
COPY cli.py ./
RUN mkdir -p /data && chown 10001:10001 /data
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/readyz', timeout=3)"
EXPOSE 8080
CMD ["python3", "-m", "unified.server", "--host", "0.0.0.0", "--port", "8080", "--data", "/data"]
