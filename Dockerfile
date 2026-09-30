FROM python:3.11-slim

# ffmpeg provides both the `ffmpeg` and `ffprobe` binaries the bot shells
# out to.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot

RUN mkdir -p /app/data/tmp
VOLUME ["/app/data/tmp"]

CMD ["python", "-m", "bot.main"]
