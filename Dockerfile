FROM python:3.11-slim

# Устанавливаем chromium + chromedriver из apt (версии всегда совпадают)
RUN apt-get update && apt-get install -y \
    chromium chromium-driver \
    fonts-liberation libatk-bridge2.0-0 libatk1.0-0 libnspr4 libnss3 \
    libx11-6 libxcb1 libxcomposite1 libxdamage1 libxext6 libxfixes3 \
    libxrandr2 libxss1 libxtst6 ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV SUBSCRIBERS_FILE=/data/subscribers.json
# Указываем путь к chromium для selenium
ENV CHROME_BIN=/usr/bin/chromium

CMD ["python", "nano.py"]
