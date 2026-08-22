# Gmail Chrome extension

A Manifest V3 extension that surfaces the email agent's output inside Gmail:

- **Inbox list** — an importance badge (`urgent 92`, `high 74`, …) on every
  thread the pipeline has processed. Hover the badge for the justification.
- **Open email** — a floating panel (bottom-right) with the summary,
  importance reasoning, no-reply flag, suggested calendar slots for
  scheduling emails, and the reply outline with an *Expand to full draft*
  button (copies to clipboard).

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

1. Start the backend (from the repo root, venv active):

   ```
   uvicorn api.main:app
   ```

2. Make sure the pipeline has run at least once so the database has
   processed emails (see the repo README).

3. Load the extension: Chrome → `chrome://extensions` → enable *Developer
   mode* → *Load unpacked* → select this `extension/` folder.

4. Open https://mail.google.com. Badges appear on processed rows within a
   second or two; open an email to see the panel.

The processed-email index refreshes every 60s automatically. After re-running
the pipeline, badges and panels update on the next refresh (or reload the
Gmail tab).

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
