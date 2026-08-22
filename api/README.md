# API (web MVP)

Thin FastAPI read layer over the `processed_email` table. **Not in the
original 8-phase plan** — added when the MVP became a web app.

## Why it's read-mostly

The workload splits in two, and keeping them apart is what makes this simple:

| | What | Cost |
|---|---|---|
| **Processing** | `python -m pipeline.cli process` | Minutes. Many LLM calls. Runs occasionally. |
| **Serving** | this API | Milliseconds. Reads rows the batch job already wrote. |

No HTTP request ever waits on the pipeline, so the API needs no long
timeouts, no job queue, and no background workers to be correct. It also
means almost any host can run it later.

## Run it

```bash
# 1. Ingest mail (once, or whenever you want fresh mail)
python -m ingestion.cli ingest

# 2. Process it (needs ANTHROPIC_API_KEY for the LLM stages)
python -m pipeline.cli process

# 3. Serve
uvicorn api.main:app --reload --port 8000
```

Then <http://localhost:8000/docs> for interactive docs.

Steps 1 and 2 are independent of step 3 — the API serves whatever has been
processed so far, including nothing.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Liveness + processed row count |
| `GET` | `/api/stats` | Counts for the header (unread, no-reply, scheduling, by level) |
| `GET` | `/api/emails` | The review list — filtered, sorted, paginated |
| `GET` | `/api/emails/{id}` | One email in full, including calendar context |
| `PATCH` | `/api/emails/{id}/outline` | Save a user-edited outline |
| `POST` | `/api/emails/{id}/expand` | Expand to full draft — **501 until implemented** |

### `GET /api/emails` parameters

`readStatus` (`read`/`unread`) · `importance` (`low`/`medium`/`high`/`urgent`) ·
`noReply` · `scheduling` · `hasOutline` · `search` (subject/sender/summary) ·
`sortBy` (`importance`/`received`/`sender`) · `descending` · `limit` · `offset`

`total` reflects the **filtered** set, not the page.

## Things the frontend should not re-implement

- **`outlineEligible`** is computed server-side. Whether an email may have a
  reply outline (read AND not no-reply) is a correctness rule; re-deriving it
  in JavaScript would be a second place for it to go wrong. Use the flag.
- **`PATCH /outline` returns 409** for an ineligible email. The gate is
  enforced server-side too, so a drifted client or a direct `curl` cannot
  attach a reply outline to an unread or no-reply email.
- **Filtering and sorting are server-side** so the CLI and the UI agree, and
  so a large inbox isn't shipped to the browser to be filtered.
- **`calendarContext` is null in the list view** and populated only on
  `GET /api/emails/{id}` — the list must not carry every email's calendar
  blob.

## Field notes

- Keys are camelCase; the internal contract (`models/schema.py`) stays
  snake_case. `api/serializers.py` is the boundary, so wire-format changes
  never touch the frozen shared contract.
- `isNoReply` and `isSchedulingRelated` can be `null`, meaning **not yet
  classified** — distinct from `false`. Filters treat them as distinct too.
- `importanceScore` is `null` until scored, and those emails always sort
  **last**, in either direction.

## Not here, on purpose

No send-email endpoint. No create-calendar-event endpoint. Both remain out of
the product until explicitly built and gated behind a user action.
