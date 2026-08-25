import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from template_engine import decide, flag_regex, is_template, parse_hook_read_payload

PAYLOADS = Path(__file__).resolve().parent

@pytest.mark.parametrize(
    "filename",
    [
        "hook_pretool_input-claude-PreToolUse-claude_format.json",
        "hook_pretool_input-copilot-PreToolUse-claude_format.json",
        "hook_pretool_input-copilot-preToolUse-copilot_format.json",
        
        # claude in auto-mode does not use the Read tool, but Bash(cat)
        "hook_pretool_input-claude-PreToolUse-claude_format_bash.json",

        # this is a concatenation of Bash(ls) and Bash(cat)
        "hook_pretool_input-claude-PreToolUse-claude_format_bash_complex.json",
    ],
)
def test_parse_pretool_hook_payload(filename):
    # test that we can "understand" the hook event payload
    # for any tool 
    payload = json.loads((PAYLOADS / filename).read_text(encoding="utf-8"))

    call = parse_hook_read_payload(payload)
    assert call.is_read is True
    assert call.file_path == Path("/Users/totomz/Desktop/test-llm-template/prompt.md")
    assert call.cwd == Path("/Users/totomz/Desktop/test-llm-template")


@pytest.mark.parametrize(
    "payload",
    [
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf ./build"},
            "cwd": "/Users/totomz/Desktop/test-llm-template",
        },
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": "/Users/totomz/Desktop/test-llm-template/prompt.md"},
            "cwd": "/Users/totomz/Desktop/test-llm-template",
        },
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": "/Users/totomz/Desktop/test-llm-template/main.py"},
            "cwd": "/Users/totomz/Desktop/test-llm-template",
        },
    ],
)
def test_a_call_we_do_not_handle_is_not_approved(payload):
    # the hook has nothing to say about these calls: no reason and no rewrite
    # means no output at all, so the call is never approved on the user's behalf
    call = parse_hook_read_payload(payload)

    reason, updated_args = decide(call, None, None)
    assert reason is None
    assert updated_args is None


def test_read_of_a_template_is_rewritten_to_the_rendered_file(tmp_path):
    template = tmp_path / "prompt.tpl.md"
    template.write_text("hello {{ 40 + 2 }}", encoding="utf-8")
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": str(template)},
        "cwd": str(tmp_path),
    }

    call = parse_hook_read_payload(payload)
    reason, updated_args = decide(call, None, None)

    assert reason is not None
    rendered_path = Path(updated_args["file_path"])
    assert rendered_path != template
    assert rendered_path.read_text(encoding="utf-8") == "hello 42"

    # reading the rendered file must never re-trigger the engine,
    # even with an --include regexp that matches the original name
    include = flag_regex(["--include=prompt"], "--include=")
    assert is_template(rendered_path, include, None) is False


def test_bash_cat_in_a_chain_is_rewritten_to_the_rendered_file(tmp_path):
    template = tmp_path / "prompt.tpl.md"
    template.write_text("hello {{ 40 + 2 }}", encoding="utf-8")
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": 'ls -la && echo "---PROMPT---" && cat prompt.tpl.md'},
        "cwd": str(tmp_path),
    }

    call = parse_hook_read_payload(payload)
    reason, updated_args = decide(call, None, None)

    assert reason is not None
    command = updated_args["command"]
    assert command.startswith('ls -la && echo "---PROMPT---" &&')
    assert "cat prompt.tpl.md" not in command

    rendered_path = Path(command.split('cat "')[1].split('"')[0])
    assert rendered_path.read_text(encoding="utf-8") == "hello 42"


def test_bash_cat_after_a_cd_is_not_handled(tmp_path):
    # `cd` moves the directory the cat resolves against: the hook cannot know
    # it, so the chain must be left alone and the cat reads the raw template
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "prompt.tpl.md").write_text("hello {{ 40 + 2 }}", encoding="utf-8")
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "cd docs && cat prompt.tpl.md"},
        "cwd": str(tmp_path),
    }

    call = parse_hook_read_payload(payload)
    assert call.is_read is False

    reason, updated_args = decide(call, None, None)
    assert reason is None
    assert updated_args is None


def test_read_of_a_missing_template_is_not_handled(tmp_path):
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": str(tmp_path / "does-not-exist.md")},
        "cwd": str(tmp_path),
    }

    call = parse_hook_read_payload(payload)
    reason, updated_args = decide(call, None, None)

    assert reason is None
    assert updated_args is None
