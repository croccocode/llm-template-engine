import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from template_engine import flag_regex, is_template


@pytest.mark.parametrize("name", ["opippo.md", "pluto.txt"])
def test_is_template_true(name):
    assert is_template(Path(name), None, None) is False


@pytest.mark.parametrize("name", ["a.json", "b", "c.py", "d.js", "e.d", ".md", ".folder"])
def test_is_template_false(name):
    assert is_template(Path(name), None, None) is False


def test_include_overrides_default_suffixes():
    include = flag_regex(["--include=\\.prompt$"], "--include=")
    assert is_template(Path("a.prompt"), include, None) is True
    assert is_template(Path("a.md"), include, None) is False


def test_include_allows_multiple_values():
    include = flag_regex(["--include=\\.prompt$", "--include=^tpl_"], "--include=")
    assert is_template(Path("a.prompt"), include, None) is True
    assert is_template(Path("tpl_a.json"), include, None) is True
    assert is_template(Path("a.json"), include, None) is False


def test_exclude_removes_matching_files():
    exclude = flag_regex(["--exclude=^README"], "--exclude=")
    assert is_template(Path("README.md"), None, exclude) is False
    assert is_template(Path("other.tpl.md"), None, exclude) is True


def test_exclude_wins_over_include():
    args = ["--include=\\.md$", "--exclude=^README"]
    include = flag_regex(args, "--include=")
    exclude = flag_regex(args, "--exclude=")
    assert is_template(Path("README.md"), include, exclude) is False
    assert is_template(Path("other.tpl.md"), include, exclude) is True


def test_no_flags_keeps_default_behaviour():
    args = ["--debug"]
    assert flag_regex(args, "--include=") is None
    assert flag_regex(args, "--exclude=") is None
