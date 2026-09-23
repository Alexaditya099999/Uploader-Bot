FROM aiogram/telegram-bot-api:latest AS tgapi
FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    ffmpeg libssl3 zlib1g libstdc++6 libatomic1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=tgapi /usr/local/bin/telegram-bot-api /usr/local/bin/telegram-bot-api
RUN chmod +x /usr/local/bin/telegram-bot-api

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py .
RUN mkdir -p downloads /tmp/tg-bot-api

CMD ["python", "bot.py"]
