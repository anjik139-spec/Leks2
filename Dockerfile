FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libcairo2 fonts-dejavu-core ffmpeg supervisor \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir \
    "aiogram>=3.13" "aiohttp>=3.10" python-docx reportlab cairosvg pillow boto3 edge-tts pypdf sentry-sdk

COPY . .

ENV DATA_DIR=/data
ENV PORT=8080
ENV WORKER_EMBEDDED=1

CMD ["supervisord", "-c", "/app/supervisord.conf"]