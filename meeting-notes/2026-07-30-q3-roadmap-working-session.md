# Q3/Q4 Roadmap Working Session — 2026-07-30

**When:** Wednesday 2026-07-30, 13:00–15:00 PDT
**Where:** Conference room 1
**Chair:** Ronith Balusani
**Attendees:** Ronith Balusani, Marcus Chen, Camille Duarte, Devon Marsh,
Dana Whitfield (last 30 min), Priya Raghunathan

## Purpose

Decide what engineering builds in Q4 beyond SC-500 sustaining. Two candidates
on the table: a customer-facing analytics dashboard, or a public SDK.

## Discussion

**Camille** made the revenue case for the dashboard. Bastion (5,000 units) and
Aurelio (3,000 units) have no software capability. Direct quote recorded:
Dale Rutherford said "I am not buying a science project, I need a screen my
safety managers can look at on Monday morning."

**Marcus** argued the SDK is the platform play. Core argument: a dashboard is a
product surface owned forever, and every OEM will want their own branding,
language, and metric definitions. Meridian already built a better athlete-facing
view than StrideCore would have, in six weeks, at zero cost to StrideCore.

**Devon** was asked for effort and declined to give one in the room, saying the
question was underspecified. Correct call — he produced real numbers three weeks
later once the reference-app option existed.

**Priya** raised that the dashboard would create a support surface Customer
Success is not staffed for. This point was noted and then dropped, and it
resurfaced as Devon's on-call argument in August.

**Dana** joined at 14:30, listened for 20 minutes, and said the decision was
Ronith's and should be made by end of Q3. She declined to express a preference.

## Decisions

**None.** The session ended without a decision. This was the intended outcome
per the agenda ("working session, not decision forum") but in retrospect the
delay cost about four weeks.

## Actions

| # | Action | Owner | Due | Outcome |
|---|---|---|---|---|
| 1 | Scope both options to a comparable level of detail | Marcus | Aug 14 | Partially — became the reference-app proposal |
| 2 | Quantify Bastion and Aurelio revenue dependency | Camille | Aug 14 | Done |
| 3 | Decide | Ronith | End Q3 | Open as of Aug 23 |

## Grading notes

The corresponding email thread (`[c.duarte@stridecore.com] Q4 roadmap — we need
the analytics dashboard, not the SDK`) is a five-message argument with a real
unresolved decision at the end. It is one of the best tests in the corpus for
whether the agent can identify that **the inbox owner owes a decision** rather
than just summarising two positions.

The correct reply outline for Ronith on that thread is roughly:
- Decide by Thursday's program review, not later
- The decision is component company vs. application company, per Marcus
- Pre-decide the hosting answer before Bastion asks, per Camille
- Do not sell hosted before hiring the operator, per Devon
- Name the API-contract commitment explicitly, per Dana

An agent that outputs "summarise both sides and schedule a follow-up" has
missed it.
