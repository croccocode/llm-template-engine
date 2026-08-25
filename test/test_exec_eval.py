import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from template_engine import TemplateRenderError, ToolCall, render

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "exec_eval"

TOOL_CALL = ToolCall(
    is_read=True,
    file_path=FIXTURES / "main.md",
    cwd=FIXTURES,
)


def test_exec_returns_namespace_to_the_template():
    rendered = render(FIXTURES / "main.md", TOOL_CALL)

    assert "- a.md" in rendered
    assert "- b.json" in rendered
    assert "- c.txt" in rendered


def test_include_is_resolved_next_to_the_template():
    assert "Always answer in English." in render(FIXTURES / "main.md", TOOL_CALL)


def test_eval_interpolates_the_expression_value():
    assert "2 ** 10 = 1024" in render(FIXTURES / "main.md", TOOL_CALL)


def test_broken_snippet_is_a_render_error():
    with pytest.raises(TemplateRenderError):
        render(FIXTURES / "broken.md", TOOL_CALL)

