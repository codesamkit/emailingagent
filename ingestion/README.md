# Ingestion (Track A — Phase 1)

Read-only Gmail ingestion: fetch the N most recent messages, normalize them, and
store them in the `raw_email` SQLite table for Tracks B and C.

**Scope is `gmail.readonly` only.** This module cannot send, modify, label, or
delete anything.

---

## 1. One-time Google Cloud setup

You only do this once, for **rsbalusani@gmail.com**. Nothing here is committed —
`credentials.json` and `token.json` are both gitignored.

1. **Create a project** — <https://console.cloud.google.com/projectcreate>
   Name it e.g. `email-agent`. Make sure the account picker at the top right
   says `rsbalusani@gmail.com` before you start.

2. **Enable the Gmail API** — <https://console.cloud.google.com/apis/library/gmail.googleapis.com>
   Select your new project, click **Enable**.

3. **Configure the OAuth consent screen** — APIs & Services → OAuth consent screen
   - User type: **External** (personal Gmail accounts can't use Internal)
   - App name: anything (`Email Agent`), user support email: your address
   - Scopes: you can skip adding scopes here; the app requests them at runtime
   - **Test users: add `rsbalusani@gmail.com`** ← easy to miss, and consent fails
     without it
   - Leave the app in **Testing** status. Do not publish it.

4. **Create the OAuth client** — APIs & Services → Credentials →
   **Create Credentials** → **OAuth client ID**
   - Application type: **Desktop app** ← must be Desktop, not Web
   - Click **Download JSON** on the created client

5. **Drop the file in** — put the downloaded JSON at the repo root. No renaming
   needed: the config accepts Google's own `client_secret_<client-id>.json`
   filename as well as a renamed `credentials.json`, and both are gitignored.
   (Or put it anywhere and set `GMAIL_CREDENTIALS_FILE=/path/to/it`.)

> Don't paste the contents of `credentials.json` into a chat, a commit, or an
> issue. Leaving it as a file on disk is all that's needed — it's gitignored.

### About the "Google hasn't verified this app" screen

Expected for a Testing-status app. Click **Advanced** → **Go to Email Agent
(unsafe)**. It's your own app requesting read access to your own mailbox.

---

## 2. Install

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r ingestion/requirements.txt
```

Python 3.9+ (the repo currently runs on macOS system Python 3.9.6; the Google
libraries warn that 3.9 is end-of-life but work correctly).

---

## 3. Run

```bash
# One-time consent — opens a browser, then writes token.json
.venv/bin/python -m ingestion.cli auth

# Fetch and store the 100 most recent inbox messages
.venv/bin/python -m ingestion.cli ingest

# Read back what's stored (no network)
.venv/bin/python -m ingestion.cli show --limit 10
.venv/bin/python -m ingestion.cli show --full <email_id>
```

Useful flags:

| Flag | Meaning |
|---|---|
| `-n / --limit N` | how many messages to fetch (default 100) |
| `-q / --query Q` | Gmail search query (default `label:inbox`) |
| `--db PATH` | alternate SQLite file |
| `-v / --verbose` | show retry/backoff logging |
| `--non-interactive` | fail instead of opening a browser when no token exists |

Every default is also settable by environment variable: `INGEST_LIMIT`,
`INGEST_QUERY`, `EMAIL_AGENT_DB`, `GMAIL_CREDENTIALS_FILE`, `GMAIL_TOKEN_FILE`.

---

## 4. Tests

```bash
.venv/bin/python -m pytest ingestion/tests -q
```

Fully offline — no credentials or network required. A fake Gmail service in
`tests/test_fetch.py` covers the list → get → parse → store path end to end,
including pagination.

---

## What gets stored

`raw_email`, keyed on the Gmail message id:

| Column | Notes |
|---|---|
| `email_id`, `thread_id` | Gmail ids |
| `sender`, `subject` | RFC 2047 decoded (`=?UTF-8?B?…?=` → real text) |
| `body_text` | plain-text part preferred; HTML safely stripped as fallback |
| `snippet` | Gmail's own preview string |
| `received_at` | ISO-8601 **UTC**, from `internalDate` — not the spoofable `Date:` header |
| `read_status` | `read` / `unread`, from the `UNREAD` label |
| `label_ids` | JSON array |
| `headers` | JSON object — includes `List-Unsubscribe`, `Precedence`, `Auto-Submitted`, `To`, `Cc`, `Reply-To` verbatim for Track B |
| `has_attachments` | 0/1 |
| `fetched_at` | when this row was last written |

Re-running `ingest` **upserts**: no duplicates, and `read_status` refreshes to
match Gmail. That last part is what lets Track C regenerate a reply outline when
an email flips from unread to read.

---

## Notes for other tracks

- `RawEmail` currently lives in `ingestion/models.py` because Phase 0 hasn't run
  and `models/schema.py` doesn't exist yet. When the shared schema is frozen,
  this module becomes a one-line re-export — **don't** copy the dataclass.
- Messages that 404 mid-run (deleted between list and fetch) or fail to parse are
  logged and skipped, never fatal.
- Attachment-only and bodyless messages store `body_text = ""` rather than
  raising. Richer handling of those is Phase 8's job.
