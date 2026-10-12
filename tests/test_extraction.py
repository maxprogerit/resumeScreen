from datetime import date

import pytest

from app.schemas import (
    EducationLevel,
    ExtractedIdentity,
    ExtractedResume,
    ExtractedRole,
    JobRequirements,
)
from app.services.extraction import (
    ExtractionFailed,
    compute_years,
    extract_resume,
    highest_education,
    parse_date,
    postprocess,
)
from app.services.requirements import extract_requirements, normalize_requirements
from tests.fakes import FakeLLM, skills_requirements

NOW = date(2026, 1, 1)


def role(start: str | None, end: str | None) -> ExtractedRole:
    return ExtractedRole(title="Dev", company=None, start=start, end=end, summary="")


def resume(**kw: object) -> ExtractedResume:
    base: dict[str, object] = {
        "identity": ExtractedIdentity(display_name="A B", email=None, phone=None),
        "skills": [],
        "total_years_experience": None,
        "roles": [],
        "education": [],
        "languages": [],
        "certifications": [],
    }
    return ExtractedResume(**{**base, **kw})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2020-03", date(2020, 3, 1)),
        ("03/2020", date(2020, 3, 1)),
        ("Mar 2020", date(2020, 3, 1)),
        ("March 2020", date(2020, 3, 1)),
        ("2020", date(2020, 7, 1)),
        ("Present", NOW),
        ("garbage", None),
        ("2020-13", None),
        (None, None),
    ],
)
def test_parse_date(raw: str | None, expected: date | None) -> None:
    assert parse_date(raw, NOW) == expected


def test_compute_years_merges_overlapping_roles() -> None:
    roles = [role("2018-01", "2020-01"), role("2019-01", "2021-01"), role("2023-01", "present")]
    # 2018-01..2021-01 (3y) + 2023-01..2026-01 (3y)
    assert compute_years(roles, NOW) == pytest.approx(6.0, abs=0.1)


def test_compute_years_returns_none_when_a_date_is_unparseable() -> None:
    assert compute_years([role("2018-01", "2020-01"), role("sometime", "later")], NOW) is None
    assert compute_years([], NOW) is None
    assert compute_years([role("2020-01", "2019-01")], NOW) is None  # end before start


def test_postprocess_prefers_dates_over_model_estimate() -> None:
    r = resume(total_years_experience=30.0, roles=[role("2024-01", "2026-01")])
    assert postprocess(r, "text", NOW).total_years_experience == pytest.approx(2.0, abs=0.1)
    r2 = resume(total_years_experience=7.5, roles=[role("soon", "later")])
    assert postprocess(r2, "text", NOW).total_years_experience == 7.5


def test_postprocess_drops_skills_absent_from_text_and_canonicalizes() -> None:
    r = resume(skills=["K8s", "Kubernetes", "Terraform", "Python"], languages=["English (C1)"])
    out = postprocess(r, "I run k8s clusters and write Python.", NOW)
    assert out.skills == ["kubernetes", "python"]
    assert out.languages == ["english"]


def test_highest_education() -> None:
    from app.schemas import ExtractedEducation

    r = resume(
        education=[
            ExtractedEducation(level=EducationLevel.BACHELOR, field=None, institution=None),
            ExtractedEducation(level=EducationLevel.PHD, field=None, institution=None),
        ]
    )
    assert highest_education(r) == EducationLevel.PHD
    assert highest_education(resume()) is None


def test_extract_resume_retries_once_then_fails() -> None:
    ok = FakeLLM(invalid_resume_outputs=1)
    assert extract_resume(ok, "Python dev\n").skills == ["python"]
    assert len(ok.calls) == 2
    bad = FakeLLM(invalid_resume_outputs=5)
    with pytest.raises(ExtractionFailed, match="extraction_invalid"):
        extract_resume(bad, "Python dev")
    assert len(bad.calls) == 2  # exactly one retry
    with pytest.raises(ExtractionFailed, match="llm_unavailable"):
        extract_resume(FakeLLM(unavailable=True), "x")


def test_schema_rejects_out_of_range_years_and_unknown_fields() -> None:
    with pytest.raises(ValueError, match="out of range"):
        resume(total_years_experience=99)
    with pytest.raises(ValueError):
        ExtractedIdentity(display_name=None, email=None, phone=None, gender="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="out of range"):
        JobRequirements(
            must_have_skills=[], nice_to_have_skills=[], min_years_experience=-1,
            languages=[], education_level=None,
        )  # fmt: skip


def test_requirements_extraction_and_normalization() -> None:
    llm = FakeLLM(requirements=skills_requirements(["K8s", "Docker"], ["docker", "Redis"], 3))
    req = extract_requirements(llm, "SRE", "desc")
    assert req is not None
    assert req.must_have_skills == ["kubernetes", "docker"]
    assert req.nice_to_have_skills == ["redis"]  # docker already must-have
    assert extract_requirements(FakeLLM(unavailable=True), "t", "d") is None
    assert extract_requirements(FakeLLM(), "t", "d") is None  # invalid twice -> manual entry

    messy = JobRequirements(
        must_have_skills=["  PostgreSQL ", "postgres"], nice_to_have_skills=[],
        min_years_experience=None, languages=["German (B2)", "german"], education_level=None,
    )  # fmt: skip
    norm = normalize_requirements(messy)
    assert norm.must_have_skills == ["postgresql"] and norm.languages == ["german"]
