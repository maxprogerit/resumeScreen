"""Generate SYNTHETIC resumes (PDF + DOCX) with ground truth. No real personal data.

All names are invented combinations, emails use example.com, phones use the fictional
555-01xx range. Run:

    python -m scripts.generate_resumes --count 30 --out data/synthetic --seed 42
"""

import argparse
import json
import random
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from pathlib import Path

FIRST_NAMES = [
    "Alex", "Maria", "Jordan", "Priya", "Chen", "Olga", "Samir", "Elena", "Tomas", "Aisha",
    "Lukas", "Mei", "Daniel", "Sofia", "Kofi", "Hanna", "Ravi", "Julia", "Omar", "Nina",
]  # fmt: skip
LAST_NAMES = [
    "Vance", "Okafor", "Lindqvist", "Marchetti", "Tanaka", "Novak", "Haddad", "Brennan",
    "Kowalski", "Reyes", "Fischer", "Adeyemi", "Petrov", "Larsen", "Moreau", "Singh",
]  # fmt: skip

TRACKS: dict[str, dict[str, list[str]]] = {
    "backend": {
        "core": ["Python", "FastAPI", "PostgreSQL", "Docker", "REST", "SQLAlchemy"],
        "extra": ["Django", "Flask", "Redis", "Kafka", "Kubernetes", "AWS", "RabbitMQ", "Git"],
        "titles": ["Backend Engineer", "Python Developer", "Software Engineer"],
    },
    "data": {
        "core": ["Python", "SQL", "Pandas", "scikit-learn", "Airflow"],
        "extra": ["Spark", "Snowflake", "AWS", "Docker", "PyTorch", "Machine Learning", "Git"],
        "titles": ["Data Engineer", "Data Scientist", "ML Engineer"],
    },
    "frontend": {
        "core": ["JavaScript", "TypeScript", "React", "HTML", "CSS"],
        "extra": ["Vue", "Node.js", "GraphQL", "Figma", "Git", "Docker"],
        "titles": ["Frontend Developer", "UI Engineer", "Web Developer"],
    },
    "devops": {
        "core": ["Docker", "Kubernetes", "Terraform", "Linux", "CI/CD"],
        "extra": ["AWS", "Python", "Azure", "Google Cloud", "Git", "Kafka"],
        "titles": ["DevOps Engineer", "Platform Engineer", "SRE"],
    },
}
# Written the way people really write them, to exercise the synonym map.
ALIASES = {
    "Kubernetes": ["Kubernetes", "k8s"],
    "PostgreSQL": ["PostgreSQL", "Postgres"],
    "JavaScript": ["JavaScript", "JS"],
    "Node.js": ["Node.js", "NodeJS"],
    "Machine Learning": ["Machine Learning", "ML"],
    "Google Cloud": ["Google Cloud", "GCP"],
    "AWS": ["AWS", "Amazon Web Services"],
}
COMPANIES = [
    "Northwind Labs", "Brightloop", "Quillstone Systems", "Harbor Analytics", "Pinecrest Digital",
    "Lumen Works", "Cobalt Freight", "Mapleleaf Software", "Orbit Retail Tech", "Kestrel Cloud",
]  # fmt: skip
SCHOOLS = ["Eastfield University", "Riverbend Institute of Technology", "Lakeshore College"]
EDUCATION = [
    ("Bachelor of Science", "bachelor"),
    ("Master of Science", "master"),
    ("PhD", "phd"),
]
CERTS = ["AWS Certified Developer", "Certified Kubernetes Administrator", "Scrum Master (PSM I)"]
LANGS = ["English", "German", "Spanish", "French", "Russian"]


@dataclass
class SyntheticCandidate:
    key: str
    name: str
    email: str
    phone: str
    track: str
    skills: list[str]
    roles: list[dict[str, str]]  # title, company, start (YYYY-MM), end (YYYY-MM|present), bullet
    degree: str
    degree_level: str
    field_of_study: str
    school: str
    languages: list[str]
    certifications: list[str]
    years: float
    salutation: str | None = None  # gendered wording used by the fairness twins
    pronoun: str | None = None
    skill_spelling: dict[str, str] = field(default_factory=dict)


def _fmt_month(idx: int) -> str:
    return f"{idx // 12}-{idx % 12 + 1:02d}"


def _spell(c: SyntheticCandidate, skill: str) -> str:
    return c.skill_spelling.get(skill, skill)


def generate_candidates(
    count: int, seed: int = 42, today: date | None = None
) -> list[SyntheticCandidate]:
    rng = random.Random(seed)
    today = today or date.today()
    out: list[SyntheticCandidate] = []
    for i in range(count):
        track = rng.choice(list(TRACKS))
        spec = TRACKS[track]
        core = [s for s in spec["core"] if rng.random() < 0.85]
        extra = rng.sample(spec["extra"], k=rng.randint(1, min(4, len(spec["extra"]))))
        skills = list(dict.fromkeys(core + extra))
        spelling = {s: rng.choice(ALIASES[s]) for s in skills if s in ALIASES}

        n_roles = rng.randint(1, 4)
        roles: list[dict[str, str]] = []
        cursor = today.year * 12 + today.month - 1
        total_months = 0
        for r in range(n_roles):
            length = rng.randint(10, 48)
            end_idx = cursor
            start_idx = end_idx - length
            role_skills = rng.sample(skills, k=min(len(skills), 3))
            roles.append(
                {
                    "title": rng.choice(spec["titles"]),
                    "company": rng.choice(COMPANIES),
                    "start": _fmt_month(start_idx),
                    "end": "present" if r == 0 else _fmt_month(end_idx),
                    "bullet": "Delivered production features using "
                    + ", ".join(_spell_skill(spelling, s) for s in role_skills)
                    + f" in a team of {rng.randint(3, 12)}.",
                }
            )
            total_months += length
            cursor = start_idx - rng.randint(0, 3)
        degree, level = rng.choices(EDUCATION, weights=[6, 3, 1])[0]
        langs = ["English"] + rng.sample(LANGS[1:], k=rng.randint(0, 2))
        out.append(
            SyntheticCandidate(
                key=f"demo-{i + 1:03d}",
                name=f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
                email=f"candidate{i + 1:03d}@example.com",
                phone=f"+1-555-01{rng.randint(0, 99):02d}",
                track=track,
                skills=skills,
                roles=roles,
                degree=degree,
                degree_level=level,
                field_of_study=rng.choice(
                    ["Computer Science", "Information Systems", "Mathematics"]
                ),
                school=rng.choice(SCHOOLS),
                languages=langs,
                certifications=rng.sample(CERTS, k=rng.randint(0, 1)),
                years=round(total_months / 12, 1),
                skill_spelling=spelling,
            )
        )
    return out


def _spell_skill(spelling: dict[str, str], skill: str) -> str:
    return spelling.get(skill, skill)


def make_twin(
    c: SyntheticCandidate, name: str, salutation: str, pronoun: str
) -> SyntheticCandidate:
    """Same qualifications, different name and gendered wording (for fairness tests)."""
    return replace(c, name=name, salutation=salutation, pronoun=pronoun)


def render_text(c: SyntheticCandidate) -> str:
    """The resume as plain text (ASCII only so core PDF fonts can render it)."""
    head = f"{c.salutation + ' ' if c.salutation else ''}{c.name}"
    lines = [head, f"{c.email} | {c.phone}", ""]
    summary = f"{c.track.capitalize()} professional with {c.years:g} years of experience."
    if c.pronoun:
        summary += f" {c.pronoun.capitalize()} enjoys mentoring colleagues."
    lines += ["SUMMARY", summary, "", "EXPERIENCE"]
    for r in c.roles:
        lines += [f"{r['title']} - {r['company']}", f"{r['start']} - {r['end']}", r["bullet"], ""]
    lines += [
        "EDUCATION",
        f"{c.degree} in {c.field_of_study}, {c.school}",
        "",
        "SKILLS",
        ", ".join(_spell(c, s) for s in c.skills),
        "",
        "LANGUAGES",
        ", ".join(c.languages),
    ]
    if c.certifications:
        lines += ["", "CERTIFICATIONS", ", ".join(c.certifications)]
    return "\n".join(lines)


def pdf_bytes(text: str) -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    for line in text.split("\n"):
        pdf.multi_cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def docx_bytes(text: str) -> bytes:
    import io

    from docx import Document

    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def write_pdf(text: str, path: Path) -> None:
    path.write_bytes(pdf_bytes(text))


def write_docx(text: str, path: Path) -> None:
    path.write_bytes(docx_bytes(text))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic resumes (PDF/DOCX)")
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--out", type=Path, default=Path("data/synthetic"))
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    candidates = generate_candidates(args.count, args.seed)
    for i, c in enumerate(candidates):
        text = render_text(c)
        if i % 2 == 0:
            write_pdf(text, args.out / f"{c.key}.pdf")
        else:
            write_docx(text, args.out / f"{c.key}.docx")
    manifest = args.out / "ground_truth.json"
    manifest.write_text(
        json.dumps(
            {"synthetic": True, "candidates": [asdict(c) for c in candidates]},
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {len(candidates)} synthetic resumes + ground_truth.json to {args.out}")


if __name__ == "__main__":
    main()
