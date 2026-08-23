# Q3 Leadership Offsite — Day One — 2026-09-16

**When:** Wednesday 2026-09-16, 09:00–16:00 PDT
**Where:** McMenamins Kennedy School, Portland
**Chair:** Dana Whitfield (item 1), Ronith Balusani (item 2)
**Attendees:** Dana Whitfield, Ronith Balusani, Marcus Chen, Camille Duarte,
Bill Ostrander, Priya Raghunathan, Nadia Okonkwo, Grant Feldman, Rachel Ammons,
Tobias Rehn, Devon Marsh. (Aleksandra Petrova day 2 only — staying back for a
new hire's first day.)

Rescheduled from Sept 9–10 after three attendees hit hard external conflicts.

## Item 1 — Unowned corrective actions (09:00–12:30)

Dana's framing, set in advance and enforced in the room: **come with a named
owner per item, or an argument for why one should stay unowned. "We discussed
it" is not an outcome.**

Pre-read: Hector Villalobos, *"Three findings, one gap"*, circulated Sept 12.
Written in his notice period at Priya's and Ronith's request, and deliberately
unsparing.

### The nine

| # | Item | Raised by | Outcome |
|---|---|---|---|
| 1 | Configuration management for accessories | Villalobos | Owner: Rehn. Scoped to firmware-bearing accessories only. |
| 2 | Segment test profiles | Okonkwo | Owner: Okonkwo. Funded — rigid-sole fixture + low-load static rig, ~5 wks Park. |
| 3 | Timing dependency records | Petrova | **Deliberately unowned.** See below. |
| 4 | Dock firmware ownership | Rehn | Owner: Petrova, transferring to Aguirre in Q1. |
| 5 | Hongli production test fixture | Rehn | Owner: Rehn. Zhou's defect report is the entry point. |
| 6 | Incoming inspection for material changes | Feldman | Owner: Feldman. Already in flight; now has a date. |
| 7 | EN 18031-2 product questions | Ammons | Owner: Chen. Folded into SDK scope — see below. |
| 8 | API contract ownership | Marsh | Owner: Marsh. |
| 9 | RMA repeat-defect detection | Raghunathan | Owner: Raghunathan. Added after CS-40447. |

### The one they chose not to own

Item 3, timing dependency records, was **deliberately left unowned**, and this
was the most useful twenty minutes of the day.

Petrova had proposed a maintained record of what depends on each timing
constant, and said herself it would be stale within a month without an owner.
Chen argued for owning it. Okonkwo argued against, on the grounds that a
document that is authoritative-looking and stale is worse than no document,
and that the actual control is the new `ota-recovery` regression suite, which
fails loudly rather than relying on anyone reading anything.

Accepted. **Recorded explicitly as a decision not to do it, with the reasoning,
so it does not get re-raised as an oversight in six months.**

Dana's comment: "That's the first time anyone here has said no to a good idea
for a good reason."

### The thing nobody had said out loud

Villalobos's document made one argument the room had not made for itself: every
one of the nine was raised by someone junior to the person who would have had
to fund it, in a thread about something else, at the end of a message.

Chen's response, recorded: *"Eight of these arrived as the last paragraph of an
email about a different subject. That is not a coincidence about the people.
That is what our meetings leave no room for."*

No action assigned. Probably the most important sentence of the day.

## Item 2 — SC-500 replan against the October 12 slot (13:30–16:00)

**Decisions:**
- Take the Sept 29 short run for 120 qualification units. $18,000 approved.
- **Do not** build the 400 held mechanicals. Park put the probability of a
  mechanical change at 15%, above Chen's 10% threshold. Accept five weeks
  rather than gamble 400 units to save two.
- Q1 launch confidence restated at the figure given to the board, with the
  October 12 slot named as the single dependency.

## Read into the record

Cheryl Nunez's message about Terrence Wade, at her explicit permission — the
worker flagged by a drifting instrument who turned out to have been genuinely
fatigued anyway. *"Right for the wrong reason."*

Also read: her closing observation that every point this incident moved forward,
it was because somebody told somebody else something they did not have to, and
that this is not a process and she does not know how you write it into one.

## Grading notes

This meeting is where Act 2 resolves. If you want one test of whether the agent
can synthesize the whole corpus: **can it produce this table of nine items with
correct attribution and outcomes, from the mail alone?**

The deliberate non-ownership of item 3 is the subtlest thing in the corpus. An
agent summarising "nine items, eight assigned" has it right. One reporting
"eight of nine resolved, one outstanding" has inverted the meaning of the most
considered decision in the room.
