from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scripts.generate_resumes import (
    SyntheticCandidate,
    docx_bytes,
    generate_candidates,
    pdf_bytes,
    render_text,
)
from tests.conftest import Env
from tests.fakes import skills_requirements


def make_job(
    env: Env,
    must: list[str] | None = None,
    nice: list[str] | None = None,
    years: float | None = None,
) -> dict[str, Any]:
    reqs = skills_requirements(must or ["python"], nice or [], years)
    response = env.client.post(
        "/api/jobs",
        json={
            "title": "Backend Engineer (demo)",
            "description_text": "We need a backend engineer with Python experience.",
            "requirements": reqs.model_dump(mode="json"),
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def resume_files(cands: list[SyntheticCandidate]) -> list[tuple[str, tuple[str, bytes, str]]]:
    files = []
    for i, c in enumerate(cands):
        text = render_text(c)
        if i % 2 == 0:
            files.append(("files", (f"{c.key}.pdf", pdf_bytes(text), "application/pdf")))
        else:
            files.append(("files", (f"{c.key}.docx", docx_bytes(text), "application/octet-stream")))
    return files


def upload(env: Env, job_id: str, files: list[tuple[str, tuple[str, bytes, str]]]) -> Any:
    return env.client.post(f"/api/jobs/{job_id}/candidates", files=files)


def upload_synthetic(
    env: Env, job_id: str, count: int = 3, seed: int = 7
) -> list[SyntheticCandidate]:
    cands = generate_candidates(count, seed)
    response = upload(env, job_id, resume_files(cands))
    assert response.status_code == 202, response.text
    return cands


def row_count(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model)) or 0
