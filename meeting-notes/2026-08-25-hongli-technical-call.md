# Hongli Precision — SC-500 Overmold T1 Technical Call — 2026-08-25

**When:** Monday 2026-08-25, 06:00–07:20 PDT (21:00 Dongguan)
**Attendees (StrideCore):** Joon-ho Park, Tobias Rehn
**Attendees (Hongli):** Kenny Zhou (NPI Engineering)
**Deliberately absent:** commercial staff from both sides

## Why no commercial people

Grant Feldman and Wei Lam agreed to split the tracks. Grant's reasoning was
that if Kenny believed the flash discussion was a proxy for a payment dispute,
he would give a commercially safe answer rather than a true one.

Tobias made the same argument in engineering terms: *"He will say the samples
are within normal T1 variation and offer to adjust. That is what any competent
supplier engineer does when the conversation smells like leverage."*

**This worked.** It is the clearest example in the corpus of a process decision
producing a technical outcome.

## The finding

Flash 0.4–0.9 mm along the medial overmold edge, spec 0.2 mm, on 11 of 12
inspected units. Not cosmetic: the flash sits over the zone 7–11 sensor traces,
and cross-sections showed 0.15 mm reduction in overmold wall thickness adjacent
to the flash line — material displacement, not simple overfill. Thin overmold
over a sensor trace is a flex-fatigue initiation site.

## Root cause

Tobias's melt-temperature hypothesis was correct.

Kenny confirmed the T1 run used a barrel temperature above the process sheet
value to achieve fill in the sensor tail region. It was recorded in the run log
and not communicated to StrideCore, because it was regarded as a normal T1
adjustment.

Signature matches: better fill in hard-to-reach sections, flash at the parting
line, localised thinning where material moves fastest, and 11-of-12 consistency
rather than intermittency.

**This is a process sheet change. Not a tooling modification.**

Schedule impact: approximately two weeks. Versus four to six weeks and an
SC-500 build plan hit if it had been venting.

## The other thing that came out

Kenny mentioned, unprompted, that Hongli had worked around a bug in the
StrideCore production test fixture software in July rather than reporting it,
because they assumed StrideCore knew and had chosen not to fix it.

That is the software that decides whether a unit passes before it ships. It was
written by the same departed contractor as the charging dock firmware, and it
has no owner, no build, and no test.

## Decisions

- T1 rejected. T2 run at process sheet temperatures, samples expected ~2 weeks.
- Tooling payment milestone remains unpayable until dimensional acceptance.
- Kenny to send cross-section flow line analysis.

## Actions

| # | Action | Owner | Due |
|---|---|---|---|
| 1 | T2 samples at corrected process parameters | Kenny Zhou | ~Sept 8 |
| 2 | Audit production test fixture software state | Tobias | Open |
| 3 | Inventory all unowned firmware/software | Tobias | Open |

## Grading notes

Actions 2 and 3 emerged sideways from a call about injection moulding. They are
arguably more serious than the thing the call was about.

The corpus contains a parallel discovery: the charging dock firmware (CS-40377)
also has no maintainer, no build, and no version tracking across ~1,400
deployed docks. Two pieces of production-critical software with no owner,
found by accident in the same week, in two unrelated threads.

**Connecting those two threads is the hardest cross-thread inference in the
corpus.** If an agent surfaces "unowned production software" as a theme rather
than as two separate incidents, that is a strong result.
