"""Resume -> structured profile (LLM) with deterministic post-processing."""

import re
from datetime import UTC, date, datetime

from app.llm import prompts
from app.llm.client import LLMClient, LLMInvalidOutput, LLMUnavailable
from app.schemas import EDUCATION_RANK, EducationLevel, ExtractedResume, ExtractedRole
from app.skills import canonical_languages, canonical_skills, skill_in_text


class ExtractionFailed(Exception):
    """Extraction could not produce a valid profile. `reason` is a short machine code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def extract_resume(llm: LLMClient, raw_text: str, *, loose: bool = False) -> ExtractedResume:
    """Strict-schema extraction. One retry on invalid output, then ExtractionFailed."""
    last = "extraction_invalid"
    for _attempt in range(2):
        try:
            resume = llm.parse(
                system=prompts.EXTRACT_RESUME_SYSTEM,
                user=prompts.extract_resume_user(raw_text),
                schema=ExtractedResume,
            )
        except LLMInvalidOutput:
            continue
        except LLMUnavailable:
            raise ExtractionFailed("llm_unavailable") from None
        return postprocess(resume, raw_text, loose=loose)
    raise ExtractionFailed(last)


# ----------------------------------------------------------------------------- post-processing

_MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for m in names
}
_PRESENT = {"present", "current", "now", "ongoing", "today", "till now", "to date"}


def parse_date(value: str | None, now: date) -> date | None:
    """Parse the date formats resumes use. Year-only dates map to 1 July."""
    if not value:
        return None
    s = value.strip().lower().strip(".")
    if s in _PRESENT:
        return now
    if m := re.fullmatch(r"(\d{4})[-/.](\d{1,2})", s):
        return _safe_date(int(m[1]), int(m[2]))
    if m := re.fullmatch(r"(\d{1,2})[-/.](\d{4})", s):
        return _safe_date(int(m[2]), int(m[1]))
    if m := re.fullmatch(r"([a-z]{3,9})\.?,?\s+(\d{4})", s):
        month = _MONTHS.get(m[1])
        return _safe_date(int(m[2]), month) if month else None
    if m := re.fullmatch(r"(\d{4})", s):
        return _safe_date(int(m[1]), 7)
    return None


def _safe_date(year: int, month: int) -> date | None:
    if 1950 <= year <= 2100 and 1 <= month <= 12:
        return date(year, month, 1)
    return None


def compute_years(roles: list[ExtractedRole], now: date) -> float | None:
    """Total non-overlapping years from role dates, or None if any dated role is unparseable.

    Deterministic on purpose: the number that drives the experience score is derived from
    the dates in the document, not from the model's own estimate.
    """
    spans: list[tuple[date, date]] = []
    for role in roles:
        if not role.start and not role.end:
            continue
        start, end = parse_date(role.start, now), parse_date(role.end, now)
        if start is None or end is None or end < start:
            return None
        spans.append((start, min(end, now)))
    if not spans:
        return None
    spans.sort()
    total_days = 0
    cur_start, cur_end = spans[0]
    for s, e in spans[1:]:
        if s <= cur_end:
            cur_end = max(cur_end, e)
        else:
            total_days += (cur_end - cur_start).days
            cur_start, cur_end = s, e
    total_days += (cur_end - cur_start).days
    return round(total_days / 365.25, 1)


def highest_education(resume: ExtractedResume) -> EducationLevel | None:
    levels = [e.level for e in resume.education]
    return max(levels, key=lambda lv: EDUCATION_RANK[lv]) if levels else None


def postprocess(
    resume: ExtractedResume, raw_text: str, now: date | None = None, *, loose: bool = False
) -> ExtractedResume:
    """Deterministic guards applied to model output.

    - skills that do not literally occur in the resume text are dropped (blocks hallucinated
      or injected skills that the document does not actually contain),
    - skills / languages are canonicalised via the synonym map,
    - years of experience come from role dates when parseable.
    """
    today = now or datetime.now(UTC).date()
    skills = [s for s in canonical_skills(resume.skills) if skill_in_text(raw_text, s, loose=loose)]
    years = compute_years(resume.roles, today)
    return resume.model_copy(
        update={
            "skills": skills,
            "languages": canonical_languages(resume.languages),
            "total_years_experience": years if years is not None else resume.total_years_experience,
        }
    )
