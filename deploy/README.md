# Deploying the API + pipeline

A deployed API is useful if you want it reachable from somewhere other than
`localhost:8000` — not required for local use with the Chrome extension.
Since SQLite is a file, not a network service, the batch pipeline that
writes to it and the API that reads from it have to run on the same host,
sharing one disk. This uses [Fly.io](https://fly.io) as a concrete example;
swap for any host that gives you a persistent volume and lets you run a
cron job.

## One-time setup

1. **Get a Gmail/Calendar OAuth token locally, once** — `ingestion/gmail_auth.py`
   and `calendaring/calendar_auth.py` use `InstalledAppFlow`, an interactive
   browser consent flow that needs a real browser. Run
   `python -m ingestion.cli ingest` and `python -m calendaring.cli auth`
   locally exactly as today; this writes `token.json` /
   `calendar_token.json`. Refresh tokens keep working headlessly after that
   — the server never needs to do this step.

2. **Create the Fly app and volume:**
   ```bash
   fly launch --no-deploy       # picks up fly.toml
   fly volumes create valence_data --size 1
   ```

3. **Copy secrets onto the volume** (one-time; `fly ssh console` gives you a
   shell on the machine, or `fly ssh sftp shell` to copy files in):
   - the `client_secret_*.json` you downloaded from Google Cloud → `/data/client_secret.json`
   - the `token.json` / `calendar_token.json` from step 1 → `/data/token_gmail.json` / `/data/calendar_token.json`
     (paths match `fly.toml`'s `GMAIL_TOKEN_FILE`/`CALENDAR_TOKEN_FILE`)

4. **Set the actual secrets** (never in `fly.toml`, which is committed):
   ```bash
   fly secrets set API_TOKEN=$(openssl rand -hex 32)
   fly secrets set ANTHROPIC_API_KEY=sk-...
   # Local dev can stay on LLM_PROVIDER=ollama; a small cloud host generally
   # can't run Ollama well, so the deployed pipeline wants Anthropic instead.
   ```
   Save the `API_TOKEN` value — it's what you'll paste into the deployed
   Valence page's token prompt the first time you load it in a browser.
   (The Chrome extension doesn't currently send an Authorization header at
   all — `extension/background.js`'s fetch proxy assumes a local, trusted
   backend — so pointing it at a deployed, `API_TOKEN`-protected host isn't
   supported yet without adding that.)

5. **Deploy the API:**
   ```bash
   fly deploy
   ```

6. **Schedule the pipeline** — Fly doesn't have built-in cron; the simplest
   option is a [scheduled machine](https://fly.io/docs/machines/flyctl/fly-machine-run/#schedule)
   that runs `deploy/cron-ingest.sh` on an interval and exits:
   ```bash
   fly machine run . --schedule "*/15 * * * *" \
     --entrypoint "" -- bash deploy/cron-ingest.sh
   ```
   (Any other host: a plain `cron` line or `systemd` timer calling
   `deploy/cron-ingest.sh` works identically — it's a self-contained script.)

## Verifying

```bash
curl -H "Authorization: Bearer $API_TOKEN" https://<your-app>.fly.dev/api/health
```
should return `{"status": "ok", "processedEmails": N}`. Once the scheduled
machine has run at least once, `N` should match your inbox.
