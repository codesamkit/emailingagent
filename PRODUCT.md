# Product

## Register

product

## Users

A single power user (the inbox owner) triaging their own Gmail through an AI pipeline. They sit at a desk, often in the evening or between meetings, working through a ranked queue: scan importance, read summaries, approve/edit reply outlines, expand to full drafts. Keyboard-driven (j/k), review-heavy, short frequent sessions.

## Product Purpose

Valence is the review interface for an AI email agent (Gmail + Google Calendar). The pipeline ranks emails by importance, summarizes them, flags no-reply mail, and drafts reply outlines for read, replyable emails — calendar-aware for scheduling threads. The UI exists so a human can inspect and act on that structured output: nothing is ever sent automatically. Success = the user trusts the ranking at a glance and clears their queue faster than in Gmail.

## Brand Personality

Precise, calm, quietly scientific. Three words: **charged, minimal, exact**. The name comes from valence electrons — the outer-shell electrons that do all the interacting — a metaphor for the mail that actually needs a response. The atom theme is an undertone (orbital rings, electron dots, energy levels), never a costume.

Reference feel: **a native macOS app** — Apple Mail's list discipline, Things' restraint. System fonts, system blue, borderless fill controls, translucent toolbar. It should feel installed, not generated.

## Anti-references

- Gmail/Outlook skeuomorphism — this is a triage instrument, not a mail client clone.
- Sci-fi dashboard slop: scan lines, glows everywhere, HUD brackets, particle backgrounds.
- SaaS-cream landing aesthetics; hero metrics; identical card grids.
- The previous "Mail Desk" paper/manila/postage identity — fully retired.

## Design Principles

1. **The tool disappears into the queue** — density and scanability beat decoration; every pixel of the row earns its place.
2. **The atom is functional, not decorative** — orbital/electron motifs only where they encode state (score rings, unread charge, the logo). One metaphor, used exactly.
3. **One accent, meaningfully spent** — electron blue marks interaction, selection, and unread charge; urgency red is reserved for urgent. Nothing else gets color.
4. **Human-in-the-loop is visible** — drafts are always previews; actions read as review steps, never as sends.
5. **State is never ambiguous** — unread, no-reply, outline status, and score always legible without opening the email.

## Accessibility & Inclusion

- WCAG AA: body text ≥ 4.5:1 in both themes; large/bold text ≥ 3:1.
- Full keyboard operation (j/k + arrows for the queue, visible focus rings).
- `prefers-reduced-motion` honored everywhere, including the orbiting logo.
- Auto dark/light via `prefers-color-scheme`, with a manual override persisted locally.
- Level color is never the only signal — score numbers and level labels always accompany it.
