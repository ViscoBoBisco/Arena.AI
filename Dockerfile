FROM python:3.11-slim
WORKDIR /app
COPY unified/ ./unified/
COPY cli.py ./
RUN mkdir -p /data
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python3", "-m", "unified.server", "--host", "0.0.0.0", "--port", "8080", "--data", "/data"]
