# Deploys the existing api/main.py as-is. The batch pipeline
# (ingestion.cli + pipeline.cli) runs in the same image via cron
# (see deploy/cron-ingest.sh) so it shares the SQLite file on the
# mounted volume with the API — see api/README.md's read/write split.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# EMAIL_AGENT_DB, GMAIL_TOKEN_FILE, CALENDAR_TOKEN_FILE all point onto this
# path by convention (see deploy/fly.toml) — one mounted volume for
# everything the pipeline and API both need to read/write.
RUN mkdir -p /data

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
