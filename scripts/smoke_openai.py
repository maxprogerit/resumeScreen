"""Live smoke test against the real OpenAI API, using SYNTHETIC resumes only.

    python -m scripts.smoke_openai            # needs OPENAI_API_KEY in the environment / .env

Checks that your key, chat model and embedding model work with this app's strict
structured-output schemas, and prints how well extraction matches the generator's ground
truth. It makes about 6 small API calls. Exit code 1 if any check fails.
"""

import sys

from app.config import get_settings
from app.llm.client import LLMInvalidOutput, LLMUnavailable
from app.llm.openai_client import OpenAIClient
from app.schemas import EvidenceResponse, ExplanationResponse
from app.services.evidence import build_evidence, locate_quote
from app.services.extraction import extract_resume
from app.services.matching import embedding_text_for_job
from app.services.requirements import extract_requirements
from app.services.scoring import combine, raw_components
from app.skills import canonical_skills
from scripts.generate_resumes import generate_candidates, render_text
from scripts.seed_demo import DEMO_JOB_TEXT, DEMO_JOB_TITLE

failures: list[str] = []


def check(ok: bool, message: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {message}")
    if not ok:
        failures.append(message)


def main() -> int:
    settings = get_settings()
    try:
        llm = OpenAIClient(settings)
    except LLMUnavailable:
        print("OPENAI_API_KEY is not set. Put it in .env or the environment and rerun.")
        return 2
    print(f"chat model: {llm.chat_model}   embedding model: {llm.embedding_model}\n")

    truth = generate_candidates(1, seed=101)[0]
    text = render_text(truth)

    try:
        resume = extract_resume(llm, text)
        check(True, "resume extraction returned a valid strict-schema object")
    except Exception as exc:  # noqa: BLE001
        check(False, f"resume extraction failed: {type(exc).__name__}")
        return 1
    expected = set(canonical_skills(truth.skills))
    got = set(resume.skills)
    recall = len(expected & got) / max(len(expected), 1)
    check(
        recall >= 0.7, f"skill recall {recall:.0%} (expected {sorted(expected)}, got {sorted(got)})"
    )
    check(
        resume.total_years_experience is not None
        and abs(resume.total_years_experience - truth.years) <= 1.5,
        f"years {resume.total_years_experience} vs ground truth {truth.years}",
    )
    check(len(resume.roles) == len(truth.roles), f"roles {len(resume.roles)} vs {len(truth.roles)}")
    check(resume.identity.display_name == truth.name, "display name extracted (display only)")

    reqs = extract_requirements(llm, DEMO_JOB_TITLE, DEMO_JOB_TEXT)
    check(reqs is not None, "requirement extraction returned a valid object")
    if reqs is not None:
        check("python" in reqs.must_have_skills, f"must-have skills: {reqs.must_have_skills}")
        check(reqs.min_years_experience == 3, f"min years: {reqs.min_years_experience}")

    try:
        vectors = llm.embed(["backend engineer python", embedding_text_for_job_stub()])
        check(
            len(vectors) == 2 and len(vectors[0]) == len(vectors[1]) > 100,
            f"embedding dim {len(vectors[0])}",
        )
    except (LLMUnavailable, LLMInvalidOutput) as exc:
        check(False, f"embeddings failed: {type(exc).__name__}")

    try:
        quotes = llm.parse(
            system="Return one verbatim quote per requirement.",
            user=f'<resume>\n{text}\n</resume>\n["python"]',
            schema=EvidenceResponse,
        )
        verified = [q for q in quotes.items if locate_quote(text, q.quote)]
        check(True, f"evidence call valid; {len(verified)}/{len(quotes.items)} quotes verified")
        llm.parse(system="Say hi in one sentence.", user="{}", schema=ExplanationResponse)
        check(True, "explanation schema accepted")
    except (LLMUnavailable, LLMInvalidOutput) as exc:
        check(False, f"evidence/explanation schema rejected: {type(exc).__name__}")

    print("\nFAILED" if failures else "\nAll live checks passed.")
    return 1 if failures else 0


def embedding_text_for_job_stub() -> str:
    from app.models import Job

    return embedding_text_for_job(Job(title=DEMO_JOB_TITLE, description_text=DEMO_JOB_TEXT))


# keep imports referenced for type checkers when the optional checks are trimmed
_ = (build_evidence, combine, raw_components)

if __name__ == "__main__":
    sys.exit(main())
