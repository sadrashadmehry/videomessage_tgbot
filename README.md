# Round Video Message Bot

A Telegram bot that turns any video or GIF you send it into a native round
**video message** ("video note") — the circular bubble you normally get
from the camera icon next to the message box.

You pick which part of your video ends up inside the circle (and,
optionally, which time range of a longer clip to use) using inline
buttons and a short text reply, then the bot renders it and sends it back
as a proper `video_note`.

For *why* it's built this way — including why the bot doesn't need to paint
per-pixel transparency to get the round look — see
[`ARCHITECTURE.md`](ARCHITECTURE.md).

## How it works (user's-eye view)

1. Send the bot a video or a GIF.
2. It replies with a still preview of your video with a circle drawn on
   it, and a set of buttons underneath.
3. Use ⬅️⬆️➡️⬇️ to move the circle and 🔍−/🔍+ to zoom out/in until it
   frames what you want.
4. Only want part of a longer clip? Tap ✂️ **Trim** and reply with the
   range you want to keep — `5-12` or `0:05-0:20` (seconds or mm:ss), or
   just a start time (e.g. `5`) to keep the next 60s from there.
5. Tap ✅ **Confirm**. A few seconds later you get a round video message
   back, cropped and trimmed exactly as previewed.
6. Send another video/GIF any time, or /cancel to abort the current one.

## Prerequisites

- Python 3.11+
- [ffmpeg](https://ffmpeg.org/) (provides `ffmpeg` and `ffprobe`) on your
  `PATH` — or use the provided Docker image, which bundles it.
- A bot token from [@BotFather](https://t.me/BotFather) (`/newbot`).

## Setup

```bash
git clone <this-repo>
cd telegram-round-video-bot
cp .env.example .env
# edit .env and paste your BOT_TOKEN
```

### Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m bot.main
```

### Run with Docker

```bash
docker compose up --build
```

## Configuration

All settings are environment variables — see [`.env.example`](.env.example)
for the full list with defaults and explanations (output size, max
duration, download size limit, temp dir, log level, ffmpeg binary paths).

## Handling files larger than 20MB

The public Telegram Bot API (`api.telegram.org`) will not let a bot
*download* a file bigger than 20MB, no matter how the bot itself is
configured — this is a Telegram-side limit on the cloud Bot API, not
something in this code. If you need to handle bigger source videos:

1. Run your own [local Bot API server](https://github.com/tdlib/telegram-bot-api)
   (Telegram publishes an official one), which raises the download limit
   entirely and the upload limit to 2000MB.
2. Point this bot at it by setting the `base_url` used to construct the
   `Bot` instance in `bot/main.py` to your local server's address, per
   [aiogram's local server docs](https://docs.aiogram.dev/).
3. Raise `MAX_DOWNLOAD_SIZE_MB` accordingly.

This is called out explicitly because it trips people up: it's a Telegram
platform limit, not a bug in this bot.

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```

The tests cover the pure trim/crop math (`bot/services/crop_state.py`,
`bot/services/time_range.py`) — the parts of the codebase most worth unit
testing since everything else is mostly I/O glue around `ffmpeg`/`ffprobe`.
See `ARCHITECTURE.md` for how the ffmpeg pipeline itself (including
combined crop+trim rendering, and GIF input) was validated end-to-end
during development.

## Project layout

```
bot/
├── main.py                 # entry point / wiring
├── config.py                # env-driven settings
├── states.py                 # aiogram FSM states
├── handlers/
│   ├── start.py               # /start, /help
│   ├── video_intake.py        # receive video/GIF -> probe -> first preview
│   ├── crop_selector.py       # button callbacks + trim text input -> render -> send
│   ├── common.py               # /cancel, stray-text fallback
│   └── errors.py                # global error handler
├── services/
│   ├── crop_state.py           # pure crop-box math (unit tested)
│   ├── time_range.py           # trim timestamp parsing/formatting (unit tested)
│   ├── ffmpeg_service.py       # ffprobe/ffmpeg subprocess wrappers
│   └── preview_service.py      # Pillow preview-image rendering
├── keyboards/
│   └── crop_keyboard.py        # inline keyboard layouts
└── utils/
    ├── tempfiles.py             # per-session temp dirs + cleanup
    ├── session.py                # shared "cancel/clear session" helper
    └── validators.py            # MIME/size checks (video + GIF)
```

Full architecture, data flow, and design-decision rationale:
**[`ARCHITECTURE.md`](ARCHITECTURE.md)**.

## License

MIT — see [`LICENSE`](LICENSE).
