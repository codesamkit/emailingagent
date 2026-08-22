#!/usr/bin/env bash
# Refreshes the shared SQLite db so the deployed API (and the Gmail
# add-on calling it) never serves data staler than one cron interval.
# Runs `ingestion.cli ingest` then `pipeline.cli process` against the
# same EMAIL_AGENT_DB the API reads — see api/README.md's read/write split.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

python -m ingestion.cli ingest
python -m pipeline.cli process   # incremental by default — see pipeline/incremental.py
