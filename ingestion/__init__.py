"""Track A — Gmail ingestion (Phase 1).

Fetches recent Gmail messages read-only, normalizes them into `RawEmail`
records, and persists them to the `raw_email` SQLite table for downstream
tracks (classification, scoring, summarization) to consume.
"""
