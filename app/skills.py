"""Skill / language normalisation and deterministic text matching.

Everything that decides "does the candidate have skill X" goes through `canonical_skill`,
so the synonym map is the single place to extend.
"""

import re
from functools import lru_cache

# canonical name -> aliases (the canonical name itself is always an alias)
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "kubernetes": ("k8s", "kube"),
    "javascript": ("js", "ecmascript", "es6"),
    "typescript": ("ts",),
    "python": ("py", "python3", "python 3"),
    "postgresql": ("postgres", "psql", "postgre sql"),
    "mongodb": ("mongo", "mongo db"),
    "node.js": ("node", "nodejs", "node js"),
    "react": ("react.js", "reactjs", "react js"),
    "vue": ("vue.js", "vuejs", "vue js"),
    "angular": ("angular.js", "angularjs"),
    "go": ("golang",),
    "c#": ("c sharp", "csharp"),
    "c++": ("cpp", "c plus plus"),
    ".net": ("dotnet", ".net core", "dotnet core", "asp.net"),
    "aws": ("amazon web services",),
    "google cloud": ("gcp", "google cloud platform"),
    "azure": ("microsoft azure",),
    "ci/cd": ("cicd", "ci cd", "continuous integration", "continuous delivery"),
    "machine learning": ("ml",),
    "deep learning": ("dl",),
    "scikit-learn": ("sklearn", "scikit learn"),
    "pytorch": ("torch",),
    "tensorflow": ("tf",),
    "rest": ("rest api", "restful", "restful api", "rest apis"),
    "graphql": ("graph ql",),
    "sql": ("structured query language",),
    "docker": (),
    "terraform": (),
    "fastapi": ("fast api",),
    "django": (),
    "flask": (),
    "redis": (),
    "kafka": ("apache kafka",),
    "rabbitmq": ("rabbit mq",),
    "elasticsearch": ("elastic search",),
    "git": (),
    "linux": ("gnu/linux",),
    "pandas": (),
    "numpy": (),
    "java": (),
    "spring": ("spring boot", "springboot"),
    "ruby": (),
    "rails": ("ruby on rails", "ror"),
    "php": (),
    "laravel": (),
    "swift": (),
    "kotlin": (),
    "rust": (),
    "r": (),
    "html": ("html5",),
    "css": ("css3",),
    "sqlalchemy": ("sql alchemy",),
    "pytest": (),
    "airflow": ("apache airflow",),
    "spark": ("apache spark", "pyspark"),
    "snowflake": (),
    "figma": (),
}

# Aliases that are ordinary words or single letters: match them case-sensitively and only
# in their canonical spelling to avoid "go to market" -> Go, "R&D" -> R, "ts" noise.
_CASE_SENSITIVE: dict[str, str] = {"go": "Go", "r": "R", "swift": "Swift", "rust": "Rust"}
_AMBIGUOUS_SHORT = {"ts", "js", "py", "ml", "dl", "tf", "node", "kube", "mongo", "ror", "cpp"}

_ALIAS_TO_CANON: dict[str, str] = {}
for _canon, _aliases in _SYNONYMS.items():
    _ALIAS_TO_CANON[_canon] = _canon
    for _alias in _aliases:
        _ALIAS_TO_CANON[_alias] = _canon

_LANGUAGES: dict[str, tuple[str, ...]] = {
    "english": ("eng", "en"),
    "german": ("deutsch", "de"),
    "french": ("francais", "français", "fr"),
    "spanish": ("espanol", "español", "es"),
    "russian": ("ru",),
    "ukrainian": ("ua", "uk"),
    "polish": ("pl",),
    "chinese": ("mandarin", "zh"),
    "japanese": ("ja",),
    "portuguese": ("pt",),
    "italian": ("it",),
}
_LANG_ALIAS_TO_CANON = {a: c for c, al in _LANGUAGES.items() for a in (c, *al)}

_PUNCT_EDGE = re.compile(r"^[\s\-_/,;:()]+|[\s\-_/,;:()]+$")


def _basic(value: str) -> str:
    value = " ".join(value.strip().lower().split())
    return _PUNCT_EDGE.sub("", value)


def canonical_skill(value: str) -> str:
    base = _basic(value)
    return _ALIAS_TO_CANON.get(base, base)


def canonical_language(value: str) -> str:
    base = _basic(value)
    # "English (C1)" / "English - fluent" -> english
    first = re.split(r"[\s(\-–:,]", base, maxsplit=1)[0] if base else base
    return _LANG_ALIAS_TO_CANON.get(base) or _LANG_ALIAS_TO_CANON.get(first) or first


def canonical_skills(values: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for v in values:
        c = canonical_skill(v)
        if c:
            seen.setdefault(c)
    return list(seen)


def canonical_languages(values: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for v in values:
        c = canonical_language(v)
        if c:
            seen.setdefault(c)
    return list(seen)


def _aliases_for(canon: str) -> list[str]:
    aliases = [canon, *_SYNONYMS.get(canon, ())]
    return [a for a in aliases if a not in _AMBIGUOUS_SHORT or canon in _CASE_SENSITIVE]


@lru_cache(maxsize=2048)
def _skill_pattern(canon: str) -> re.Pattern[str]:
    parts: list[str] = []
    for alias in sorted(_aliases_for(canon), key=len, reverse=True):
        escaped = re.escape(alias).replace(r"\ ", r"\s+")
        if canon in _CASE_SENSITIVE and alias == canon:
            parts.append(re.escape(_CASE_SENSITIVE[canon]))
        else:
            parts.append(f"(?i:{escaped})")
    body = "|".join(parts)
    # Letters/digits/+/# on either side mean we are inside a longer token ("java" in
    # "javascript", "c" in "c++"). "R&D" must not count as the R language.
    tail = r"(?![A-Za-z0-9_+#])"
    if canon == "r":
        tail = r"(?![A-Za-z0-9_+#&/])"
    elif canon == "go":
        tail = r"(?![A-Za-z0-9_+#]|-to\b|\s+to\s+market)"
    return re.compile(rf"(?<![A-Za-z0-9_+#])(?:{body}){tail}")


@lru_cache(maxsize=2048)
def _loose_skill_pattern(canon: str) -> re.Pattern[str] | None:
    """For OCR text, where word boundaries and spaces are unreliable ("usingPython",
    "machinelearning"): no token boundaries, optional whitespace inside multi-word names.
    Names of one or two characters are too ambiguous without boundaries and never match."""
    parts = [
        re.escape(alias).replace(r"\ ", r"\s*")
        for alias in sorted(_aliases_for(canon), key=len, reverse=True)
        if len(alias) >= 3 and canon not in _CASE_SENSITIVE
    ]
    if not parts:
        return None
    guard = r"(?!(?i:script))" if canon == "java" else ""
    return re.compile(rf"(?i:{'|'.join(parts)}){guard}")


def find_skill(text: str, skill: str, *, loose: bool = False) -> re.Match[str] | None:
    """First occurrence of `skill` (any alias) in `text`. `loose=True` is for OCR text."""
    canon = canonical_skill(skill)
    if loose:
        pattern = _loose_skill_pattern(canon)
        if pattern is not None and (m := pattern.search(text)):
            return m
    return _skill_pattern(canon).search(text)


def skill_in_text(text: str, skill: str, *, loose: bool = False) -> bool:
    return find_skill(text, skill, loose=loose) is not None


def find_all_skill(text: str, skill: str, *, loose: bool = False) -> list[re.Match[str]]:
    """Every occurrence of `skill` (any alias) in `text`, in document order."""
    canon = canonical_skill(skill)
    pattern = _loose_skill_pattern(canon) if loose else None
    found = list((pattern or _skill_pattern(canon)).finditer(text))
    if not found and loose:
        found = list(_skill_pattern(canon).finditer(text))
    return found


@lru_cache(maxsize=256)
def _language_pattern(canon: str) -> re.Pattern[str]:
    aliases = [canon, *_LANGUAGES.get(canon, ())]
    # 2-letter codes are too ambiguous for free text; only match full names.
    names = [a for a in aliases if len(a) > 2]
    body = "|".join(re.escape(a) for a in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z]){body}(?![A-Za-z])", re.IGNORECASE)


def find_language(text: str, language: str) -> re.Match[str] | None:
    return _language_pattern(canonical_language(language)).search(text)
