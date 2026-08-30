# COMMANDS

Operational runbook: how to start, stop, and exercise the app locally.
For *why* it is shaped this way, see `ARCHITECTURE.md` (§15 has a condensed
version of the happy path); for the build plan see `PHASES.md`.

---

## 0. One-time setup

```bash
cd ~/Desktop/emailingagent
python3 -m venv .venv                 # .venv is Python 3.9.6 today
.venv/bin/pip install -r requirements.txt
```

OAuth consent — two separate grants, two separate token files. Each opens a
browser once:

```bash
.venv/bin/python -m ingestion.cli auth      # Gmail    -> token.json
.venv/bin/python -m calendaring.cli auth    # Calendar -> calendaring/data/calendar_token.json
```

Client secrets are auto-discovered from `client_secret_*.json` in the repo root
unless `GMAIL_CREDENTIALS_FILE` / `CALENDAR_CREDENTIALS_FILE` say otherwise.

---

## 1. Environment

**Nothing in the codebase loads `.env` automatically** — there is no
`python-dotenv` dependency and no `load_dotenv()` call. The file is read by
*you*, not by the app. Every command below assumes you have done this first in
the shell:

```bash
set -a; source .env; set +a
```

Skip it and the app silently falls back to `llm/config.py` defaults
(`LLM_PROVIDER=anthropic`, `OLLAMA_MODEL=llama3.1:8b`) — which is the usual
cause of "why is it hitting the API when I set it to local?".

Variables that matter:

| Variable | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | `ollama` for local/free |
| `OLLAMA_MODEL` | `llama3.1:8b` | `.env` currently pins `gemma2:2b` |
| `LLM_PROVIDER_AGENT` | inherits | agent loop needs tool-calling → anthropic |
| `LLM_PROVIDER_OUTLINE` | inherits | reply drafts, prose quality is visible |
| `ANTHROPIC_API_KEY` | — | required whenever a stage routes to anthropic |
| `OLLAMA_HOST` | `http://localhost:11434` | needs `ollama serve` running |
| `EMAIL_AGENT_DB` | `ingestion/data/emails.db` | SQLite path |
| `GMAIL_TOKEN_FILE` | `token.json` | Gmail OAuth token |
| `CALENDAR_TOKEN_FILE` | `calendaring/data/calendar_token.json` | Calendar OAuth token |
| `INGEST_LIMIT` / `INGEST_QUERY` | `100` / `label:inbox` | ingest defaults |
| `API_TOKEN` | unset | **unset disables API auth** (fine locally) |
| `EXTRA_ORIGINS` | empty | extra CORS origins, comma-separated |

---

## 2. Run the backend

```bash
set -a; source .env; set +a
.venv/bin/uvicorn api.main:app --reload --port 8000
```

- <http://localhost:8000/> — self-contained review UI (`api/static/index.html`,
  no build step)
- <http://localhost:8000/docs> — interactive API docs

Drop `--reload` for a long-running session; that is how the current process was
started.

## 3. Run the React frontend (optional, richer client)

```bash
./run_frontend.sh          # installs deps if needed, then vite on :5173
```

or by hand:

```bash
cd frontend && npm install && npm run dev
```

<http://localhost:5173> — Vite proxies `/api` → `localhost:8000`, so **the
backend must already be running**.

> **Two frontend directories exist.** `frontend/` is the live one — it is what
> `run_frontend.sh` launches, it has `node_modules`, and it carries the newest
> work (todo components, latest `App.tsx`). `valence-frontend/` is a
> near-identical older copy that is still tracked in git. Edit `frontend/`.

---

## 4. The data path

Ingest and processing are **separate from the server** — the API never triggers
processing (ADR-019). Run them yourself, or from cron (`deploy/cron-ingest.sh`).

```bash
# fetch mail into SQLite
.venv/bin/python -m ingestion.cli ingest                # default limit/query
.venv/bin/python -m ingestion.cli ingest -n 50 -q "label:inbox"
.venv/bin/python -m ingestion.cli show -n 10            # no network

# score / classify / summarize / draft
.venv/bin/python -m pipeline.cli process                # only what changed
.venv/bin/python -m pipeline.cli process --all          # reprocess everything
.venv/bin/python -m pipeline.cli process --dry-run      # print the plan only
.venv/bin/python -m pipeline.cli process --skip drafting
.venv/bin/python -m pipeline.cli show -n 20
.venv/bin/python -m pipeline.cli show --full <EMAIL_ID>
```

Other CLIs, same shape: `context.cli`, `calendaring.cli`, `retrieval.cli`.

```bash
.venv/bin/python -m context.cli build
.venv/bin/python -m context.cli entities      # inspect before trusting it
```

---

## 5. Tests

```bash
.venv/bin/pytest -q                      # everything
.venv/bin/pytest -q pipeline api         # a couple of packages
.venv/bin/pytest -q pipeline/tests/test_orchestrate.py
```

Each track owns a `tests/` dir: `agent api calendaring classification context
drafting feedback ingestion interface llm pipeline retrieval scoring
summarization`.

---

## 6. Ports and processes

| Port | What |
|---|---|
| 8000 | FastAPI / uvicorn — API + static UI |
| 5173 | Vite dev server (frontend) |
| 11434 | Ollama, when `LLM_PROVIDER=ollama` |

Check what is actually up before assuming:

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
ps aux | grep uvicorn | grep -v grep
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/health
```

Stop a stray server by PID:

```bash
kill <PID>
```

> **Gotcha, seen in the wild:** a `while true; do uvicorn ...; sleep 2; done`
> loop from an unrelated project sat on this machine for 11 days crash-looping
> against port 8000. If you restart the backend and get an app you do not
> recognize, or `Address already in use`, run the `lsof`/`ps` checks above and
> confirm the PID's working directory is this repo:
>
> ```bash
> lsof -a -p <PID> -d cwd
> ```

---

## 7. Deploy

Fly.io config is `fly.toml` (one volume shared by the API and the scheduled
pipeline machine); the scheduled side is `deploy/README.md` +
`deploy/cron-ingest.sh`. `API_TOKEN` and `ANTHROPIC_API_KEY` are set as
secrets, not in `fly.toml`. Nothing outside those files depends on Fly — a
plain VPS with systemd works the same way.
