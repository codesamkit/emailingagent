# Kaiho Footwear — Regulatory Pathway Call — 2026-09-01

**When:** Monday 2026-09-01, 17:00–18:04 PDT / Tuesday 09:00–10:04 JST
**Attendees (StrideCore):** Rachel Ammons (chair), Ronith Balusani, Priya Raghunathan
**Attendees (Kaiho):** Yuki Tanabe (R&D Manager), Hiroshi Sato (QA),
Aiko Fujimoto (Regulatory Affairs)
**Recording:** Fathom — notification in corpus

## Why this call was scheduled early in Kaiho's week

Priya's recommendation. Tanabe needs to take the outcome back to his regulatory
group before responding substantively; a Friday afternoon Osaka time would give
them a weekend to construct a position StrideCore would then have to argue with.

## 1. ISO 13485 — declined

StrideCore will not pursue ISO 13485. Reasoning presented rather than the bare
decision: certification is not a document to obtain, it is a permanent change
to how the whole company operates — design history files, post-market
surveillance, vigilance reporting, notified body audits — across the entire
product line, for a segment that is a small share of volume.

Fujimoto accepted after questioning whether a supplier quality agreement alone
satisfies their notified body's expectations. Tanabe noted his regulatory group
had received a clearer statement of the alternative pathway from StrideCore
than from their own analysis.

## 2. Component qualification package — agreed

StrideCore provides; Kaiho remains legal manufacturer and qualifies the SC-400
through its own supplier control process. Package includes material
declarations, ISO 10993 biocompatibility, EMC/electrical safety reports,
environmental qualification, sensor characterisation, manufacturing process
description.

Kaiho added four items, all accepted:
1. **Sensor drift characterisation over device lifetime** (18 months) — not
   just over temperature and humidity
2. FMEA for the sensing subsystem, or enough design information to build one
3. Software lifecycle documentation (not IEC 62304 compliance, but process,
   verification approach, defect management)
4. 24-month manufacturing change history

## 3. Lot 22B disclosed proactively

Priya disclosed the drift investigation unprompted. **Kaiho's exposure is zero**
— their units are February 2026 build on the prior sensor lot, verified twice
against build records.

Disclosed anyway because Kaiho is designing around the SC-400 as a component
they will buy for years, and the honest statement about that component is not
"it is stable" but "we have just discovered our sensor supply chain can produce
a progressive baseline drift from a material change we were notified of and did
not treat as significant."

Fujimoto asked whether the mechanism could occur on Kaiho's lots. Confirmed no.

## 4. The technical finding that came out of it

Sato had separately filed CS-40312: systematic low-load under-reading, ~9–10%
low at Kaiho's 32 kPa clinical alert threshold, caused by a factory zero offset
calibrated at mid-range.

Sato then connected the two independently, from two communications one of which
was not addressed to him:

> Both are zero-point phenomena. If delamination shifts the physical zero, and
> the factory offset correction is fixed, then the two errors are not
> independent. A drifting unit would show a low-load error that changes over
> time in a manner your correction curve cannot accommodate.

And further: if delamination concentrates in the medial forefoot, and the
offset error is 1.4x larger in those same zones, the two errors **compound in
the same zones** rather than averaging out.

Nadia accepted the analysis and withdrew her earlier implication that the
correction curve solved the problem. What is actually needed is periodic
re-establishment of physical zero, not factory-once calibration — which is
mechanically the same measurement as Devon's off-foot baseline method, running
continuously rather than retrospectively.

## 5. Change notification scope — agreed

90 days hardware and materials · 30 days qualifying firmware · emergency
provision for security fixes. Sensor construction materials **including
adhesives and substrates** explicitly in scope, at Kaiho's request.

Scoped deliberately rather than open-ended: StrideCore committed only to what
it can honour. Rachel's position, backed by Priya, was that an open-ended
commitment breached later is worse than a bounded one honoured.

Kaiho's confidence was materially increased by StrideCore acknowledging that
firmware 2.1 changed baseline calibration without notification — the exact
failure the agreement prevents.

## Actions

| # | Action | Owner | Due |
|---|---|---|---|
| 1 | Draft supplier quality agreement | Rachel Ammons | Sept 12 |
| 2 | Lifetime baseline stability characterisation | Nadia Okonkwo | Sept 30 |
| 3 | **Decision: continuous zero re-establishment as a product feature** | **Ronith** | Sept 19 |
| 4 | Follow-up after Kaiho design review (Sept 4) | Priya | Sept 5 |
| 5 | Correction curve derivation + characterisation data under NDA | Nadia | Sept 4 |

## Grading notes

Action 3 is the corpus's best example of a **product opportunity discovered
through a defect**. Devon's off-foot baseline method was built to correct
drifted field units retrospectively. Sato's analysis shows the same mechanism,
run continuously, would be a materially better foundation for absolute-threshold
clinical alerting than anything StrideCore currently ships.

Nobody in the corpus says "this is a product opportunity." Three people
describe pieces of it in three different threads — Devon in CS-40218, Nadia in
CS-40312, and Devon again in his unrelated gait-fatigue email.

If an agent connects those, that is a genuinely strong result and not one I
would expect.
