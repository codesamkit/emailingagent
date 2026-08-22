"""Adapts ingestion's RawEmail (ingestion/models.py) to the frozen shared
contract's RawEmail (models/schema.py).

ingestion/models.py's own docstring says it should collapse to
`from models.schema import RawEmail` now that Phase 0 is frozen, but it
still defines a diverged local dataclass (body_text vs body, received_at
as an ISO string vs datetime, read_status as a plain str vs ReadStatus,
no recipients field at all). Track A owns that file, so the real fix
belongs there; this adapter is the pipeline-boundary translation until
it lands - every other track already imports models.schema.RawEmail
directly.
"""

from __future__ import annotations

from datetime import datetime

from ingestion.models import RawEmail as IngestedRawEmail
from models.schema import ReadStatus
from models.schema import RawEmail as SchemaRawEmail


def _parse_recipients(headers: dict[str, str]) -> list[str]:
    """ingestion doesn't capture a structured recipients list, only a raw
    To header string - split it here rather than losing the field."""
    to_header = headers.get("To", "")
    return [addr.strip() for addr in to_header.split(",") if addr.strip()]


def adapt_raw_email(ingested: IngestedRawEmail) -> SchemaRawEmail:
    """Convert an ingestion.models.RawEmail into the canonical
    models.schema.RawEmail the rest of the pipeline is built against."""
    return SchemaRawEmail(
        email_id=ingested.email_id,
        thread_id=ingested.thread_id,
        sender=ingested.sender,
        recipients=_parse_recipients(ingested.headers),
        subject=ingested.subject,
        body=ingested.body_text,
        received_at=datetime.fromisoformat(ingested.received_at),
        read_status=ReadStatus(ingested.read_status),
        headers=dict(ingested.headers),
    )
