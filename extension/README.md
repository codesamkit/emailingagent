# Gmail Chrome extension

A Manifest V3 extension that surfaces the email agent's output inside Gmail:

- **Inbox list** — an importance badge (`urgent 92`, `high 74`, …) plus a
  topic chip and a 📅 marker for scheduling emails on every processed thread.
  Hover the badge for the justification.
- **Open email** — a floating panel (bottom-right) with the summary,
  importance reasoning, topic, no-reply flag, calendar context (suggested
  slots + your upcoming events + busy blocks) for scheduling emails, and the
  reply outline with an *Expand to full draft* button (copies to clipboard).
- **Auto-refresh** — the extension calls `POST /api/refresh` on load and
  every 2 minutes (and on demand when you open a not-yet-processed email),
  which ingests new Gmail messages and runs the incremental pipeline. No
  manual `python -m pipeline.cli process` needed while the backend is up.

The extension is a frontend only. The FastAPI backend stays the brain; all
requests go through the background service worker to `http://127.0.0.1:8000`
(configurable in the extension's options page).

## How it maps Gmail to the database

Gmail's web DOM exposes the same ids the Gmail API returns:

- inbox rows carry `data-legacy-thread-id` == `ProcessedEmail.thread_id`
- open messages carry `data-legacy-message-id` == `ProcessedEmail.email_id`

So no fuzzy matching — the content scripts read those attributes and look the
email up directly. If Google ever renames those attributes, only
`content/inbox.js` / `content/detail.js` selectors need updating.

## Running it

1. Start the backend (from the repo root, venv active, with the LLM env —
   this machine uses ollama):

   ```
   LLM_PROVIDER=ollama OLLAMA_MODEL=gemma2:2b uvicorn api.main:app --port 8000
   ```

   The LLM env matters: `/api/refresh` runs pipeline stages in this process.

2. Nothing else — the extension triggers ingestion + processing itself via
   `/api/refresh` (a full catch-up run can take a few minutes; a no-op check
   is ~30s of Gmail fetching).

3. Load the extension: Chrome → `chrome://extensions` → enable *Developer
   mode* → *Load unpacked* → select this `extension/` folder.

4. Open https://mail.google.com. Badges appear on processed rows within a
   second or two; open an email to see the panel.

The processed-email index re-reads every 60s and the backend pipeline
refreshes every 2 minutes, so new mail and read-status flips (which unlock
reply outlines) show up on their own.

## Files

| File | Role |
| --- | --- |
| `manifest.json` | MV3 manifest: content scripts on `mail.google.com`, local host permissions |
| `background.js` | Service worker — proxies all API fetches (avoids page CORS/CSP) |
| `content/api.js` | API client + 60s-refreshing index of processed emails by thread/message id |
| `content/inbox.js` | MutationObserver that stamps importance badges on list rows |
| `content/detail.js` | Floating panel for the open email; expand-to-draft flow |
| `content/styles.css` | All injected styles, `ea-` prefixed |
| `options.html/.js` | Backend URL setting (`chrome.storage.sync`) |
