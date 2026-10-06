"""Self-tests for fill_checklist.py — run directly, not part of the main
`python -m pytest` sweep (this lives under .claude/skills/, outside
pyproject.toml's testpaths=["modules","shared"]):

    python3 -m pytest .claude/skills/qa-analyze/assets/tests/test_fill_checklist.py -v
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ASSETS_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = ASSETS_DIR / "qa-checklist-template.html"
SAMPLE_DATA_PATH = Path(__file__).resolve().parent / "sample_data.json"

_spec = importlib.util.spec_from_file_location("fill_checklist", ASSETS_DIR / "fill_checklist.py")
fill_checklist = importlib.util.module_from_spec(_spec)
sys.modules["fill_checklist"] = fill_checklist
_spec.loader.exec_module(fill_checklist)


@pytest.fixture
def template_text():
    return TEMPLATE_PATH.read_text(encoding="utf-8")


@pytest.fixture
def sample_data():
    return json.loads(SAMPLE_DATA_PATH.read_text(encoding="utf-8"))


def _block(text, tag):
    # strip comments first — the template's own leading instruction comment
    # mentions "<style>"/"<script>" as prose, which false-matches a naive regex
    # (documented gotcha, see docs/agent/qa-html-checklist.md)
    clean = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    m = re.search(r"<%s>.*?</%s>" % (tag, tag), clean, re.S)
    assert m, "no <%s> block found" % tag
    return m.group(0)


def test_full_render_balanced_and_no_leftover_placeholders(template_text, sample_data):
    out = fill_checklist.build(sample_data, template_text)
    fill_checklist.verify_balanced(out)  # raises on failure


def test_style_and_script_reused_byte_for_byte(template_text, sample_data):
    out = fill_checklist.build(sample_data, template_text)
    assert _block(out, "style") == _block(template_text, "style")
    # script is byte-for-byte except the one sanctioned substitution: STORAGE_KEY
    out_script = _block(out, "script").replace("'PAY-101-checklist-v1'", "'{{TASK_KEY}}-checklist-v1'")
    assert out_script == _block(template_text, "script")


def test_task_key_and_title_substituted(template_text, sample_data):
    out = fill_checklist.build(sample_data, template_text)
    assert "PAY-101" in out
    assert "Provider X" in out
    assert "'PAY-101-checklist-v1'" in out
    assert "{{TASK_KEY}}" not in out
    assert "{{" not in out.split("<style>")[0]  # header area fully filled


def test_all_test_cases_and_priorities_present(template_text, sample_data):
    out = fill_checklist.build(sample_data, template_text)
    assert out.count('<details class="tc"') == 3  # TC-01, TC-02, TC-03
    assert 'data-priority="P0"' in out
    assert 'data-priority="P1"' in out
    assert 'data-priority="P2"' in out
    assert "TC-01" in out and "TC-02" in out and "TC-03" in out


def test_blocked_banner_rendered_for_partial(template_text, sample_data):
    out = fill_checklist.build(sample_data, template_text)
    assert "нет тестового аккаунта без прав" in out
    assert "PARTIALLY BLOCKED" in out


def test_html_special_chars_escaped(template_text, sample_data):
    out = fill_checklist.build(sample_data, template_text)
    # TC-02 title/steps deliberately contain <b>/& — must come out escaped in the
    # content area, never as live markup that could break the page.
    body = out.split("</header>", 1)[1].split("<script>", 1)[0]
    assert "&lt;b&gt;" in body
    assert "<b>&amp;</b>" not in body


def test_appendix_and_blockers_optional_sections(template_text, sample_data):
    out = fill_checklist.build(sample_data, template_text)
    assert "Резолв TBD" in out
    assert "Кто подтверждает статусы" in out  # open question
    assert "Сумма из хука перезаписывает" in out  # dev/po question


def test_missing_required_field_raises(template_text, sample_data):
    del sample_data["checklist"]
    with pytest.raises(ValueError, match="checklist"):
        fill_checklist.build(sample_data, template_text)


def test_minimal_data_no_tc_no_blockers_no_appendix(template_text):
    minimal = {
        "task_key": "PAY-0001",
        "title": "Minimal",
        "checklist": {"code_touches": "x", "essence": "y", "items": [{"text": "z"}]},
    }
    out = fill_checklist.build(minimal, template_text)
    fill_checklist.verify_balanced(out)
    assert out.count('<details class="tc"') == 0
    assert 'id="blockers"' not in out  # section dropped entirely — no blockers/questions given


def test_cli_main_writes_file(tmp_path, sample_data):
    data_path = tmp_path / "data.json"
    out_path = tmp_path / "checklist.html"
    data_path.write_text(json.dumps(sample_data), encoding="utf-8")

    rc = fill_checklist.main(["--data", str(data_path), "--output", str(out_path)])

    assert rc == 0
    assert out_path.exists()
    written = out_path.read_text(encoding="utf-8")
    fill_checklist.verify_balanced(written)
    assert "PAY-101" in written
