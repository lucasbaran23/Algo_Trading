# Commodities terminal: one container serves the API, the websocket stream and the frontend.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DATA_MODE=live AUTH_MODE=on \
    CACHE_DIR=/data/cache DATABASE_PATH=/data/app.db

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend backend
COPY frontend frontend

# Users, workspaces and the data cache live on a mounted volume so they survive deploys.
VOLUME ["/data"]
EXPOSE 8000
WORKDIR /app/backend
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
