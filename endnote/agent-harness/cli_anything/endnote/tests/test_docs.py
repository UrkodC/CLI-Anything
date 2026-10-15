from __future__ import annotations

from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
REPO_SKILL = PKG.parents[3] / "skills" / "cli-anything-endnote" / "SKILL.md"
CODES = ["endnote_missing", "no_library", "many_libraries", "automation_denied", "pubmed_error",
         "output_exists", "output_is_input", "bad_docx", "no_pmids", "endnote_timeout", "applescript_error"]


def test_packaged_skill_covers_every_command_and_error_code():
    text = (PKG / "skills" / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: \"cli-anything-endnote\"")
    for word in ["status", "add", "find", "link", "--json", "Update Citations and Bibliography", *CODES]:
        assert word in text, word


@pytest.mark.skipif(not REPO_SKILL.exists(), reason="not running inside the CLI-Anything repo")
def test_repo_root_skill_is_identical_to_packaged_copy():
    assert REPO_SKILL.read_text(encoding="utf-8") == (PKG / "skills" / "SKILL.md").read_text(encoding="utf-8")


def test_readme_exists_with_install_and_test_sections():
    text = (PKG / "README.md").read_text(encoding="utf-8")
    for heading in ["## Install", "## Use", "## Tests"]:
        assert heading in text


def test_docs_have_no_em_dashes():
    for path in [PKG / "README.md", PKG / "skills" / "SKILL.md", PKG.parents[1] / "ENDNOTE.md"]:
        assert "—" not in path.read_text(encoding="utf-8"), path
