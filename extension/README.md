# Valence — Gmail Chrome extension

A Manifest V3 extension that surfaces Valence (the email agent) inside Gmail:

- **Inbox list** — an importance badge (`urgent 92`, `high 74`, …) plus a
  topic chip and a 📅 marker for scheduling emails on every processed thread.
  Hover the badge for the justification.
- **Open email** — a floating panel (bottom-right) with the summary,
  importance reasoning, topic, no-reply flag, calendar context (suggested
  slots + your upcoming events + busy blocks) for scheduling emails, and the
  reply outline with an *Expand to full draft* button (copies to clipboard).
- **Inbox tab** — the panel's second tab lists the whole processed inbox in
  the agent's order: sort by importance, reply effort (quick first), category,
  or newest. Clicking a row opens that thread in Gmail. (Gmail's own rows
  can't be reordered, so the sorted view lives here.)
- **To-Do tab** — the derived to-do list (`pipeline/todo.py`): extracted
  action items plus a "needs your reply" marker per unreplied, non-no-reply
  email. Each row has a checkbox; checking it off calls
  `POST /api/todos/{id}/complete` and the row disappears immediately. Clicking
  the row text (not the checkbox) opens that thread in Gmail.
- **Reply effort** — quick / moderate / involved, derived from the reply
  outline's size (`drafting/effort.py`, no LLM call); shown as a chip and
  sortable in both the panel and the Valence review UI.
- **Calendar events with human approval** — when the pipeline extracts a
  meeting from a scheduling email, the panel shows the proposed event (title,
  time, attendees, location) with *Add to calendar* / *Dismiss* buttons.
  Approving is the only path that writes to Google Calendar, and it only
  fires on your click; failures show the error with a Retry. Pending
  proposals get a "📅 pending" chip in the Inbox tab.
- **Instant outlines** — opening an unread email triggers
  `POST /api/emails/{id}/refresh`, a fast single-message re-fetch that picks
  up the read flip and generates the outline in seconds, instead of waiting
  for the next bulk refresh.
- **Auto-refresh** — the extension calls `POST /api/refresh` on load and
  every 2 minutes (and on demand when you open a not-yet-processed email),
  which ingests new Gmail messages and runs the incremental pipeline. No
  manual `python -m pipeline.cli process` needed while the backend is up.
- **Ask tab** — a third panel tab, a chat interface over the in-app agent
  (`POST /api/agent/chat`). Streams its reply as it's produced (a dedicated
  `chrome.runtime.connect` port, since the fetch proxy below can't stream),
  shows which tools it used, and lists which emails its answer cites — click
  one to open that thread, same navigation as the Inbox tab's rows. The
  conversation is persisted server-side (`agent_conversation`/
  `agent_message`), so `GET /api/agent/conversations/{id}` has the full
  history even if this tab's in-memory state is lost.
- **Context section** — linked case/project chips and related emails for the
  open message, pulled from the context graph (`chunk`/`entity`/`mention`/
  `relation`, Checkpoint 0). Empty until the extraction pipeline has actually
  run over your mail; the wiring is real, not a placeholder.
- **Correct this** — the sender-priors feedback picker (segmented importance
  level + "this sender is automated/a real correspondent"), ported from the
  now-unmaintained `api/static/index.html` debug page. Same
  `POST /api/emails/{id}/feedback` endpoint, same "applies to every future
  email from this sender" behavior.

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
| `background.js` | Service worker — proxies all API fetches (avoids page CORS/CSP), plus a `chrome.runtime.connect` port transport for streaming `/api/agent/chat` |
| `content/api.js` | API client + 60s-refreshing index of processed emails by thread/message id |
| `content/ask.js` | The Ask tab's chat UI (`EmailAgentAsk`), mounted into the panel by `detail.js` |
| `content/inbox.js` | MutationObserver that stamps importance badges on list rows |
| `content/detail.js` | Floating panel (Email / Inbox / Ask tabs) for the open email; feedback picker, context chips, expand-to-draft flow |
| `content/styles.css` | All injected styles, `ea-` prefixed |
| `options.html/.js` | Backend URL setting (`chrome.storage.sync`) |
