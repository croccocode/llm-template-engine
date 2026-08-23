# /// script
# requires-python = ">=3.14"
# dependencies = ["minijinja>=2.22.0"]
# ///
# ^ PEP 723: uv provides interpreter and dependencies by itself, so installing
# this hook in another repo only takes `uv` and this single file — no venv, no
# pyproject, no pip. `uv run --script` deliberately ignores the project's
# pyproject.toml, so the dependency pin above is the only one that matters here.

"""Template engine and pre-tool-use hook, in a single file.

Rendering
---------
Any *.tpl.md anywhere in the repo can be rendered: includes resolve first
against the template's own directory (local partials, short names), then
fall back to the project root (e.g. `{% include "README.md" %}`). Path
traversal (`..`) outside those two roots is rejected by the loader.

Besides includes, a template can interpolate the output of a bash script
with `{{ sh("scripts/now.sh") }}`: same path resolution as includes, the
script's stdout replacing the call. This is arbitrary code execution per
project: the template *is* code, just like a hook.

Hook
----
Run as a script, this intercepts the reading of a `*.tpl.md` file, expands it
and returns the rendered content to the agent inside the denial reason.
Non-template files pass through immediately (suffix check, no I/O).

The logic is one and the same for Claude Code and Copilot CLI: only the host
*protocol* changes, described in the PROTOCOLS table below — what the input
keys are called, what shape the output JSON has, and whether the allow must be
declared or silence is enough.

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
import re
import shutil
import subprocess
import sys
import traceback
import minijinja
from functools import lru_cache
from pathlib import Path

TEMPLATE_SUFFIXES = (".md", ".txt")
RAW_TERMINATOR = re.compile(r"\{%[-+]?\s*endraw\s*[-+]?%\}")
SCRIPT_TIMEOUT_SECONDS = 30

PROJECT_ROOT = Path(__file__).resolve().parent


class TemplateRenderError(Exception):
    pass


def is_template(file_path: Path) -> bool:
    return bool(file_path.stem) and file_path.suffix in TEMPLATE_SUFFIXES


def resolve_in_roots(name: str, roots: list[Path]) -> Path | None:
    """First existing file among `roots`, in order. Like load_from_path, it
    discards candidates that (via `..`) escape the root they start from."""
    for root in roots:
        candidate = (root / name).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None


def make_loader(roots: list[Path]):
    # Custom loader instead of minijinja.load_from_path, for two reasons.
    # First, load_from_path mis-decodes non-ASCII bytes on Windows (em dashes
    # come back as "â€”"); reading the files ourselves with an explicit UTF-8
    # decode avoids that. Second, it cannot tell templates from plain files,
    # and only *.tpl.md may be evaluated (see below). We re-implement its
    # traversal guard: reject any candidate that resolves outside its root.
    resolved_roots = [root.resolve() for root in roots]

    def loader(name: str):
        candidate = resolve_in_roots(name, resolved_roots)
        if candidate is None:
            return None
        source = candidate.read_text(encoding="utf-8")
        # Only *.tpl.md files are templates. Everything else is inlined
        # verbatim: a plain .md must not have its {{ }} / {% %} evaluated.
        # Otherwise including any markdown file would be arbitrary code
        # execution, since `sh()` is in scope during the render.
        if is_template(Path(name)):
            return source
        # A literal raw-terminator in the text would close our wrapper early
        # and leave the tail to be parsed. So each one is stepped around:
        # close the raw block, emit the terminator as a plain string
        # expression, reopen. This keeps documents that *talk about* the
        # engine inlinable -- this project's own README is one of them.
        escaped = RAW_TERMINATOR.sub(
            lambda match: "{%% endraw %%}{{ %s }}{%% raw %%}" % json.dumps(match.group(0)),
            source,
        )
        return f"{{% raw %}}{escaped}{{% endraw %}}"

    return loader


@lru_cache(maxsize=1)
def _bash_is_wsl(bash: str) -> bool:
    """On Windows `bash` may be Git for Windows (native Windows paths work
    as they are, mounted directly) or the WSL stub in System32 (it wants
    Linux paths like /mnt/c/...; a path with backslashes arrives mangled and
    the file "does not exist"). We tell them apart once by asking bash
    itself who it is."""
    if sys.platform != "win32":
        return False
    try:
        result = subprocess.run(
            [bash, "-c", "uname -r"],
            capture_output=True,
            text=True,
            timeout=SCRIPT_TIMEOUT_SECONDS,
        )
        return "microsoft" in result.stdout.lower()
    except Exception:  # noqa: BLE001 - not knowing isn't fatal, assume Git Bash
        return False


def _to_bash_path(path: Path, bash: str) -> str:
    """Converts a Windows path into the form the resolved `bash` expects as a
    command line argument (the WSL stub wants /mnt/<drive>/..., Git for
    Windows accepts the native path)."""
    if not _bash_is_wsl(bash):
        return str(path)
    drive, tail = os.path.splitdrive(path)
    if not drive:
        return str(path)
    return f"/mnt/{drive[0].lower()}{tail.replace(chr(92), '/')}"


def make_sh(roots: list[Path]):
    """The `sh(script, *args)` function exposed to templates.

    Looks the script up with the same rules as includes, runs it with bash
    and returns its stdout (without the trailing newline, so it can be
    interpolated inline). A non-zero exit is a render error, not a silent
    empty string: better a prompt that doesn't start than one that lies.
    """
    resolved_roots = [root.resolve() for root in roots]

    def sh(script: str, *args) -> str:
        path = resolve_in_roots(script, resolved_roots)
        if path is None:
            raise TemplateRenderError(
                f"script '{script}' not found in "
                f"{', '.join(str(root) for root in resolved_roots)}"
            )
        # On Windows bash comes from Git for Windows or from WSL; either way
        # it is on the PATH. No shell=True: arguments stay arguments.
        bash = shutil.which("bash")
        if bash is None:
            raise TemplateRenderError(
                f"'{script}': bash not found on the PATH"
            )
        try:
            result = subprocess.run(
                [bash, _to_bash_path(path, bash), *(str(arg) for arg in args)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=SCRIPT_TIMEOUT_SECONDS,
                # fixed cwd: the same template must render identically no
                # matter which directory the agent was launched from.
                cwd=PROJECT_ROOT,
            )
        except subprocess.TimeoutExpired:
            raise TemplateRenderError(
                f"script '{script}' did not finish within {SCRIPT_TIMEOUT_SECONDS}s"
            ) from None
        if result.returncode != 0:
            raise TemplateRenderError(
                f"script '{script}' exited with code {result.returncode}: "
                f"{(result.stderr or '').strip()}"
            )
        return result.stdout.rstrip("\r\n")

    return sh


def render(file_path: Path) -> str:
    roots = [file_path.resolve().parent, PROJECT_ROOT]
    env = minijinja.Environment(loader=make_loader(roots))
    env.add_function("sh", make_sh(roots))
    try:
        return env.render_template(file_path.name)
    except minijinja.TemplateError as exc:
        raise TemplateRenderError(str(exc)) from exc


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