# Valence Gmail Add-on

A contextual side panel that shows the currently-open email's Valence
review — importance, summary, no-reply flag, proposed calendar event,
editable reply outline, expand-to-draft — without leaving Gmail. It calls
the same `api/` endpoints the web UI/extension use (`api/README.md`); it
doesn't process mail itself and never sends email.

For a scheduling email, the pipeline extracts a candidate meeting
(`calendaring/propose.py`); the panel shows it and lets you **Approve**
(creates the real Google Calendar event) or **Dismiss**. Once approved, the
panel can also **rename, reschedule, or cancel** that event — each one an
explicit tap, never automatic. See `interfaces/README.md`'s "Calendar event
proposal & creation" section for what's actually happening on the Calendar
side.

This is Apps Script, not Python — a fundamentally different runtime from
the rest of the repo, so it's not wired into `pytest`/`requirements.txt`.

## One-time setup

1. **Deploy the API first** — see `../deploy/README.md`. You need its base
   URL and the `API_TOKEN` you set there.

2. **Install [clasp](https://github.com/google/clasp)** and log in:
   ```bash
   npm install -g @google/clasp
   clasp login
   ```

3. **Create the Apps Script project** from this directory:
   ```bash
   cd gmail-addon
   clasp create --type standalone --title "Valence"
   clasp push
   ```
   (`clasp create` writes a `.clasp.json` here — not committed, it's
   per-developer.)

4. **Set script properties** — open the project (`clasp open`), then
   **Project Settings > Script properties**, add:
   - `API_BASE_URL` — e.g. `https://valence-email-agent.fly.dev`
   - `API_TOKEN` — the same value you set as a Fly secret

   Also update `appsscript.json`'s `urlFetchWhitelist` (and `logoUrl`) to
   match your actual deployed domain, then `clasp push` again.

5. **Install it for yourself** — in the Apps Script editor: **Deploy > Test
   deployments > Install add-on**. This installs it only for your own
   Google account; no Marketplace listing or OAuth verification review is
   needed for personal use. The first time it runs you'll see Google's
   "unverified app" consent screen — expected for an app only you use;
   click through to grant the two scopes it asks for.

6. Open Gmail, open any processed email — the Valence panel should appear
   in the right-hand add-on rail.

## Iterating

There's no local dev server for Apps Script — the loop is `clasp push`,
then reload the add-on panel in Gmail (or **View > Show execution log** in
the editor while testing a function). `console.log` in any `.gs` file shows
up there.

## Files

| File | Purpose |
|---|---|
| `appsscript.json` | Manifest: scopes, contextual trigger, URL whitelist |
| `Card.gs` | Builds the CardService UI from the API response |
| `Api.gs` | `UrlFetchApp` calls to `GET/PATCH/POST /api/emails/...`, including the `/calendar-event/approve\|decline\|update\|cancel` routes |
| `Auth.gs` | Reads `API_BASE_URL`/`API_TOKEN` from script properties |
