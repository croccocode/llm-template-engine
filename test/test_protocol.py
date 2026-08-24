import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from template_engine import decide, parse_hook_read_payload

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
    assert call.file_path == "/Users/totomz/Desktop/test-llm-template/prompt.md"
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
