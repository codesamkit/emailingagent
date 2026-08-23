# Vantera Safety Group — Lot 22B Technical Review — 2026-08-26

**When:** Wednesday 2026-08-26, 06:00–07:00 PDT / 15:00–16:00 CEST
**Attendees (StrideCore):** Priya Raghunathan, Nadia Okonkwo, Ingrid Solberg,
Devon Marsh, Ronith Balusani
**Attendees (Vantera):** Joost van Dijk (Head of Product), Bram Kuiper (Technical Manager)

## Context

Vantera is StrideCore's second-largest account. 2,340 affected units across 60
distribution centres. Return rate 2.0% and accelerating. Five weeks of being
told this was a pairing problem when it was not.

The customer's actual exposure is not the hardware. Vantera has an eleven-month
fatigue dataset supporting an underwriter discussion about a premium reduction,
running since March. Joost stated plainly that the data is worth considerably
more to Vantera than the entire value of the hardware.

## Agenda and outcomes

**1. Root cause and mechanism (Nadia, 15 min).** Delivered as briefed. Kuiper
accepted the mechanism without argument.

**2. Retrospective data correction (Devon, 20 min).** The centre of the meeting.

Devon presented the off-foot baseline method: extract per-zone baselines from
periods of 90+ minutes with no IMU motion and all zones below 4 kPa — i.e. a
boot in a locker. Validated on 31 returned units against measured actual drift:
correlation 0.94, mean absolute error 0.6 percentage points.

Caveats presented honestly and unprompted:
- 46 of 2,293 units lack usable off-foot windows (one site with different practice)
- Drift is **not** zone-uniform — medial forefoot zones 7–14 drift ~1.6x the
  heel zones, so correction must be per-zone
- Validation is 31 units, which is real but small

Kuiper spent twenty minutes on methodology and pushed hard on both the 46 units
and the zone dependence. He accepted the approach.

Joost, quoted: *"This is the first time in six weeks that someone has shown me
something instead of telling me something."*

**3. Replacement plan (Ingrid, 10 min).** Tranche 1 of 4, 180 units, delivery
~Sept 17, with the Anshun dependency stated explicitly. Joost's response: a
date with a stated risk is worth more than a date without one.

Kuiper requested Venlo move to tranche 2 so that site receives replacements
rather than only having units collected — the Venlo DC manager stopped using
the system in July and Kuiper wants him to see the fix. Agreed; costs nothing.

**4. Units in service (Ronith, 10 min). DECISION.**

**Continue running.** Rationale given: drift direction is conservative (over-
reports fatigue), and suspending the fleet destroys the operational data the
retrospective correction depends on. Coupled decision, not two independent ones.

Caveat surfaced by Tobias's bench work and delivered on the call: the real risk
is not bad numbers, it is *missing* numbers. Advanced-delamination units fail
the sensor plausibility check at startup and the firmware hangs silently rather
than reporting a fault. A worker then appears fine while being unmonitored.

Mitigation: missing-data report so absence becomes visible. Joost accepted
without argument once the coupling was explained.

**5. New commitment.** Joost asked that the missing-data report be permanent,
not an incident measure. His argument: a unit that can be unmonitored silently
is a gap in his safety programme regardless of cause. Priya said yes on the
call. Logged as ACT-9931; likely should be a product feature, not a
Vantera-specific report.

## The structural point

Joost, closing:

> I do not need you to be faster. I need the person on my phone to know what
> your engineers know.

Priya logged this in the case record rather than the call summary, deliberately,
because case records get read in postmortems and call summaries do not. She
noted it was the fifth time the same observation had been made this month by
five different people — Ingrid about herself, Hector three times from support,
Nadia about segment test profiles, and now the customer.

Requested as its own item on the September offsite agenda.

## Grading notes

This meeting resolves the corpus's largest open decision (ACT-9916). The
resolution is non-obvious and depends on a coupling between two actions that
were logged as independent.

Good test: does the agent's summary of CS-40218 mention *why* the units stay
running, or only *that* they do?
