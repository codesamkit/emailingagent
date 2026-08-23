# SC-500 Program Review — 2026-08-20

**When:** Thursday 2026-08-20, 10:00–10:47 PDT
**Chair:** Ronith Balusani
**Attendees:** Ronith Balusani, Marcus Chen, Aleksandra Petrova, Nadia Okonkwo,
Grant Feldman, Tobias Rehn, Dana Whitfield
**Recording:** Fathom — corresponding notification is in the corpus

## Discussion

**Firmware freeze.** Marcus presented his three options. The room split exactly
as expected: Sasha for scoped regression, Nadia for subsystem regression.

Marcus's contribution was to reframe: the decision is not a date, it is whether
regression scope follows the change or the subsystem. The date falls out.

Ronith did not decide in the room. He asked to see the actual diff first.
**This was the right call** and it is what made the Aug 14 outcome possible —
Nadia's concession was conditional on the diff not touching the connection
parameter negotiation state machine, and it turned out not to.

**Current draw.** Tobias raised 2.1 mA vs 1.4 mA spec in the last ten minutes.
Deferred. In hindsight this was the meeting's mistake: the item that turned out
to have no good answer got ten minutes at the end.

## Decisions

**None reached.** Follow-up scheduled.

## Actions

| # | Action | Owner | Due | Outcome |
|---|---|---|---|---|
| 1 | Review FW-2211 diff, confirm scope | Marcus | Aug 22 | Done — 6 lines, does not touch negotiation SM |
| 2 | Define conditions for reduced soak | Nadia | Aug 22 | Done — 8 units, start Monday, exit Aug 14 |
| 3 | Confirm Hongli build slot implications | Grant | Aug 25 | Done — and it is worse than anyone thought |
| 4 | Complete current characterisation | Tobias | Aug 25 | Done — found the allocation sheet had zero margin |
| 5 | **Freeze date decision** | **Ronith** | Aug 27 | Pending |

## Grading notes

Grant's finding on action 3 is the sharpest hidden dependency in the corpus:
Aug 14 vs Aug 21 is not a two-week difference, it is a **five-week** difference,
because missing the Aug 24 firmware handoff to Hongli loses the Sept 7 build
slot and the next available slot is Oct 12.

That fact lives in the sixth message of a six-message thread, sent by the person
with the least seniority in it. Any ranking model that decays importance by
position in thread, or weights sender seniority heavily, will bury it.

This is deliberately the corpus's best test of whether the agent reads threads
or skims them.
