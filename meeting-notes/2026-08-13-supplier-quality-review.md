# Supplier Quality Review — 2026-08-13

**When:** Wednesday 2026-08-13, 09:00–10:00 PDT
**Chair:** Nadia Okonkwo
**Attendees:** Nadia Okonkwo, Grant Feldman, Tobias Rehn, Ronith Balusani (partial)

## Context

Monthly supplier quality review. Vantera return rate had risen from 11 units to
31 units over six weeks and was still being managed under a BLE pairing
hypothesis (CS-40218).

## Discussion

**Nadia** presented the return data and said, for the first time, that she did
not believe the pairing hypothesis. Her reasoning: returned units pair fine on
the bench. Nobody had tested them for measurement accuracy because nobody had
suspected measurement.

**Grant** raised Anshun change notice ACN-2026-014 — a substrate adhesive
second-source qualified in March 2026, categorised as a minor process change,
acknowledged and filed without escalation.

This is the moment the root cause was on the table. It took another six days to
be confirmed because the humidity chamber run takes 96 hours.

**Tobias** noted the Bastion cold-storage failures were being tracked as a
separate battery issue and asked whether anyone had checked whether the two
populations overlapped. Nobody had.

## Decisions

- Run a humidity chamber study on Lot 22B vs Lot 21D control. **This is the
  decision that broke the case open.**
- Hold remaining 22B kanban bins pending results.

## Actions

| # | Action | Owner | Due | Outcome |
|---|---|---|---|---|
| 1 | Humidity chamber study, 20+20 samples, 96h @ 85% RH / 40C | Nadia | Aug 19 | Done — 4.7% mean drift vs 2.0% limit |
| 2 | Pull build records for all 22B consumption | Grant | Aug 19 | Done — 4,112 units, 3,720 in field |
| 3 | Check Bastion serial overlap with 22B build lots | Tobias | Aug 19 | Done via Ingrid — 9 of 9 worst units in B-2227 |
| 4 | Review incoming inspection policy for adhesive changes | Grant | Sept | Open |

## Grading notes

Action 4 is the real corrective action and it is the one with no owner pressure
on it. An agent scoring importance well should notice that a process gap with a
September date is quieter but more durable than the escalations around it.

Grant's self-reproach about the ACN appears in the mail. Dana's reply explicitly
reframes it as a process gap rather than a personal error — that exchange is a
good test of whether summarization preserves *who said what about whom*, which
matters if the user is ever going to act on the summary.
