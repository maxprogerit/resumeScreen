"""Retention: hard-delete candidates (and, via ON DELETE CASCADE, all derived data)
older than RETENTION_DAYS. Seeded demo rows are synthetic and exempt."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from app.models import Candidate
from app.services.pipeline import recover_interrupted

log = logging.getLogger(__name__)

CHECK_INTERVAL_S = 300


def purge_expired(db: Session, retention_days: int, now: datetime | None = None) -> int:
    if retention_days <= 0:
        return 0
    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
    result = db.execute(
        delete(Candidate).where(Candidate.created_at < cutoff, Candidate.is_demo.is_(False))
    )
    db.commit()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


def _run_once(session_factory: sessionmaker[Session], retention_days: int) -> tuple[int, int]:
    recovered = recover_interrupted(session_factory)
    with session_factory() as db:
        return recovered, purge_expired(db, retention_days)


async def maintenance_loop(session_factory: sessionmaker[Session], retention_days: int) -> None:
    """Every few minutes: flag stale PROCESSING candidates and purge expired ones."""
    while True:
        try:
            recovered, deleted = await asyncio.to_thread(_run_once, session_factory, retention_days)
            if recovered:
                log.info("maintenance: %d interrupted candidates flagged", recovered)
            if deleted:
                log.info("retention: purged %d candidates", deleted)
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            log.warning("maintenance run failed (%s)", type(exc).__name__)
        await asyncio.sleep(CHECK_INTERVAL_S)
