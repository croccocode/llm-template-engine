import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from template_engine import parse_hook_read_payload

PAYLOADS = Path(__file__).resolve().parent

@pytest.mark.parametrize(
    "filename",
    [
        "hook_pretool_input-claude-PreToolUse-claude_format.json",
        "hook_pretool_input-copilot-PreToolUse-claude_format.json",
        "hook_pretool_input-copilot-preToolUse-copilot_format.json",
        
        # claude in auto-mode does not use the Read tool, but Bash(cat)
        "hook_pretool_input-claude-PreToolUse-claude_format_bash.json"
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
