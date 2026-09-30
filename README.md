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

### Cloudflare first, XHTTP fallback

The bot can send API requests, previews, video uploads, and file downloads
through your own authenticated Cloudflare Worker. On Cloudflare error 1027
(Workers Free daily request quota), it switches to the XHTTP proxy until
midnight UTC, then tries the Worker again. Telegram's own JSON 429 responses
keep their normal retry behavior; switching proxies cannot bypass those limits.
Worker network failures use the proxy for 60 seconds. An ambiguous failed
send/upload is not automatically replayed, to avoid sending duplicate messages.
An interrupted download is not restarted after bytes have been written.

1. Deploy `worker/index.mjs` with `worker/wrangler.toml` using Cloudflare's
   dashboard or Wrangler. Set Worker secrets `BOT_TOKEN` (the same bot token)
   and `RELAY_SECRET` (a long random secret). The relay only forwards to Telegram;
   it never accepts an arbitrary upstream URL. Keep Worker request logging off.
2. In your private `.env`, set `WORKER_URL=https://your-worker.workers.dev`
   and `WORKER_SECRET` to that same relay secret.
3. Save your VLESS link as `secrets/xhttp.txt`, then run:

   ```bash
   python scripts/configure_xray.py secrets/xhttp.txt
   docker compose -p telegram-bot-v2 -f docker-compose.yml -f compose.proxy.yml up -d --build
   ```

The supplied converter preserves VLESS encryption, XHTTP host/path/mode, and
padding. It supports the encrypted, non-TLS XHTTP configuration used here.
Xray's SOCKS port is only on the project's Docker network; no host port is
published. Other applications and the server's default route are unaffected.
Both downloads and uploads consume proxy traffic whenever fallback is active.
Leave `WORKER_URL` blank for proxy-only operation. Without either a Worker
or `FALLBACK_PROXY_URL`, the bot uses direct Telegram access.

The free Worker quota is account-wide (100,000 requests/day), not a GB allowance.
Other Workers share it. This application reacts to Cloudflare's enforced quota;
it does not impose a billing cap on paid Workers plans. See
[Cloudflare limits](https://developers.cloudflare.com/workers/platform/limits/).
After a process restart, one Worker request may probe an already-exhausted quota.

For servers whose network blocks the normal package registries, use the supplied
`Dockerfile.server`. An untracked `docker-compose.override.yml` can select it.
When using explicit `-f` flags, include the override explicitly as the last file.
The optional `xray/Dockerfile` builds from a checksum-verified official Xray
Linux x64 binary saved as `xray/xray`, for offline installation of the supplied
non-TLS transport. Credentials and binaries are ignored by Git.

Run transport and Worker checks with:

```bash
python -m pytest -q
node --test worker/worker.test.mjs
```

## Configuration

The result has one **Send as GIF** action. It sends a silent animation with
the video-note image inside a circular matte, which Telegram users can save
to their GIFs. Telegram still displays the animation in a rectangular media
frame; it cannot use the native video-note circle or transparency. The matte
matches a dark chat theme and may be visible in other themes.

## Inspecting users and media

Version 3 keeps user activity and job metadata in `data/bot.sqlite3`. The
Docker deployment mounts the entire `data` directory, so records survive
container replacement. Recording starts when this version is deployed; older
bot activity is not reconstructed.

On the server, from `/opt/telegram-bot-v2`, use the running bot container:

```bash
docker exec telegram-bot-v2-bot-1 python -m bot.inspect users
docker exec telegram-bot-v2-bot-1 python -m bot.inspect jobs
docker exec telegram-bot-v2-bot-1 python -m bot.inspect jobs --user-id 123456789
docker exec telegram-bot-v2-bot-1 python -m bot.inspect download 1 source
docker exec telegram-bot-v2-bot-1 python -m bot.inspect download 1 video_note
docker exec telegram-bot-v2-bot-1 python -m bot.inspect download 1 animation
```

Replace `1` with a job ID from the jobs list. Downloaded files appear in
`/opt/telegram-bot-v2/data/exports/` with a `.bin` extension; they are usually
video files and can be opened with a video player or renamed to `.mp4`. The
database stores Telegram `file_id` references, not the video bytes, and a
source file over Telegram's 20 MB bot download limit cannot be retrieved this
way. The database also contains user names, prompts, and media references;
keep it private.

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
