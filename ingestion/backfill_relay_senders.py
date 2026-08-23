"""One-off backfill: re-resolve relay-tagged senders on already-stored mail.

`ingestion/parse.py::to_raw_email` now resolves the real sender at the
ingestion boundary, but rows ingested before that still carry the relay
address and the tagged subject. This rewrites them in place so stored mail
matches what a fresh ingest would produce. Safe to re-run: rows with no tag
left to strip are skipped.
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ingestion.parse import split_relay_tag

DB = os.environ.get("EMAIL_AGENT_DB") or "ingestion/data/emails.db"
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

raw_updates, proc_updates = [], []
for row in con.execute("SELECT email_id, sender, subject, headers FROM raw_email"):
    addr, subject = split_relay_tag(row["subject"] or "")
    if addr is None:
        continue
    try:
        headers = json.loads(row["headers"] or "{}")
    except json.JSONDecodeError:
        headers = {}
    headers.setdefault("X-Envelope-From", row["sender"])
    raw_updates.append((addr, subject, json.dumps(headers), row["email_id"]))
    proc_updates.append((addr, subject, row["email_id"]))

con.executemany(
    "UPDATE raw_email SET sender=?, subject=?, headers=? WHERE email_id=?", raw_updates
)
con.executemany(
    "UPDATE processed_email SET sender=?, subject=? WHERE email_id=?", proc_updates
)

# The poisoned priors: every row is keyed on the relay address, which after
# this backfill matches nothing. Drop them rather than leave dead rows that
# would silently reattach if that address ever sends real mail.
dropped = con.execute(
    "DELETE FROM feedback WHERE sender NOT IN (SELECT DISTINCT sender FROM raw_email)"
).rowcount

con.commit()
print(f"raw_email rows rewritten     : {len(raw_updates)}")
print(f"processed_email rows rewritten: {con.total_changes and len(proc_updates)}")
print(f"orphaned feedback rows dropped: {dropped}")
print("\ndistinct senders now:",
      con.execute("SELECT COUNT(DISTINCT sender) FROM raw_email").fetchone()[0])
con.close()
