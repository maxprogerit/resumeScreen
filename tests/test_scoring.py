import numpy as np
import pytest

from app.schemas import EducationLevel, Weights
from app.services.scoring import (
    COMPONENTS,
    ScoringJob,
    ScoringProfile,
    combine,
    raw_components,
)

W = Weights(must_have=40, nice_to_have=10, experience=15, semantic=20, language_education=15)


def profile(**kw: object) -> ScoringProfile:
    base: dict[str, object] = {
        "skills": frozenset({"python", "docker"}),
        "years_experience": 4.0,
        "education_level": EducationLevel.BACHELOR,
        "languages": frozenset({"english"}),
        "embedding": np.array([1.0, 0.0], dtype=np.float32),
        "embedding_model": "m",
    }
    return ScoringProfile(**{**base, **kw})  # type: ignore[arg-type]


def job(**kw: object) -> ScoringJob:
    base: dict[str, object] = {
        "must_have": ("python", "docker", "kubernetes", "aws"),
        "nice_to_have": ("redis",),
        "min_years": 2.0,
        "languages": ("english",),
        "education_level": EducationLevel.BACHELOR,
        "embedding": np.array([1.0, 0.0], dtype=np.float32),
        "embedding_model": "m",
    }
    return ScoringJob(**{**base, **kw})  # type: ignore[arg-type]


def comps(p: ScoringProfile, j: ScoringJob) -> dict[str, dict[str, object]]:
    return raw_components(p, j, semantic_floor=0.2, semantic_ceil=0.8)


def test_component_values() -> None:
    c = comps(profile(), job())
    assert c["must_have"]["score"] == 0.5
    assert c["must_have"]["matched"] == ["python", "docker"]
    assert c["must_have"]["missing"] == ["kubernetes", "aws"]
    assert c["nice_to_have"]["score"] == 0.0
    assert c["experience"]["score"] == 1.0  # 4y vs 2y, capped
    assert c["semantic"]["score"] == 1.0  # identical vectors
    assert c["language_education"]["score"] == 1.0


def test_total_is_weighted_sum() -> None:
    result = combine(comps(profile(), job()), W)
    # 0.5*40 + 0*10 + 1*15 + 1*20 + 1*15 = 70 (weights already sum to 100)
    assert result["total"] == pytest.approx(70.0)
    assert sum(result["components"][n]["points"] for n in COMPONENTS) == pytest.approx(70.0)


def test_experience_is_capped_and_partial() -> None:
    assert comps(profile(years_experience=10.0), job())["experience"]["score"] == 1.0
    assert comps(profile(years_experience=1.0), job())["experience"]["score"] == 0.5
    assert comps(profile(years_experience=None), job())["experience"]["score"] == 0.0


def test_not_applicable_components_are_excluded_not_counted_as_zero() -> None:
    j = job(nice_to_have=(), min_years=None, languages=(), education_level=None)
    result = combine(comps(profile(), j), W)
    assert not result["components"]["nice_to_have"]["applicable"]
    assert not result["components"]["experience"]["applicable"]
    # must-have 0.5 and semantic 1.0 only, weights renormalised 40:20
    assert result["total"] == pytest.approx((0.5 * 40 + 1.0 * 20) / 60 * 100, abs=0.1)


def test_semantic_unavailable_when_embeddings_missing_or_incompatible() -> None:
    for p in (
        profile(embedding=None),
        profile(embedding_model="other"),
        profile(embedding=np.array([1.0, 0.0, 0.0], dtype=np.float32)),
    ):
        c = comps(p, job())["semantic"]
        assert c["score"] is None
    result = combine(comps(profile(embedding=None), job()), W)
    assert result["components"]["semantic"]["applicable"] is False


def test_semantic_mapping_is_clamped() -> None:
    orth = comps(profile(embedding=np.array([0.0, 1.0], dtype=np.float32)), job())["semantic"]
    assert orth["score"] == 0.0 and orth["cosine"] == 0.0


def test_education_partial_credit() -> None:
    j = job(education_level=EducationLevel.MASTER)
    assert comps(profile(education_level=EducationLevel.BACHELOR), j)["language_education"][
        "score"
    ] == pytest.approx((1.0 + 0.5) / 2)
    assert comps(profile(education_level=None), j)["language_education"]["score"] == pytest.approx(
        (1.0 + 0.0) / 2
    )


def test_changing_weights_changes_total_without_recomputing_components() -> None:
    c = comps(profile(), job())
    only_must = combine(
        c, Weights(must_have=1, nice_to_have=0, experience=0, semantic=0, language_education=0)
    )
    assert only_must["total"] == pytest.approx(50.0)
    # Stored breakdown can be re-weighted from its own components.
    again = combine(only_must["components"], W)
    assert again["total"] == pytest.approx(70.0)


def test_weights_validation() -> None:
    with pytest.raises(ValueError, match="positive"):
        Weights(must_have=0, nice_to_have=0, experience=0, semantic=0, language_education=0)
    with pytest.raises(ValueError):
        Weights(must_have=-1, nice_to_have=0, experience=0, semantic=0, language_education=1)


def test_no_requirements_means_zero_not_a_crash() -> None:
    j = job(
        must_have=(), nice_to_have=(), min_years=None, languages=(), education_level=None,
        embedding=None,
    )  # fmt: skip
    result = combine(comps(profile(), j), W)
    assert result["total"] == 0.0
