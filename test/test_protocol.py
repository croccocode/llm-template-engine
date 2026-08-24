import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from template_engine import sniff_protocol


@pytest.mark.parametrize("name", ["opippo.md", "pluto.txt"])
def test_is_template_true(name):
    assert is_template(Path(name)) is True


@pytest.mark.parametrize("name", ["a.json", "b", "c.py", "d.js", "e.d", ".md", ".folder"])
def test_is_template_false(name):
    assert is_template(Path(name)) is False
