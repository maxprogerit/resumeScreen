"""Fairness: name / gender / contact data must not influence scoring."""

import ast
import inspect
from pathlib import Path

import numpy as np

from app.models import Candidate, CandidateIdentity
from app.schemas import EducationLevel, Weights
from app.services import scoring
from app.services.matching import embedding_text_for_profile, rescore_job
from app.services.scoring import ScoringJob, ScoringProfile, combine, raw_components
from scripts.generate_resumes import generate_candidates, make_twin
from tests.conftest import Env
from tests.helpers import make_job, resume_files, upload

W = Weights(must_have=40, nice_to_have=10, experience=15, semantic=20, language_education=15)


def test_scoring_function_has_no_identity_inputs() -> None:
    """Structural guarantee: the only per-person input is ScoringProfile."""
    fields = set(ScoringProfile.__dataclass_fields__)
    assert fields == {
        "skills", "years_experience", "education_level", "languages", "embedding",
        "embedding_model",
    }  # fmt: skip
    params = set(inspect.signature(raw_components).parameters)
    assert params == {"profile", "job", "semantic_floor", "semantic_ceil"}


def test_scoring_module_does_not_import_models_or_identity() -> None:
    tree = ast.parse(Path(scoring.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not any(name.startswith("app.models") for name in imported)
    source = Path(scoring.__file__).read_text(encoding="utf-8").lower()
    for forbidden in ("display_name", "candidateidentity", "email", "phone", "gender"):
        assert forbidden not in source


def test_extraction_schema_has_no_protected_attributes() -> None:
    from app.schemas import ExtractedIdentity, ExtractedResume

    names = set(ExtractedIdentity.model_fields) | set(ExtractedResume.model_fields)
    for protected in (
        "gender",
        "sex",
        "age",
        "birth",
        "nationality",
        "marital",
        "photo",
        "address",
    ):
        assert not any(protected in n.split("_") for n in names)


def test_same_profile_different_identity_gives_identical_score() -> None:
    """Unit-level version of the required test: identity never reaches the scorer."""
    emb = np.array([0.6, 0.8], dtype=np.float32)
    job = ScoringJob(
        must_have=("python", "aws"), nice_to_have=("docker",), min_years=3.0, languages=("english",),
        education_level=EducationLevel.BACHELOR, embedding=emb, embedding_model="m",
    )  # fmt: skip

    def profile_for(_name: str, _gender: str) -> ScoringProfile:
        # The identity arguments exist only to show they are not part of the scoring input.
        return ScoringProfile(
            skills=frozenset({"python", "docker"}), years_experience=5.0,
            education_level=EducationLevel.BACHELOR, languages=frozenset({"english"}),
            embedding=emb, embedding_model="m",
        )  # fmt: skip

    scores = {
        combine(raw_components(profile_for(n, g), job, semantic_floor=0.2, semantic_ceil=0.8), W)[
            "total"
        ]
        for n, g in [("Maria Rossi", "female"), ("Mohamed Ali", "male"), ("Alex Kim", "nonbinary")]
    }
    assert len(scores) == 1


def test_pipeline_twins_with_different_names_and_gendered_text_score_identically(env: Env) -> None:
    job = make_job(env, must=["python", "kubernetes", "docker"], nice=["redis"], years=3)
    base = generate_candidates(1, seed=21)[0]
    twins = [
        base,
        make_twin(base, "Maria Gonzalez", "Ms.", "she"),
        make_twin(base, "Mohamed Al-Rashid", "Mr.", "he"),
        make_twin(base, "Zhang Wei", "Dr.", "they"),
    ]
    assert upload(env, job["id"], resume_files(twins)).status_code == 202

    items = env.client.get(f"/api/jobs/{job['id']}/ranking").json()["items"]
    assert len(items) == 4
    assert len({i["display_name"] for i in items}) == 4  # names really differ
    assert len({i["total_score"] for i in items}) == 1
    for comp in ("must_have", "nice_to_have", "experience", "semantic", "language_education"):
        assert len({i["components"][comp]["points"] for i in items}) == 1

    # The text sent to the embedding model for candidates is de-identified and identical.
    candidate_texts = [t for t in env.llm.embed_inputs if t.startswith("Skills:")]
    assert len(candidate_texts) == 4 and len(set(candidate_texts)) == 1
    joined = "\n".join(candidate_texts)
    for t in twins:
        assert t.name.split()[0] not in joined
        assert t.email not in joined


def test_changing_stored_identity_does_not_change_rescored_result(env: Env) -> None:
    job = make_job(env, must=["python", "kubernetes"])
    upload(env, job["id"], resume_files(generate_candidates(2, seed=5)))
    before = env.client.get(f"/api/jobs/{job['id']}/ranking").json()["items"]

    with env.session_factory() as db:
        for ident in db.query(CandidateIdentity).all():
            ident.display_name = "Completely Different Name"
            ident.email = "other@example.org"
            ident.phone = "000"
        db.commit()
        from app.models import Job
        from app.services.weights import load_weights

        j = db.query(Job).one()
        rescore_job(db, j, llm=None, weights=load_weights(db, env.settings), settings=env.settings)

    after = env.client.get(f"/api/jobs/{job['id']}/ranking").json()["items"]
    assert [(i["candidate_id"], i["total_score"]) for i in after] == [
        (i["candidate_id"], i["total_score"]) for i in before
    ]


def test_embedding_text_excludes_company_and_school() -> None:
    from app.models import CandidateProfile

    p = CandidateProfile(
        skills=["python"],
        roles=[{"title": "Engineer", "company": "Acme Corp", "summary": "Built APIs"}],
        education=[{"level": "bachelor", "field": "CS", "institution": "Eastfield University"}],
        languages=["english"],
        certifications=[],
    )
    text = embedding_text_for_profile(p)
    assert "Acme" not in text and "Eastfield" not in text
    assert "Built APIs" in text


def test_candidate_model_not_used_by_scoring_inputs() -> None:
    # Candidate holds raw_text and filename; ScoringProfile must not be constructible from them.
    assert "raw_text" not in ScoringProfile.__dataclass_fields__
    assert "source_filename" not in ScoringProfile.__dataclass_fields__
    assert Candidate.__tablename__ == "candidates"
