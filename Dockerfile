# Replay/API 镜像默认不安装浏览器和模型 SDK，构建稳定且不需要密钥。
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SIGNAL_RADAR_MODE=replay

WORKDIR /app
COPY pyproject.toml README.md ./
COPY signal_radar ./signal_radar
COPY fixtures ./fixtures

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ".[api]"

EXPOSE 8000
CMD ["uvicorn", "signal_radar.api:app", "--host", "0.0.0.0", "--port", "8000"]

