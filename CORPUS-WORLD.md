# StrideCore Simulation — World Bible

Reference for the 225-email corpus delivered to the Valence test inbox
(rsbhacks@gmail.com). Fictional timeline: 2026-03 (backstory) → 2026-09-30.

Delivered in two acts:
- **Act 1** — 160 emails, 79 threads. July–early September. The discovery arc.
- **Act 2** — 65 emails, 34 threads. September. Consequences and new pressure.

## Company

**StrideCore Technologies** — Portland OR, ~180 people, Series B (Northline Capital).
B2B hardware. **SC-series smart insole modules**: 32-zone flexible pressure array,
6-axis IMU, BLE 5.3, Li-poly cell, overmolded into customer midsoles. Sold to
footwear manufacturers, never direct to consumer.

- **SC-400** — current gen, volume. Rev B shipping, Rev C in qualification.
- **SC-500** — next gen, Q1 2027. Thinner stack, on-device gait classification.
- **StrideCore Cloud** — ingest API + analytics the OEMs build on.

Segments: athletic performance · industrial safety (fatigue/slip risk) · orthopedic.

## Inbox owner

**Ronith Balusani** — Head of Product & Program Management. Reports to Dana
Whitfield (COO). Owns SC-400 sustaining and the SC-500 program. Chairs the
Thursday program review. Is the decision-forcing function between engineering,
sales, and supply chain — which is why most threads end with something he owes.

## Internal cast — @stridecore.com

| Person | Role | Voice |
|---|---|---|
| Dana Whitfield (d.whitfield) | COO | Terse. Two sentences. Wants the number. |
| Marcus Chen (m.chen) | VP Engineering | Long, careful, options with tradeoffs |
| Aleksandra "Sasha" Petrova (s.petrova) | Firmware Lead | Blunt, lowercase, impatient with process |
| Tobias Rehn (t.rehn) | Hardware Lead, EE | Dry, precise, formal, cites measurements |
| Nadia Okonkwo (n.okonkwo) | QA Lead | Structured, bullet-heavy, always numbers |
| Grant Feldman (g.feldman) | Supply Chain Mgr | Anxious, dates and PO numbers |
| Camille Duarte (c.duarte) | VP Sales | Enthusiastic, pushes dates, sales-speak |
| Priya Raghunathan (p.raghunathan) | Dir. Customer Success | Diplomatic, escalation-savvy |
| Wes Tanaka (w.tanaka) | CSM — athletic | Casual, fast, fragments |
| Ingrid Solberg (i.solberg) | CSM — industrial/EU | Formal, thorough, numbered lists |
| Hector Villalobos (h.villalobos) | Support Eng, Tier 2 | Log dumps, no pleasantries |
| Joon-ho Park (j.park) | Mechanical Engineer | Concise, tolerances and drawings |
| Rachel Ammons (r.ammons) | Regulatory & Compliance | Cautious, cites clause numbers |
| Bill Ostrander (b.ostrander) | CFO | Rare, short, budget only |
| Talia Byrne (t.byrne) | Recruiting Coordinator | Cheerful, scheduling-heavy |
| Devon Marsh (d.marsh) | Data/Analytics Engineer | Curious, metrics, good questions |

## Customer accounts

**Meridian Athletic** — Boston MA. Running brand. Flagship, ~40% of revenue.
SC-400 in the "Flux" trainer; spring line needs Rev C by Oct 1.
- Karen Lindqvist — Director of Innovation. Direct, warm, firm on dates.
- Paul Achebe — Product Engineering Mgr. Detail-heavy, asks the hard question.

**Vantera Safety Group** — Rotterdam NL. Industrial safety footwear, 60 DCs.
- Joost van Dijk — Head of Product. Blunt Dutch directness.
- Marieke Bosch — Procurement. Contractual, structured, pricing pressure.
- Bram Kuiper — Technical Manager.

**Kaiho Footwear Co.** — Osaka JP. Orthopedic / diabetic offloading. Regulated.
- Yuki Tanabe — R&D Manager. Polite, precise, formal English.
- Hiroshi Sato — QA. Very formal, defect-report style, unusually sharp.
- Aiko Fujimoto — Regulatory Affairs.

**Bastion Workwear** — Dallas TX. Safety boots. 200-unit pilot → potential 5,000.
- Dale Rutherford — VP Operations. Folksy, impatient, ROI-driven, fair.
- Cheryl Nunez — Safety Program Lead. Practical, field anecdotes, does her own analysis.

**Aurelio Sport S.p.A.** — Montebelluna IT. Cycling + running. Prospect, RFQ stage.
- Elena Ricci — Innovation Lead. Enthusiastic, slightly imprecise English.
- Federico Marchetti — Supplier Quality.

## Suppliers & partners

- **Hongli Precision** (Dongguan CN) — contract manufacturer. Wei Lam (Account Mgr,
  formal), Kenny Zhou (NPI Engineer, technical, good).
- **Anshun Micro** (Hsinchu TW) — flex sensor supplier. Grace Hsu (Sales Engineer).
- **Nordic Semiconductor** — BLE SoC. Lars Bergström (FAE, Oslo/Trondheim).
- **TÜV Rheinland** — certification. Dr. Anke Möller (Lead Assessor, Köln).
- **Redpine Logistics** — freight forwarder. Manny Ortiz (breezy, problem-solving).
- **Calder & Wynn LLP** — outside counsel. Susan Calder (Partner, Portland).
- **Northline Capital** — Series B investor. Ravi Menon (board).

## Corpus composition

| Bucket | Act 1 | Act 2 | Total |
|---|---|---|---|
| Internal | 48 | 22 | 70 |
| External | 37 | 16 | 53 |
| CRM cases | 32 | 12 | 44 |
| Automated / no-reply | 30 | 10 | 40 |
| Spam / phishing | 13 | 5 | 18 |
| **Total** | **160** | **65** | **225** |

Threads: 79 (Act 1) + 34 (Act 2) = **113**.
Thread depth ranges 1–6 messages. Deepest: the SC-500 freeze thread (6).

## CRM cases

| Case | Account | Subject | Sev |
|---|---|---|---|
| CS-40218 | Vantera | Sensor drift, 47 units returned | S1 → S2 |
| CS-40255 | Meridian | Step count regression on FW 2.3.1 | S2 |
| CS-40301 | Bastion | Freezer units not waking | S1 |
| CS-40312 | Kaiho | Low-load calibration offset | Medium |
| CS-40344 | Meridian | Ingest API 429s during panel sync | S3 |
| CS-40350 | Vantera | RMA authorization, 180 units tranche 1 | S2 |
| CS-40377 | Bastion | Charging dock firmware mismatch | S2 |
| CS-40390 | Aurelio | Sample kit, 3 of 10 DOA | S3 |
| CS-40447 | Meridian | OTA bricks 3 panel units; defect dates to 2024 | S1 |
| CS-40460 | Aurelio | Zone saturation on rigid sole (640 kPa vs 400 spec) | Med |

## Sender encoding

The Gmail connector cannot set `From`, `Reply-To`, or custom headers. Every
message arrives from the connected account. Two compensating conventions:

1. **Subject prefix** `[originator@domain]` — identifies the **thread
   originator only**. The reply tool inherits the root subject and prepends
   `Re:`, so per-message prefixes and real threading are mutually exclusive.
2. **Signature block** on every message — name, title, company, email, phone.
   This is the per-message sender signal, and it is what a VIP/sender model
   should be built from.

Automated mail signs with a `noreply@`-style address and carries an
unsubscribe or "do not reply" footer. That is the no-reply signal, since
`List-Unsubscribe` and `Auto-Submitted` headers cannot be set.

## Known limitations of this corpus

- **All `From` addresses are identical.** Sender-reputation and VIP-list
  features must be driven from subject prefix and signature, not headers.
- **All `received_at` timestamps are the delivery date.** Recency scoring is
  flat. Use in-body dates or disable recency for this dataset.
- **No `List-Unsubscribe` / `Auto-Submitted` headers.** Rule-based no-reply
  detection must fall back to body and sender-string patterns.
- **No attachments.** Where a thread implies one, the body says so explicitly.
- **No calendar events.** `is_scheduling_related` will fire on several threads
  but `get_calendar_context()` returns nothing. The corpus contains scheduling
  asks with concrete times if you want to populate a calendar to match.
