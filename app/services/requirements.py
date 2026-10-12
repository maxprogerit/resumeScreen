"""Job description -> structured requirements, plus normalisation of edited requirements."""

from app.llm import prompts
from app.llm.client import LLMClient, LLMInvalidOutput, LLMUnavailable
from app.schemas import JobRequirements
from app.skills import canonical_languages, canonical_skills


def normalize_requirements(req: JobRequirements) -> JobRequirements:
    """Canonicalise skills/languages. A skill listed as must-have is not repeated as nice-to-have."""
    must = canonical_skills(req.must_have_skills)
    nice = [s for s in canonical_skills(req.nice_to_have_skills) if s not in must]
    return req.model_copy(
        update={
            "must_have_skills": must,
            "nice_to_have_skills": nice,
            "languages": canonical_languages(req.languages),
        }
    )


def extract_requirements(llm: LLMClient, title: str, description: str) -> JobRequirements | None:
    """Returns None when the LLM is unavailable or keeps returning invalid output;
    the recruiter then fills the requirements in by hand."""
    for _attempt in range(2):
        try:
            result = llm.parse(
                system=prompts.EXTRACT_REQUIREMENTS_SYSTEM,
                user=prompts.extract_requirements_user(title, description),
                schema=JobRequirements,
            )
        except LLMInvalidOutput:
            continue
        except LLMUnavailable:
            return None
        return normalize_requirements(result)
    return None


def empty_requirements() -> JobRequirements:
    return JobRequirements(
        must_have_skills=[],
        nice_to_have_skills=[],
        min_years_experience=None,
        languages=[],
        education_level=None,
    )
