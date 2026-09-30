# Replay/API 镜像默认不安装浏览器和模型 SDK，构建稳定且不需要密钥。
FROM node:22-slim AS frontend-build

WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend ./
RUN npm run build

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SIGNAL_RADAR_MODE=replay

WORKDIR /app
COPY pyproject.toml README.md ./
COPY signal_radar ./signal_radar
COPY fixtures ./fixtures
COPY --from=frontend-build /frontend/dist ./frontend/dist

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ".[api]"

EXPOSE 8000
CMD ["uvicorn", "signal_radar.api:app", "--host", "0.0.0.0", "--port", "8000"]

