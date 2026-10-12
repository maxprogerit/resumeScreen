"""Scoring weights: config defaults, optionally overridden from the Settings page (DB)."""

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AppSetting, Match
from app.schemas import Weights
from app.services.scoring import combine

_KEY = "scoring_weights"


def default_weights(settings: Settings) -> Weights:
    return Weights(
        must_have=settings.weight_must_have,
        nice_to_have=settings.weight_nice_to_have,
        experience=settings.weight_experience,
        semantic=settings.weight_semantic,
        language_education=settings.weight_language_education,
    )


def load_weights(db: Session, settings: Settings) -> Weights:
    row = db.get(AppSetting, _KEY)
    if row is None:
        return default_weights(settings)
    return Weights.model_validate(row.value)


def has_override(db: Session) -> bool:
    return db.get(AppSetting, _KEY) is not None


def save_weights(db: Session, weights: Weights) -> None:
    row = db.get(AppSetting, _KEY)
    if row is None:
        db.add(AppSetting(key=_KEY, value=weights.model_dump()))
    else:
        row.value = weights.model_dump()
    retotal_all(db, weights)
    db.commit()


def reset_weights(db: Session, settings: Settings) -> Weights:
    row = db.get(AppSetting, _KEY)
    if row is not None:
        db.delete(row)
    weights = default_weights(settings)
    retotal_all(db, weights)
    db.commit()
    return weights


def retotal_match(match: Match, weights: Weights) -> None:
    """Recompute a stored match's totals from its stored component scores (no LLM involved)."""
    new = combine(match.breakdown["components"], weights)
    match.breakdown = new
    match.total_score = new["total"]


def retotal_all(db: Session, weights: Weights) -> None:
    for match in db.query(Match).all():
        retotal_match(match, weights)
