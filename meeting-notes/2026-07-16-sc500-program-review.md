# SC-500 Program Review — 2026-07-16

**When:** Thursday 2026-07-16, 10:00–10:45 PDT
**Where:** Conference room 2 / Meet
**Chair:** Ronith Balusani
**Attendees:** Ronith Balusani, Marcus Chen, Aleksandra Petrova, Nadia Okonkwo,
Tobias Rehn, Grant Feldman, Dana Whitfield (partial)

## Agenda

1. SC-500 firmware v2.4.0 status against Aug 7 freeze
2. EVT3 board build status
3. Hongli tooling — T1 sample timing
4. Rev C qualification kickoff

## Discussion

**Firmware.** Sasha reported 7 open defects against 2.4.0, of which 4 were
characterised as bounded. FW-2211 (multipoint reconnect) described as "not
understood yet." Marcus asked whether Aug 7 was still credible; Sasha said yes.
Nadia said yes *if* nothing touches the radio stack.

Nobody wrote that condition down. It became the entire argument five weeks later.

**EVT3.** Tobias reported 4 boards built, current characterisation not yet run.
Flagged that the bias rail load switch removed in the April cost-down "may show
up in the power numbers." No action assigned.

**Hongli.** Grant reported tooling on schedule, T1 samples expected early August.
Payment milestone discussed briefly; Grant noted the PO went through 4 revisions
in February and he wanted to re-read the executed version. He did not, until
Aug 22.

**Rev C qual.** Nadia presented the 11-test matrix. Vibration flagged as needing
a shaker table booking. No booking made.

## Decisions

- Aug 7 freeze confirmed. **No conditions recorded.**
- Rev C qual to start immediately, target close mid-September.

## Actions

| # | Action | Owner | Due | Outcome |
|---|---|---|---|---|
| 1 | Book shaker table for vibration test | Nadia | Jul 23 | MISSED — Hillsboro booked out by the time she called |
| 2 | Run EVT3 current characterisation | Tobias | Jul 30 | Slipped to Aug 22 |
| 3 | Confirm Hongli T1 date | Grant | Jul 23 | Done |
| 4 | Re-read executed Hongli PO | Grant | — | Not assigned a date, not done until Aug 22 |

## Grading notes

This meeting is where three of the corpus's problems were created and none were
caught. Useful as a test of whether the agent connects a July commitment to an
August escalation.

Related mail: none directly — this predates the corpus window. Referenced in
`[m.chen@stridecore.com] SC-500 firmware freeze: where we actually are`.
