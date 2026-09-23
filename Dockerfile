FROM aiogram/telegram-bot-api:latest
USER root
ENTRYPOINT []
RUN apk add --no-cache python3 py3-pip ffmpeg ca-certificates curl && rm -rf /var/cache/apk/*
WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt
COPY bot.py .
RUN mkdir -p downloads thumbs /tmp/tg-bot-api
CMD ["python3", "bot.py"]
