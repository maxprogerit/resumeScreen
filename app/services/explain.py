"""Explanation text. Written only from the stored breakdown and verified quotes."""

from typing import Any

from app.llm import prompts
from app.llm.client import LLMClient, LLMInvalidOutput, LLMUnavailable
from app.schemas import ExplanationResponse
from app.services.evidence import EvidenceItem

MAX_SUMMARY_CHARS = 1500  # enforced here: OpenAI strict schemas do not accept maxLength

_LABELS = {
    "must_have": "must-have skills",
    "nice_to_have": "nice-to-have skills",
}


def facts_for_explanation(breakdown: dict[str, Any], items: list[EvidenceItem]) -> dict[str, Any]:
    comps = breakdown["components"]
    return {
        "components": {
            name: {
                k: v
                for k, v in comp.items()
                if k
                in (
                    "score",
                    "matched",
                    "missing",
                    "years",
                    "required",
                    "cosine",
                    "languages_matched",
                    "languages_missing",
                    "education_required",
                    "education_have",
                )
            }
            for name, comp in comps.items()
            if comp["applicable"]
        },
        "not_applicable": [n for n, c in comps.items() if not c["applicable"]],
        "verified_quotes": [
            {"requirement": i.requirement, "quote": i.quote} for i in items if i.quote
        ],
    }


def template_summary(breakdown: dict[str, Any]) -> str:
    """Deterministic fallback wording, also used when no LLM is configured."""
    comps = breakdown["components"]
    sentences: list[str] = []
    for key, label in _LABELS.items():
        comp = comps[key]
        if comp["applicable"]:
            total = len(comp["matched"]) + len(comp["missing"])
            text = f"Matches {len(comp['matched'])} of {total} {label}"
            if comp["missing"]:
                text += f" (missing: {', '.join(comp['missing'])})"
            sentences.append(text + ".")
    exp = comps["experience"]
    if exp["applicable"]:
        years = exp.get("years")
        have = f"{years:g} years" if years is not None else "unknown experience"
        sentences.append(f"Experience: {have} against {exp['required']:g} required.")
    le = comps["language_education"]
    if le["applicable"]:
        bits = []
        if le.get("languages_missing"):
            bits.append(f"missing languages: {', '.join(le['languages_missing'])}")
        if "education_required" in le:
            bits.append(
                f"education {le.get('education_have') or 'not stated'} vs {le['education_required']} required"
            )
        if bits:
            sentences.append("Languages/education: " + "; ".join(bits) + ".")
    sem = comps["semantic"]
    if sem["applicable"]:
        sentences.append(f"Semantic similarity to the job text: cosine {sem['cosine']}.")
    else:
        sentences.append("Semantic similarity was not available for this candidate.")
    return " ".join(sentences) or "No scoring requirements are defined for this job yet."


def build_summary(
    llm: LLMClient | None, breakdown: dict[str, Any], items: list[EvidenceItem]
) -> tuple[str, str]:
    """Return (summary, source) where source is "llm" or "template"."""
    if llm is not None:
        try:
            response = llm.parse(
                system=prompts.EXPLAIN_SYSTEM,
                user=prompts.explain_user(facts_for_explanation(breakdown, items)),
                schema=ExplanationResponse,
            )
            return response.summary.strip()[:MAX_SUMMARY_CHARS], "llm"
        except (LLMUnavailable, LLMInvalidOutput):
            pass
    return template_summary(breakdown), "template"
