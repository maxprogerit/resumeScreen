"""Evidence quotes for matched requirements.

Hard rule: a quote is only kept if it is a real substring of the resume text (whitespace
normalised). Quotes proposed by the LLM are verified here; anything that does not verify is
discarded and counted. Rule-based quotes are cut from the text itself, so they verify by
construction.
"""

import re
from dataclasses import asdict, dataclass
from typing import Any

from app.llm import prompts
from app.llm.client import LLMClient, LLMInvalidOutput, LLMUnavailable
from app.schemas import EDUCATION_RANK, EducationLevel, EvidenceResponse
from app.skills import find_all_skill, find_language

MAX_QUOTE_CHARS = 300
MIN_QUOTE_CHARS = 3

_EDU_KEYWORDS: dict[EducationLevel, str] = {
    EducationLevel.PHD: r"ph\.?\s?d|doctorate|doctoral",
    EducationLevel.MASTER: r"master'?s?|m\.?\s?sc|\bm\.?\s?eng|mba",
    EducationLevel.BACHELOR: r"bachelor'?s?|b\.?\s?sc|\bb\.?\s?eng|\bb\.?\s?a\b",
    EducationLevel.HIGH_SCHOOL: r"high school|secondary school|diploma",
}


@dataclass
class EvidenceItem:
    kind: str  # must_have_skill | nice_to_have_skill | language | education | experience
    requirement: str
    matched: bool
    quote: str | None = None
    start: int | None = None  # character span in the stored raw_text
    end: int | None = None
    source: str | None = None  # "llm" | "rule"
    note: str | None = None
    # Skills only: True if the skill appears outside a bare "Skills" list (i.e. in described
    # work), False if it is only listed there. A hint against keyword stuffing, not a score input.
    demonstrated: bool | None = None


def normalize_ws(text: str) -> str:
    return " ".join(text.split())


def locate_quote(raw_text: str, quote: str) -> tuple[int, int] | None:
    """Span of `quote` in `raw_text` if it is a real substring modulo whitespace, else None."""
    tokens = quote.split()
    if not MIN_QUOTE_CHARS <= len(normalize_ws(quote)) <= MAX_QUOTE_CHARS:
        return None
    pattern = r"\s+".join(re.escape(t) for t in tokens)
    m = re.search(pattern, raw_text)
    return (m.start(), m.end()) if m else None


_SKILLS_HEADING = re.compile(
    r"^\s*(technical\s+)?(skills|technologies|tech stack|tools|core competencies|key skills)[:\s]*$",
    re.IGNORECASE,
)
_LIST_SEPARATORS = re.compile(r"[,|;•·]")


def _is_list_line(raw_text: str, pos: int) -> bool:
    """True if the line containing `pos` is a bare list of items (a skills dump)."""
    line_start = raw_text.rfind("\n", 0, pos) + 1
    nl = raw_text.find("\n", pos)
    line = raw_text[line_start : len(raw_text) if nl == -1 else nl]
    if len(_LIST_SEPARATORS.findall(line)) >= 3:
        return True
    before = raw_text[:line_start].rstrip("\n").rsplit("\n", 1)[-1]
    return bool(_SKILLS_HEADING.match(before))


def _line_snippet(raw_text: str, start: int, end: int) -> tuple[int, int]:
    """Expand a match to its line; if the line is long, to a window around the match."""
    line_start = raw_text.rfind("\n", 0, start) + 1
    nl = raw_text.find("\n", end)
    line_end = len(raw_text) if nl == -1 else nl
    if line_end - line_start <= MAX_QUOTE_CHARS - 50:
        return line_start, line_end
    s = max(line_start, start - 120)
    e = min(line_end, end + 120)
    if s > line_start:  # snap to a word boundary without cutting into the match
        gap = raw_text.find(" ", s, start)
        s = gap + 1 if gap != -1 else s
    if e < line_end:
        gap = raw_text.rfind(" ", end, e)
        e = gap if gap != -1 else e
    return s, e


def _make_item(
    kind: str, requirement: str, raw_text: str, span: tuple[int, int], source: str
) -> EvidenceItem:
    s, e = span
    return EvidenceItem(
        kind=kind,
        requirement=requirement,
        matched=True,
        quote=normalize_ws(raw_text[s:e]),
        start=s,
        end=e,
        source=source,
    )


def _rule_span(
    raw_text: str,
    kind: str,
    requirement: str,
    education_have: EducationLevel | None,
    loose: bool,
) -> tuple[int, int] | None:
    match: re.Match[str] | None = None
    if kind in ("must_have_skill", "nice_to_have_skill"):
        occurrences = find_all_skill(raw_text, requirement, loose=loose)
        # Prefer an occurrence in described work over one in a bare skills list.
        match = next(
            (m for m in occurrences if not _is_list_line(raw_text, m.start())),
            occurrences[0] if occurrences else None,
        )
    elif kind == "language":
        match = find_language(raw_text, requirement)
    elif kind == "education" and education_have is not None:
        keywords = _EDU_KEYWORDS.get(education_have)
        if keywords:
            match = re.search(rf"(?<![A-Za-z])(?:{keywords})(?![A-Za-z])", raw_text, re.IGNORECASE)
    if match is None:
        return None
    return _line_snippet(raw_text, match.start(), match.end())


def _requirement_items(components: dict[str, Any]) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for name, kind in (("must_have", "must_have_skill"), ("nice_to_have", "nice_to_have_skill")):
        comp = components[name]
        items += [EvidenceItem(kind, s, True) for s in comp.get("matched", [])]
        items += [EvidenceItem(kind, s, False) for s in comp.get("missing", [])]
    le = components["language_education"]
    items += [EvidenceItem("language", s, True) for s in le.get("languages_matched", [])]
    items += [EvidenceItem("language", s, False) for s in le.get("languages_missing", [])]
    if "education_required" in le:
        need = EducationLevel(le["education_required"])
        have = le.get("education_have")
        ok = have is not None and EDUCATION_RANK[EducationLevel(have)] >= EDUCATION_RANK[need]
        items.append(EvidenceItem("education", need.value, ok))
    exp = components["experience"]
    if exp.get("score") is not None:
        years = exp.get("years")
        items.append(
            EvidenceItem(
                "experience",
                f">= {exp['required']:g} years",
                bool(years is not None and years >= exp["required"]),
                note=f"{years:g} years computed from role dates"
                if years is not None
                else "unknown",
            )
        )
    return items


def build_evidence(
    raw_text: str,
    components: dict[str, Any],
    llm: LLMClient | None,
    *,
    loose: bool = False,
) -> tuple[list[EvidenceItem], dict[str, Any]]:
    """Return evidence items for every requirement and metadata about how they were obtained."""
    items = _requirement_items(components)
    education_have_raw = components["language_education"].get("education_have")
    education_have = EducationLevel(education_have_raw) if education_have_raw else None
    wanting = [i for i in items if i.matched and i.kind != "experience"]
    discarded = 0
    llm_used = False

    if llm is not None and wanting:
        labels = sorted({i.requirement for i in wanting})
        try:
            response = llm.parse(
                system=prompts.EVIDENCE_SYSTEM,
                user=prompts.evidence_user(raw_text, labels),
                schema=EvidenceResponse,
            )
            llm_used = True
        except (LLMUnavailable, LLMInvalidOutput):
            response = None
        if response is not None:
            by_label: dict[str, str] = {}
            for q in response.items:
                by_label.setdefault(q.requirement.strip().lower(), q.quote)
            for item in wanting:
                quote = by_label.get(item.requirement.lower())
                if quote is None:
                    continue
                span = locate_quote(raw_text, quote)
                if span is None:
                    discarded += 1  # hallucinated or altered quote
                    continue
                verified = _make_item(item.kind, item.requirement, raw_text, span, "llm")
                item.quote, item.start, item.end, item.source = (
                    verified.quote,
                    verified.start,
                    verified.end,
                    "llm",
                )

    for item in wanting:
        if item.quote is not None:
            continue
        span = _rule_span(raw_text, item.kind, item.requirement, education_have, loose)
        if span is not None:
            rule = _make_item(item.kind, item.requirement, raw_text, span, "rule")
            item.quote, item.start, item.end, item.source = (
                rule.quote,
                rule.start,
                rule.end,
                "rule",
            )
        else:
            item.note = "matched in the extracted profile, but no supporting quote was found"

    for item in items:
        if item.matched and item.kind in ("must_have_skill", "nice_to_have_skill"):
            found = find_all_skill(raw_text, item.requirement, loose=loose)
            item.demonstrated = any(not _is_list_line(raw_text, m.start()) for m in found)
            if found and not item.demonstrated:
                item.note = "only listed in a skills list, not described in the work history"

    meta = {"llm_quotes_requested": llm_used, "quotes_discarded": discarded}
    return items, meta


def items_to_json(items: list[EvidenceItem]) -> list[dict[str, Any]]:
    return [asdict(i) for i in items]
