FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_HOME=/app \
    KICOMPORT_PORT=8000

WORKDIR ${APP_HOME}

RUN apt-get update && apt-get install -y --no-install-recommends unzip && rm -rf /var/lib/apt/lists/*

COPY v1/backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY v1/backend ./backend
COPY v1/frontend ./frontend
COPY v1/config ./config

EXPOSE 8000

CMD ["/bin/sh", "-c", "exec uvicorn backend.main:app --host 0.0.0.0 --port ${KICOMPORT_PORT:-8000}"]
