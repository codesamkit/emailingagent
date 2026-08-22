## Project Overview
This repo is building an AI email agent (Gmail + Google Calendar integration) that ranks emails by importance, summarizes them, and generates reply outlines — gated by read status and no-reply detection, calendar-aware for scheduling emails. Full behavior spec and phased build plan: `PHASES.md`. Condensed working summary (schema, track ownership, phase table, design rationale): `CONTEXT.md`. Proposed target directory layout: `FILE-TREE.md`.

Work is split into 3 parallel tracks (Ingestion & Calendar / Classification & Intelligence / Drafting & Orchestration-UI), each owning its own folders, sharing one frozen `models/schema.py` + `interfaces/README.md` contract from Phase 0 onward. See `CONTEXT.md` for the full ownership map before touching any track's files.

## Development Practices
Always identify and use software development best practices (like DRY) for any code changes. Avoid redundant code where possible. Build reusable components where feasible.

## Planning approval
Before implementing any change — new features, refactors touching multiple files, schema changes, new agents, or anything that would take more than a few edits — present a plan and wait for explicit approval. Do not start coding until the user confirms. What counts as "large": adding a new route + service + model, changing shared infrastructure (event bus, agent registry, database schema), or any task that touches more than ~3 files in a meaningful way. 

## Fix quality
When recommending fixes, check if this is the right fix for the solution or just a mask on the real problem, a band-aid. If it is not the right fix, don't implement it, and clearly state that the right fix as well as the band-aid, so that the I can make a choice.

### Avoid redundancy
Before implementing any new functionality, check if it does not exist already, and if so, reuse existing code. 

## Testing
Give clear instructions to the user about how they can validate the change.