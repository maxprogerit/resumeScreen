from fastapi import APIRouter

from app.api.schemas import WeightsOut
from app.deps import DB, SettingsDep
from app.schemas import Weights
from app.services import weights as weights_service
from app.services.retention import purge_expired

router = APIRouter(tags=["settings"])


def _out(db: DB, settings: SettingsDep, weights: Weights) -> WeightsOut:
    return WeightsOut(
        weights=weights.model_dump(),
        defaults=weights_service.default_weights(settings).model_dump(),
        is_override=weights_service.has_override(db),
    )


@router.get("/settings/weights", response_model=WeightsOut)
def get_weights(db: DB, settings: SettingsDep) -> WeightsOut:
    return _out(db, settings, weights_service.load_weights(db, settings))


@router.put("/settings/weights", response_model=WeightsOut)
def put_weights(weights: Weights, db: DB, settings: SettingsDep) -> WeightsOut:
    """Saves the weights and recomputes totals of all stored matches (no LLM calls)."""
    weights_service.save_weights(db, weights)
    return _out(db, settings, weights)


@router.delete("/settings/weights", response_model=WeightsOut)
def reset_weights(db: DB, settings: SettingsDep) -> WeightsOut:
    return _out(db, settings, weights_service.reset_weights(db, settings))


@router.post("/admin/retention/run")
def run_retention(db: DB, settings: SettingsDep) -> dict[str, int]:
    """Manually trigger the retention purge (it also runs hourly in the server process)."""
    return {
        "retention_days": settings.retention_days,
        "deleted_candidates": purge_expired(db, settings.retention_days),
    }
