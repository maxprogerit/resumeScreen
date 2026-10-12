"""Pydantic schemas shared by the LLM layer, services and the API.

The LLM-facing models avoid field defaults and use `extra="forbid"` so they translate
to OpenAI strict JSON Schema. Range checks live in validators: a violation raises a
ValidationError, which the extraction service treats as "invalid output, retry once".
"""

import enum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator


class EducationLevel(enum.StrEnum):
    NONE = "none"
    HIGH_SCHOOL = "high_school"
    BACHELOR = "bachelor"
    MASTER = "master"
    PHD = "phd"


EDUCATION_RANK: dict[EducationLevel, int] = {
    EducationLevel.NONE: 0,
    EducationLevel.HIGH_SCHOOL: 1,
    EducationLevel.BACHELOR: 2,
    EducationLevel.MASTER: 3,
    EducationLevel.PHD: 4,
}


def _clean_list(items: list[str], *, max_items: int = 100, max_len: int = 100) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        value = " ".join(item.split())[:max_len]
        key = value.lower()
        if value and key not in seen:
            seen.add(key)
            out.append(value)
    return out[:max_items]


CleanList = Annotated[list[str], AfterValidator(_clean_list)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- resume extraction


class ExtractedRole(_Strict):
    title: str
    company: str | None
    start: str | None  # as written in the resume, e.g. "2019-03", "Mar 2019", "2019"
    end: str | None  # same, or "present"
    summary: str


class ExtractedEducation(_Strict):
    level: EducationLevel
    field: str | None
    institution: str | None


class ExtractedIdentity(_Strict):
    """Display-only. Deliberately has no gender, age, nationality, photo or address."""

    display_name: str | None
    email: str | None
    phone: str | None


class ExtractedResume(_Strict):
    identity: ExtractedIdentity
    skills: CleanList
    total_years_experience: float | None
    roles: list[ExtractedRole]
    education: list[ExtractedEducation]
    languages: CleanList
    certifications: CleanList

    @field_validator("total_years_experience")
    @classmethod
    def _years_in_range(cls, v: float | None) -> float | None:
        if v is not None and not 0 <= v <= 60:
            raise ValueError("total_years_experience out of range")
        return v

    @field_validator("roles")
    @classmethod
    def _cap_roles(cls, v: list[ExtractedRole]) -> list[ExtractedRole]:
        return v[:30]


# --------------------------------------------------------------------------- job requirements


class JobRequirements(_Strict):
    must_have_skills: CleanList
    nice_to_have_skills: CleanList
    min_years_experience: float | None
    languages: CleanList
    education_level: EducationLevel | None

    @field_validator("min_years_experience")
    @classmethod
    def _years_in_range(cls, v: float | None) -> float | None:
        if v is not None and not 0 <= v <= 50:
            raise ValueError("min_years_experience out of range")
        return v


# --------------------------------------------------------------------------- evidence / explanation


class EvidenceQuote(_Strict):
    requirement: str
    quote: str


class EvidenceResponse(_Strict):
    items: list[EvidenceQuote]


class ExplanationResponse(_Strict):
    summary: str


# --------------------------------------------------------------------------- weights


class Weights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    must_have: float = Field(ge=0, le=100)
    nice_to_have: float = Field(ge=0, le=100)
    experience: float = Field(ge=0, le=100)
    semantic: float = Field(ge=0, le=100)
    language_education: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def _some_positive(self) -> "Weights":
        if sum(self.model_dump().values()) <= 0:
            raise ValueError("at least one weight must be positive")
        return self
