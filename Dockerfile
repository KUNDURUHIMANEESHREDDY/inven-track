# Build browser-side Qwen from pinned source dependencies. Do not commit generated bundles.
FROM node:22-bookworm-slim AS web-assets
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY frontend/qwen_worker.js ./frontend/qwen_worker.js
RUN npm run build:qwen

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY frontend ./frontend
COPY --from=web-assets /web/frontend/qwen_worker.bundle.js ./frontend/qwen_worker.bundle.js
RUN useradd --uid 10001 --create-home inven && mkdir -p /data /backups && chown -R 10001:10001 /data /backups /app
USER 10001
EXPOSE 8000
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
