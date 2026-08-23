# Meeting Notes — StrideCore Simulation Corpus

Ground-truth meeting notes generated alongside the 225-email simulation corpus
delivered to the Valence test inbox (Act 1: 160 emails, Act 2: 65 emails).

## Purpose

These are **not** part of the email corpus. They are the underlying record of
what happened, so you can grade the agent's output against something.

Concretely, they let you check:

- **Summarization** — does the agent's 1-3 sentence summary of a thread match
  what actually got decided?
- **Importance ranking** — the notes mark which decisions were load-bearing.
  Emails attached to a load-bearing decision should rank high.
- **Reply outlines** — the notes record what the inbox owner (Ronith) actually
  owed people. A good outline should converge on that.
- **Scheduling detection** — every meeting here has a corresponding invite,
  reschedule, or availability request somewhere in the mail.

## The world

**StrideCore Technologies** — Portland OR, ~180 people, Series B. Sells SC-series
smart insole modules (32-zone pressure array + IMU + BLE 5.3) B2B to footwear
manufacturers. Three segments: athletic, industrial safety, orthopedic/medical.

**Inbox owner:** Ronith Balusani, Head of Product & Program Management.
Reports to Dana Whitfield (COO). Owns SC-400 sustaining and the SC-500 program.

Full cast and account list: see `../CORPUS-WORLD.md`.

## Act 2 (September)

Act 1 is discovery. Act 2 is consequences, and three new pressures:

6. **The freeze slips again** — the soak finds FW-2278, an OTA ordering bug
   present since 2024 that the timeout fix made reachable. The Hongli September
   build slot is lost; next is October 12. Grant's August warning lands exactly
   as he described it.
7. **Hector Villalobos resigns** — the support engineer who found three defects,
   because corrective actions have no owner. His exit document ends up requested
   by the board.
8. **A journalist has the story** — accurately, with a pre-publication factual
   review offer and a Sept 12 deadline.

Resolutions: Bastion closes at half, Vantera's underwriters accept the corrected
data, Kaiho's design review passes, Hongli T2 conforms, Rev C ships.

## The five storylines everything hangs off

1. **SC-500 firmware freeze** — Aug 7 target slips. Regression-scope argument
   between Sasha (firmware) and Nadia (QA). Resolves to Aug 14, scoped.
2. **Anshun Lot 22B sensor drift** — supplier adhesive change causes progressive
   baseline drift. 4,112 units affected, 3,720 in the field. The spine of the
   corpus; touches Vantera, Meridian, Bastion, Kaiho, and the board.
3. **Meridian Rev C / raw data** — Oct 1 qualification date plus a request to
   expose per-step raw pressure data.
4. **Bastion pilot → 5,000 unit PO** — contingent on a Sept 9 COO review.
5. **Q4 platform decision** — SDK vs. analytics dashboard. Ronith's call.

## Files

| File | Date | Type |
|---|---|---|
| `2026-07-16-sc500-program-review.md` | Jul 16 | Internal recurring |
| `2026-07-30-q3-roadmap-working-session.md` | Jul 30 | Internal working |
| `2026-08-06-sc500-program-review.md` | Aug 6 | Internal recurring |
| `2026-08-13-supplier-quality-review.md` | Aug 13 | Internal |
| `2026-08-19-lot22b-incident-standup.md` | Aug 19 | Incident |
| `2026-08-20-sc500-program-review.md` | Aug 20 | Internal recurring |
| `2026-08-21-anshun-commercial-call.md` | Aug 21 | External supplier |
| `2026-08-25-hongli-technical-call.md` | Aug 25 | External supplier |
| `2026-08-26-vantera-lot22b-review.md` | Aug 26 | External customer |
| `2026-08-27-sc500-program-review.md` | Aug 27 | Internal recurring — decisions |
| `2026-09-01-kaiho-regulatory-call.md` | Sep 1 | External customer |
| `2026-09-11-northline-board-meeting.md` | Sep 11 | Board |
| `2026-09-16-q3-leadership-offsite.md` | Sep 16 | Internal — Act 2 resolution |

## Note on dates

Every email in the corpus was *delivered* on 2026-08-23 — the Gmail connector
cannot backdate. The dates in the message bodies and in these notes are the
fictional timeline. If your ingestion uses `received_at` for recency scoring,
that signal is flat across the whole corpus and you should score on in-body
dates instead, or ignore recency for this dataset.
