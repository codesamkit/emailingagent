# SC-500 Program Review — 2026-08-06

**When:** Thursday 2026-08-06, 10:00–10:52 PDT
**Chair:** Ronith Balusani
**Attendees:** Ronith Balusani, Marcus Chen, Aleksandra Petrova, Nadia Okonkwo,
Tobias Rehn, Grant Feldman

## The meeting where the freeze date died

Held one day before the committed Aug 7 firmware freeze.

## Discussion

**Firmware.** Sasha reported FW-2230 and FW-2244 closed on her branch. FW-2211
still open, but she stated she had a fix "essentially working." Nadia asked
whether the fix touched the radio stack. Sasha said yes. Nadia said that meant
a 5-day soak and the freeze could not happen tomorrow.

This was the first time the exit-criteria rule was stated explicitly in front
of everyone. Sasha's response, paraphrased: the rule was written when StrideCore
had one customer and is not a law of physics.

Marcus asked for 48 hours to look at options rather than settle it in the room.
Ronith agreed.

**Rev C.** Nadia reported vibration blocked — Hillsboro booked to Sept 2. First
time this was flagged as a schedule risk rather than a booking detail.

**Hongli.** T1 samples shipped Aug 8, expected Portland Aug 12. Grant flagged
the 10-working-day firmware handoff requirement for the Sept 7 pilot build.
This is the constraint that later turns a 2-week slip into a 5-week slip. It
was stated here and not written into any decision record.

## Decisions

- Aug 7 freeze **not met.** No replacement date set.
- Marcus to produce written options by Aug 8.

## Actions

| # | Action | Owner | Due | Outcome |
|---|---|---|---|---|
| 1 | Written freeze options with tradeoffs | Marcus | Aug 8 | Done — became the corpus's opening email |
| 2 | Pursue alternative vibration lab | Tobias | Aug 13 | Done — Cascade Test Services |
| 3 | Confirm Hongli firmware handoff deadline | Grant | Aug 13 | Done — Aug 24 |

## Grading notes

Marcus's Aug 8 options memo is the root message of the largest internal thread
in the corpus (6 messages). The thread resolves to **Aug 14, 8-unit soak,
targeted BLE suite** — a decision that only became available because Nadia
offered a conditional concession and Marcus then verified the condition against
the actual diff.

If the agent summarises that thread as "team debating firmware freeze date" it
has technically succeeded and substantively failed. The useful summary names
the resolution and the condition attached to it.
