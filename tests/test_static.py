import re
from pathlib import Path

from tests.conftest import Env

STATIC = Path(__file__).parent.parent / "app" / "static"


def test_index_is_served_with_security_headers(env: Env) -> None:
    r = env.client.get("/")
    assert r.status_code == 200 and "Resume" in r.text
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert env.client.get("/js/app.js").status_code == 200
    assert env.client.get("/css/app.css").status_code == 200


def test_frontend_never_renders_data_as_html() -> None:
    """Uploaded resume content must only ever reach the DOM through textContent."""
    for js in STATIC.rglob("*.js"):
        source = js.read_text(encoding="utf-8")
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            assert banned not in source, f"{banned} used in {js.name}"


def test_frontend_has_no_inline_scripts_or_styles() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)
    assert " style=" not in html and "<style" not in html


def test_design_tokens_present() -> None:
    css = (STATIC / "css" / "app.css").read_text(encoding="utf-8")
    for token in (
        "--bg: #14110A", "--panel: #1D190F", "--panel-active: #272114", "--border: #2F2916",
        "--border-strong: #433A1F", "--text: #F6F1E2", "--text-muted: #B8AE8A",
        "--text-dim: #7D7551", "--accent: #FFD84D", "--warn: #FF9F43", "--danger: #FF5C5C",
        "--ok: #7CFFB2",
    ):  # fmt: skip
        assert token in css
    assert "gradient" not in css and "box-shadow" not in css and "text-shadow" not in css


def test_decision_support_wording_is_in_the_ui() -> None:
    js = "".join(p.read_text(encoding="utf-8") for p in (STATIC / "js").rglob("*.js"))
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "A human makes the hiring decision" in js
    assert "A human makes the hiring decision" in html
