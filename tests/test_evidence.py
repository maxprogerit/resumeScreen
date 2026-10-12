from typing import Any

from app.schemas import EvidenceQuote, EvidenceResponse
from app.services.evidence import build_evidence, locate_quote
from tests.fakes import FakeLLM

RAW = (
    "Jane Doe\nSKILLS\nPython, k8s, Docker\n\n"
    "Backend Engineer - Acme\n2020-01 - present\n"
    "Migrated   services to Kubernetes\nand cut deploy time by half.\n"
    "LANGUAGES\nEnglish, German\nEDUCATION\nMaster of Science in Computer Science, Eastfield University"
)


def components(**le: Any) -> dict[str, Any]:
    return {
        "must_have": {"score": 0.5, "matched": ["kubernetes", "python"], "missing": ["aws"]},
        "nice_to_have": {"score": None, "matched": [], "missing": []},
        "experience": {"score": 1.0, "years": 5.0, "required": 3.0},
        "semantic": {"score": None},
        "language_education": {"score": 1.0, **le},
    }


def test_locate_quote_ignores_whitespace_differences() -> None:
    span = locate_quote(RAW, "Migrated services to Kubernetes and cut deploy time by half.")
    assert span is not None
    assert "Kubernetes" in RAW[span[0] : span[1]]


def test_locate_quote_rejects_non_substrings() -> None:
    assert locate_quote(RAW, "Led a team of 40 engineers") is None
    assert locate_quote(RAW, "python, k8s, docker") is None  # case differs: not verbatim
    assert locate_quote(RAW, "ab") is None  # too short
    assert locate_quote(RAW, "Python " * 100) is None  # too long


def test_rule_based_quotes_are_real_substrings() -> None:
    items, meta = build_evidence(RAW, components(), llm=None)
    by_req = {i.requirement: i for i in items}
    k8s = by_req["kubernetes"]
    assert k8s.matched and k8s.quote and k8s.source == "rule"
    assert locate_quote(RAW, k8s.quote) is not None
    assert RAW[k8s.start : k8s.end].split() == k8s.quote.split()
    assert by_req["aws"].matched is False and by_req["aws"].quote is None
    assert meta["quotes_discarded"] == 0


def test_hallucinated_llm_quote_is_discarded_and_rule_quote_used() -> None:
    llm = FakeLLM(
        evidence=EvidenceResponse(
            items=[
                EvidenceQuote(
                    requirement="python", quote="Led Python migration at Google for 10 years"
                ),
                EvidenceQuote(requirement="kubernetes", quote="Migrated services to Kubernetes"),
            ]
        )
    )
    items, meta = build_evidence(RAW, components(), llm)
    by_req = {i.requirement: i for i in items}
    assert meta["quotes_discarded"] == 1
    assert by_req["kubernetes"].source == "llm"
    # fabricated quote never survives; python falls back to a rule-based real quote
    assert by_req["python"].source == "rule"
    assert "Google" not in (by_req["python"].quote or "")
    for item in items:
        if item.quote:
            assert locate_quote(RAW, item.quote) is not None


def test_llm_failure_falls_back_to_rules() -> None:
    items, meta = build_evidence(RAW, components(), FakeLLM(unavailable=True))
    assert meta["llm_quotes_requested"] is False
    assert all(i.quote for i in items if i.matched and i.kind != "experience")


def test_language_and_education_evidence() -> None:
    items, _ = build_evidence(
        RAW,
        components(
            languages_matched=["german"],
            languages_missing=["french"],
            education_required="master",
            education_have="master",
        ),
        llm=None,
    )
    by = {(i.kind, i.requirement): i for i in items}
    assert by[("language", "german")].quote
    assert by[("language", "french")].matched is False
    assert by[("education", "master")].matched is True
    assert "Master of Science" in (by[("education", "master")].quote or "")
    experience = next(i for i in items if i.kind == "experience")
    assert experience.matched and experience.quote is None
