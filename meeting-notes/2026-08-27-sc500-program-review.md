# SC-500 Program Review — 2026-08-27 — DECISION MEETING

**When:** Thursday 2026-08-27, 10:00–10:30 PDT (hard stop, Dana has Northline at 3)
**Chair:** Ronith Balusani
**Attendees:** Ronith Balusani, Marcus Chen, Aleksandra Petrova, Nadia Okonkwo,
Grant Feldman, Tobias Rehn, Dana Whitfield

Dana's pre-meeting instruction: three things, nothing else. A date not a range,
Lot 22B exposure in units/accounts/dollars, and the platform decision as a
decision not a menu.

## 1. SC-500 freeze date — DECIDED

**Aug 14. 8-unit soak. Targeted BLE regression suite. Supervision timeout
change only.**

Recorded with the regression scope attached, per Nadia's explicit request —
"Aug 14" alone is not auditable, the scope is what gets asked about after a
field failure.

Conditions on the record:
- If the 8-unit soak surfaces anything, escalate to the full 24-unit run and
  the date becomes Aug 21 or later. Nobody plans around Aug 14 as firm until
  Wednesday Aug 12.
- **If the date slips to Aug 21, the Hongli Sept 7 build slot is lost and the
  next available is Oct 12.** Grant to hold a contingency plan and be on a call
  with Wei Lam the same day.

Marcus noted for the record that QA was not being obstructive — the 5-day rule
exists because engineering shipped firmware 1.8.2 in Nov 2024 with a reconnect
defect that a 36-hour regression would not have caught. The rule was correct
and, in this instance, too blunt; Nadia sharpened it when shown the diff.

## 2. Lot 22B exposure — REPORTED

4,112 units built · 3,720 in field · ~USD 745,000 product value ·
~USD 260,000 COGS if full replacement · Anshun contribution capped USD 140,000
(~54%) · replacement program ~EUR 114,000 across 4 tranches.

Units in service: continue running, decided at the Vantera call Aug 26.

## 3. Q4 platform decision — DECIDED

**Build the SDK properly. Ship one reference dashboard on top of it, as source,
customer-hosted, unsupported.**

With three conditions, each adopted from the person who raised it:

- **Hosting answer pre-decided (Camille).** If a customer asks StrideCore to
  host, the answer is: single-tenant hosted is a real SKU with a real price and
  a defined support boundary — **but not sold before the operator is hired.**
  Decided now, in writing, so it is not decided in the room with a PO on the table.
- **Do not sell hosted before hiring (Devon).** Sequencing, not yes/no.
- **The API contract commitment is named explicitly (Dana).** Publishing an API
  contract means StrideCore stops being able to change it unilaterally. Dana
  flagged this as the largest commitment in the thread and the one nobody had
  costed. Recorded as such.

Effort: SDK 9–11 weeks / 2 engineers. Reference app 4 weeks ugly, 7 weeks
demo-able. ~13–15 weeks total, end of Q4 if started September.

## Items raised and NOT decided

| Item | Status |
|---|---|
| SC-500 idle current — restore bias rail load switch | Agreed in principle; roll into next scheduled spin, do not respin for it. Datasheet standby figure to be corrected to a defensible 12–13 days. **Someone still has to tell Karen the number is not 14.** |
| Segment test profiles (4 profiles, ~4h rack time/release) | Supported by QA, firmware, support, and Meridian. Blocked: StrideCore cannot currently run 2 of the 4 (no rigid-sole fixture, no low-load static rig). ~5 weeks of Joon-ho. Not funded. |
| Unowned firmware (charging dock, Hongli test fixture) | Raised. No owner assigned. |
| EN 18031 / CE Declaration of Conformity gap | Self-declare SC-400, engage TÜV for SC-500. Marieke Bosch needs a substantive answer by Aug 29. Two EN 18031-2 clauses need a product decision on end-user data control. |
| "The person on the phone does not know what the engineers know" | Requested for September offsite as its own agenda item |

## Grading notes

This meeting is the resolution point for most of the corpus. If you want a
single evaluation target: **can the agent, given only the inbox, produce this
list of decisions and open items?**

The three decided items are recoverable from mail with careful reading. The
five undecided items are the harder test — they are scattered across threads,
none of them is anyone's emergency, and several are more consequential than the
things that are on fire.
