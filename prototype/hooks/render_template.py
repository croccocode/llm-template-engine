# /// script
# requires-python = ">=3.14"
# dependencies = ["minijinja>=2.22.0"]
# ///
# ^ PEP 723: uv provides interpreter and dependencies by itself, so installing
# this hook in another repo only takes `uv` and these two files (the hook and
# `template_engine.py` next to it) — no venv, no pyproject, no pip.
#
# minijinja is NOT needed by this file: it is imported by `template_engine`,
# which we import below. It must still be declared here because
# `uv run --script` deliberately ignores the project's pyproject.toml. If you
# bump it there without bumping it here the hook won't complain: it exits 0
# (see the bottom) and .tpl.md files reach the agent raw. Two places, keeping
# them in sync is manual.

"""Single pre-tool-use hook for Claude Code and Copilot CLI.

Intercepts the reading of a `*.tpl.md` file, expands it with MiniJinja
(`template_engine.py`) and returns the rendered content to the agent inside
the denial reason. Non-template files pass through immediately (suffix
check, no I/O).

The logic is one and the same: only the host *protocol* changes, described in
the PROTOCOLS table below — what the input keys are called, what shape the
output JSON has, and whether the allow must be declared or silence is enough.

Which protocol to use is stated by the host invoking us, since the two config
files are separate anyway: `--protocol claude` from `.claude/settings.json`,
`--protocol copilot` from `.github/hooks/render-template.json`. Failing that
we try to infer it from the payload shape, which is however ambiguous:
Copilot also has a "VS Code compatible" format with the same keys as Claude.

On any unexpected error we exit 0 with no output. This isn't fussiness:
Copilot's `preToolUse` command hooks are *fail-closed*, a non-zero exit would
block every tool call in the session.
"""

import json
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from template_engine import TemplateRenderError, is_template, render  # noqa: E402


def _claude_output(decision: str, reason: str | None) -> str:
    payload = {"hookEventName": "PreToolUse", "permissionDecision": decision}
    if reason is not None:
        payload["permissionDecisionReason"] = reason
    return json.dumps({"hookSpecificOutput": payload}, ensure_ascii=False)


def _copilot_output(decision: str, reason: str | None) -> str | None:
    if decision == "allow":
        return None  # empty stdout = no decision, Copilot proceeds
    return json.dumps(
        {"permissionDecision": decision, "permissionDecisionReason": reason},
        ensure_ascii=False,
    )


PROTOCOLS = {
    "claude": {
        # Keys of the incoming payload.
        "tool_name_keys": ("tool_name",),
        "tool_args_keys": ("tool_input",),
        # File-reading tools to intercept (compared lowercased).
        "read_tools": {"read"},
        # Keys to look the path up under, inside the tool arguments.
        "path_keys": ("file_path",),
        # How the decision is written to stdout. None = print nothing.
        "output": _claude_output,
    },
    "copilot": {
        "tool_name_keys": ("toolName", "tool_name"),
        # `toolArgs` is a JSON *string* in the CLI command hooks, an object in
        # the SDK: parse_tool_args() handles both.
        "tool_args_keys": ("toolArgs", "tool_input", "tool_args"),
        # `view` is the native tool; the others are defensive aliases.
        "read_tools": {"view", "read", "read_file", "str_replace_editor"},
        # Copilot passes `path`; the others are defensive aliases.
        "path_keys": ("path", "file_path", "filePath", "absolute_path"),
        "output": _copilot_output,
    },
}


def pick_protocol(argv: list[str]) -> dict | None:
    """--protocol from the command line, then env, then nothing."""
    name = os.environ.get("LTE_HOOK_PROTOCOL")
    for index, arg in enumerate(argv):
        if arg == "--protocol" and index + 1 < len(argv):
            name = argv[index + 1]
        elif arg.startswith("--protocol="):
            name = arg.split("=", 1)[1]
    if not name:
        return None
    protocol = PROTOCOLS.get(name.lower())
    if protocol is None:  # typo in the config: say so, don't fall silently back to sniffing
        print(f"unknown protocol '{name}', trying to infer it from the payload",
              file=sys.stderr)
    return protocol


def sniff_protocol(payload: dict) -> dict | None:
    """Fallback: camelCase keys belong to Copilot only. snake_case ones are
    ambiguous (Claude, but also Copilot in VS Code format) and we treat them
    as Claude, the only one of the two that *demands* an explicit output."""
    if "toolName" in payload or "toolArgs" in payload:
        return PROTOCOLS["copilot"]
    if "tool_name" in payload or "tool_input" in payload:
        return PROTOCOLS["claude"]
    return None


def first_value(source: dict, keys: tuple[str, ...]):
    return next((source[key] for key in keys if source.get(key)), None)


def parse_tool_args(payload: dict, protocol: dict) -> dict:
    args = first_value(payload, protocol["tool_args_keys"])
    if isinstance(args, str):  # Copilot command hook: JSON string
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return {}
    return args if isinstance(args, dict) else {}


def read_payload() -> dict:
    # utf-8-sig: some shells (PowerShell) prepend a BOM on stdin.
    raw = sys.stdin.buffer.read().decode("utf-8-sig").strip()
    return json.loads(raw) if raw else {}


def decide(payload: dict, protocol: dict) -> tuple[str, str | None]:
    tool_name = first_value(payload, protocol["tool_name_keys"]) or ""
    if tool_name.lower() not in protocol["read_tools"]:
        return "allow", None

    raw_path = first_value(parse_tool_args(payload, protocol), protocol["path_keys"])
    if not raw_path:
        return "allow", None

    file_path = Path(raw_path)
    if not is_template(file_path):
        return "allow", None
    if not file_path.is_absolute():
        # Copilot may pass paths relative to the session cwd.
        file_path = Path(payload.get("cwd") or Path.cwd()) / file_path

    try:
        rendered = render(file_path)
    except TemplateRenderError as exc:
        return "deny", f"Error rendering '{file_path.name}': {exc}"

    return "deny", (
        f"'{file_path.name}' is a source template. "
        f"Here is the content of the file: {rendered}"
    )


def main():
    sys.stdout.reconfigure(encoding="utf-8")  # the rendered output may contain non-ASCII
    payload = read_payload()

    protocol = pick_protocol(sys.argv[1:]) or sniff_protocol(payload)
    if protocol is None:
        return  # unknown host: empty stdout, which everywhere means "proceed"

    line = protocol["output"](*decide(payload, protocol))
    if line is not None:
        print(line)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - never exit != 0: on Copilot that's a deny
        print(f"render_template hook error: {exc}\n{traceback.format_exc()}", file=sys.stderr)
    sys.exit(0)
