FROM aiogram/telegram-bot-api:latest

RUN apt-get update && \
    apt-get install -y python3 python3-pip ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY bot.py .

RUN mkdir -p downloads /tmp/tg-bot-api

CMD ["python3", "bot.py"]
