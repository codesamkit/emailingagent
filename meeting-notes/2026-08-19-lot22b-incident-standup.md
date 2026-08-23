# Lot 22B Incident Standup — 2026-08-19

**When:** Tuesday 2026-08-19, 16:30–17:15 PDT (called same-day)
**Chair:** Nadia Okonkwo
**Attendees:** Nadia Okonkwo, Grant Feldman, Priya Raghunathan, Ingrid Solberg,
Marcus Chen, Ronith Balusani, Dana Whitfield

## Trigger

Humidity chamber results came back at 16:00. Nadia called the standup within
thirty minutes.

## The finding

| Sample set | Mean drift @ 96h | Max | Pass/fail |
|---|---|---|---|
| Lot 21D (control) | 1.1% | 1.6% | Pass |
| Lot 22B | 4.7% | 7.9% | **18 of 20 fail** |

Acceptance limit 2.0%. Drift does not recover — still 4.1% mean after 48h back
at ambient. Failure mode consistent with adhesive delamination at the substrate
interface, i.e. a supplier process change, not a design margin issue.

## The realisation

Nadia's framing, recorded because it reframed the whole case:

> A 4.7% zero drift on a 32-zone array does not present to the customer as a
> bad sensor. It presents as a slowly wandering pressure map. Which is exactly
> what Vantera has been describing on CS-40218 and which we have been treating
> as a pairing issue. I think we have been chasing the wrong bug for three weeks.

## Exposure (Grant, same evening)

- 4,112 modules built on 22B, Apr 14 – Jun 30
- Vantera 2,340 · Meridian 1,180 · Bastion 200 · Kaiho **0** · quarantine 392
- ~3,720 units in customer hands
- ~USD 745,000 of product in field; ~USD 260,000 COGS if full replacement

## Decisions

1. **Tell Karen Lindqvist and Joost van Dijk this week, before Meridian's
   wear-test panel finds it.** Priya argued for this against the instinct to
   wait for a fix. Dana approved.
2. Ronith to produce a written "what we know / what we commit to / what we are
   not saying yet" before any customer contact. Dana's requirement.
3. Quarantine 392 finished goods. 100% incoming inspection on Lot 23A.

## Actions

| # | Action | Owner | Due |
|---|---|---|---|
| 1 | Three-part written position | Ronith | Aug 19 EOD |
| 2 | Customer calls, Vantera and Meridian | Priya + Nadia | Aug 20–21 |
| 3 | Serial ranges per shipment to Vantera | Ingrid | Aug 26 |
| 4 | Drift model — exposure to expected drift | Nadia | Sept 4 |
| 5 | Feasibility: retrospective data correction | Devon | Sept 11 |
| 6 | Replacement plan, 2,340 units | Grant | Aug 27 |
| 7 | **Decision: continue or suspend units in service** | **Ronith** | Aug 27 |

## Grading notes

Action 7 is the most important unresolved item in the entire corpus at the point
the mail was captured. It appears in CS-40218 as ACT-9916, flagged as having no
owner confirmation and blocking a customer call agenda.

**An importance-ranking model that does not put CS-40218 near the top of the
queue is wrong.** It is a Severity 1 case, with a named unassigned decision
owned by the inbox owner, blocking a scheduled external call, with safety-
relevant customer decisions downstream.

The eventual answer (continue running) is non-obvious and is arrived at through
a coupling nobody saw initially: suspending the fleet destroys the off-foot
baseline data that the retrospective correction depends on. Devon and Nadia
both flag the coupling independently. Good summarization should surface the
coupling, not just the answer.
