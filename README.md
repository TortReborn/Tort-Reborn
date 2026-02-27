# Tort-Reborn

Discord bot for **The Aquarium [TAq]** Wynncraft guild. Built with [py-cord](https://docs.pycord.dev/) (Python).

**Contributors:** Thundderr, LordGonner
**Credit:** [badpinghere/dernal](https://github.com/badpinghere/dernal) for some map stuff

---

## External Services

| Service | What it does | Key config |
|---|---|---|
| **Wynncraft API (v3)** | Player stats, guild info, online status | `WYNN_TOKEN` |
| **PostgreSQL (Neon)** | Primary database — player data, applications, guild state | `DB_HOST`, `DB_LOGIN`, etc. |
| **Supabase S3** | Image storage — profile backgrounds, cached avatars, shell exchange assets | `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, etc. |
| **OpenAI** | AI-powered application parsing — completeness checks, IGN extraction | `OPENAI_API_KEY` |
| **Google Sheets** (Apps Script) | Recruitment tracking spreadsheet | `SHEETS_SCRIPT_URL` |
| **Discord Webhooks** | Posting application embeds and shell exchange updates | `LEGACY_WEBHOOK_URL` |
| **Flask + Waitress** | Web server receiving guild website application form submissions | Runs alongside the bot |
| **Railway** | Cloud hosting — runs the bot as a worker service | Shared variables (see below) |

All secrets live in `.env` — copy `.env.example` and fill in values (ask a contributor).

---

## Project Structure

```
Tort-Reborn/
├── main.py              # Bot entry point — loads cogs, starts the bot
├── webhook.py           # Flask server for website application submissions
│
├── Commands/            # Slash commands (~30 modules)
├── UserCommands/        # Right-click context menu commands
├── Events/              # Event listeners (on_message, member updates, etc.)
├── Tasks/               # Scheduled background tasks (activity sync, app checks, etc.)
│
├── Helpers/
│   ├── functions.py     # Wynncraft API calls, shared utilities
│   ├── database.py      # PostgreSQL connection + query helpers
│   ├── storage.py       # S3/Supabase image storage
│   ├── openai_helper.py # OpenAI integration for application analysis
│   ├── sheets.py        # Google Sheets API wrapper
│   ├── variables.py     # Guild IDs, channel IDs, role IDs, constants
│   └── ...              # Image generation, profile rendering, etc.
│
├── images/              # Static assets (banners, mythics, shell icons)
├── Archive/             # Deprecated code (kept for reference)
└── requirements.txt     # Python dependencies
```

### How things connect

**Commands/** — Each file is a py-cord Cog loaded by `main.py`. Slash commands for player info, guild management, visuals, recruitment, etc.

**Events/** — React to Discord events: detecting new applications in chat, member role changes, reactions, channel updates.

**Tasks/** — Background loops that run on intervals:
- Sync member activity from Wynncraft API (every 3 min)
- Monitor application status and auto-close stale apps
- Track territory changes
- Assign vanity roles daily based on war/raid participation

**Helpers/** — Shared logic imported by commands, events, and tasks. This is where all external service clients live.

**webhook.py** — Standalone Flask server. Receives `POST /application` from the guild website, validates the player, and forwards the application into Discord.

---

## Getting Started

```bash
# Install dependencies
pip install -r requirements.txt

# Set up your .env file (get values from a contributor)

# Run the bot
python main.py

# The Flask webhook server starts automatically alongside the bot
```

Set `TEST_MODE=true` in `.env` to use test database/tokens during development.

---

## Railway Deployment

The bot runs on [Railway](https://railway.app/) as a **worker** service (no public port needed).

### Setting up environment variables

Railway uses **shared variable groups** to manage secrets. Variables defined in a shared group are **not** automatically available to services — you must link them.

1. Go to your Railway project dashboard
2. Click **Variables** in the top nav (project-level) or create a shared variable group
3. Add all required env vars (`TOKEN`, `TEST_MODE`, `DB_HOST`, etc.)
4. Go to your **worker** service → **Variables** tab
5. Click **Add Variable Reference** (or **Insert Reference**) and select the shared variable group
6. The variables will now appear in the service's Variables tab — confirm they show without warning icons

### Notes

- The Python version is pinned in `.python-version` (currently 3.12). Do not use Python 3.13 — `py-cord` depends on `audioop` which was removed in 3.13.
- `TEST_MODE` defaults to production if unset. Set `TEST_MODE=true` only for test deployments.
- Railway auto-deploys from the branch connected in your service settings. Push to that branch to trigger a deploy.
