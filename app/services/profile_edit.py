"""Human correction of an extracted profile (or manual entry when extraction failed).

Edits are marked on the profile (`manually_edited`) so recruiters can see that a person, not
the model, produced these values. Identity fields are not editable here and never feed scoring.
"""

from pydantic import BaseModel, ConfigDict, field_validator

from app.llm.client import LLMClient, LLMInvalidOutput, LLMUnavailable
from app.models import Candidate, CandidateIdentity, CandidateProfile, CandidateStatus
from app.schemas import CleanList, EducationLevel
from app.services.matching import embedding_text_for_profile
from app.skills import canonical_languages, canonical_skills
from app.vectors import to_bytes


class ProfileEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skills: CleanList
    total_years_experience: float | None
    education_level: EducationLevel | None
    languages: CleanList
    certifications: CleanList

    @field_validator("total_years_experience")
    @classmethod
    def _years_in_range(cls, v: float | None) -> float | None:
        if v is not None and not 0 <= v <= 60:
            raise ValueError("total_years_experience out of range")
        return v


def apply_profile_edit(
    candidate: Candidate, edit: ProfileEdit, llm: LLMClient | None
) -> CandidateProfile:
    """Apply `edit` to the candidate's profile (creating it if extraction had failed)."""
    profile = candidate.profile
    if profile is None:
        profile = CandidateProfile(candidate_id=candidate.id)
        candidate.profile = profile
        candidate.identity = candidate.identity or CandidateIdentity()
    previous = profile.education[0] if profile.education else {}
    profile.skills = canonical_skills(edit.skills)
    profile.total_years_experience = edit.total_years_experience
    profile.education = (
        [
            {
                "level": edit.education_level.value,
                "field": previous.get("field"),
                "institution": previous.get("institution"),
            }
        ]
        if edit.education_level is not None
        else []
    )
    profile.languages = canonical_languages(edit.languages)
    profile.certifications = edit.certifications
    profile.manually_edited = True

    # The old embedding describes the old profile: drop it, then recompute if possible.
    # Without one the semantic component is simply "n/a" rather than stale.
    profile.embedding = None
    profile.embedding_model = None
    if llm is not None:
        try:
            profile.embedding = to_bytes(llm.embed([embedding_text_for_profile(profile)])[0])
            profile.embedding_model = llm.embedding_model
        except (LLMUnavailable, LLMInvalidOutput):
            pass
    candidate.status = CandidateStatus.READY
    candidate.status_reason = None
    return profile
