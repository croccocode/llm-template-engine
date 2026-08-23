# /// script
# requires-python = "==3.14.0"
# dependencies = ["minijinja=2.22.0"]
# ///
# ^ PEP 723: uv provides interpreter and dependencies by itself, so installing


import json
import logging
import os
import re
import shutil
import subprocess
import sys
import traceback
import minijinja
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from enum import StrEnum

class Decision(StrEnum):
    PASS = "allow"
    """Reads the file normally."""
    
    PARSE = "deny"
    """Parse the file as a template."""

TEMPLATE_SUFFIXES = (".md", ".txt")
SCRIPT_TIMEOUT_SECONDS = 30
PROJECT_ROOT = None

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
    # Custom loader instead of minijinja.load_from_path:
    # - load_from_path mis-decodes non-ASCII bytes on Windows 
    #   (em dashes come back as "â€”");
    # - add 2 search roots: first try to resolve {% include %} relative to the current file
    #   then starts from the tool root
    resolved_roots = [root.resolve() for root in roots]

    def loader(name: str):
        candidate = resolve_in_roots(name, resolved_roots)
        if candidate is None:
            # file not found :(
            return None
        
        source = candidate.read_text(encoding="utf-8")
        
        # Follow recursively the template as per the minijinja default behavior
        # We check if a fle is_Template only to "activate" the template engine. 
        return source
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


def claude_output(decision: Decision, reason: str | None) -> str:
    payload = {"hookEventName": "PreToolUse", "permissionDecision": decision}
    if reason is not None:
        payload["permissionDecisionReason"] = reason
    return json.dumps({"hookSpecificOutput": payload}, ensure_ascii=False)


def copilot_output(decision: str, reason: str | None) -> str | None:
    if decision == Decision.PASS:
        return None  # empty stdout = no decision, Copilot proceeds
    return json.dumps(
        {"permissionDecision": decision, "permissionDecisionReason": reason},
        ensure_ascii=False,
    )


@dataclass(frozen=True)
class ToolCall:
    """Host-independent view of a PreToolUse payload: whatever the host calls
    its keys, `decide()` only ever sees these four fields."""

    tool_name: str
    is_read: bool  # whether tool_name is one of the host's file-reading tools
    file_path: str
    cwd: str


def sniff_protocol() -> str:
    if "CLAUDE_PROJECT_DIR" in os.environ:
        return "claude"
    
    return ""


def parse_claude(payload: dict) -> ToolCall:
    tool_name = payload.get("tool_name") or ""
    args = payload.get("tool_input")
    if not isinstance(args, dict):
        args = {}
    return ToolCall(
        tool_name=tool_name,
        is_read=tool_name.lower() == "read",
        file_path=args.get("file_path"),
        cwd=payload.get("cwd"),
    )


def parse_copilot(payload: dict) -> ToolCall:
    tool_name = payload.get("toolName") or payload.get("tool_name") or ""
    # `toolArgs` is a JSON *string* in the CLI command hooks, an object in the
    # SDK; the other key names are the "VS Code compatible" payload shape.
    args = payload.get("toolArgs") or payload.get("tool_input") or payload.get("tool_args")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}
    if not isinstance(args, dict):
        args = {}
    # `view` is the native reading tool and `path` its native argument; the
    # other names are defensive aliases across Copilot's payload shapes.
    return ToolCall(
        tool_name=tool_name,
        is_read=tool_name.lower() in {"view", "read", "read_file", "str_replace_editor"},
        file_path=next(
            (
                args[key]
                for key in ("path", "file_path", "filePath", "absolute_path")
                if args.get(key)
            ),
            None,
        ),
        cwd=payload.get("cwd"),
    )


def read_payload() -> dict:
    # utf-8-sig: some shells (PowerShell) prepend a BOM on stdin.
    raw = sys.stdin.buffer.read().decode("utf-8-sig").strip()
    return json.loads(raw) if raw else {}


def decide(call: ToolCall) -> tuple[Decision, str | None]:
    if not call.is_read or not call.file_path:
        logging.debug(f"tool call is not file_read, PASS call={call}")
        return Decision.PASS, None

    file_path = Path(call.file_path)
    if not is_template(file_path):
        logging.debug(f"file is not a template, PASS call={call}")
        return Decision.PASS, None
    
    if not file_path.is_absolute():
        # Copilot may pass paths relative to the session cwd.
        file_path = Path(call.cwd or Path.cwd()) / file_path

    logging.debug(f"rendering template for file={file_path}")
    try:
        rendered = render(file_path)
    except TemplateRenderError as exc:
        logging.error(f"error parsing template, let the tool to read it error={exc}")
        return Decision.PASS, f"Error rendering '{file_path.name}': {exc}"

    logging.debug(f"template rendered, send it to the tool")
    return Decision.PARSE, (
        f"'{file_path.name}' is a source template. "
        f"Here is the content of the file: {rendered}"
    )


def main():
    
    protocol = sniff_protocol()
    if protocol == "":
        raise RuntimeError("unknown protocol - where are you invoking this hook from?")

    payload = read_payload()
    tool_call : ToolCall
    if protocol == "claude":
        tool_call = parse_claude(payload)        
    elif protocol == "copilot":
        tool_call = parse_copilot(payload)
    else:
        raise RuntimeError("unknown protocol, nothing to parse")

    # noinspection PyShadowingNames
    # this is intended: the "root" is the root of the tool
    PROJECT_ROOT = Path(tool_call.cwd)
    logging.basicConfig(
        filename=PROJECT_ROOT / "lltpl.log",
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(message)s",
    )    
    logging.debug(f" detected protocol={protocol}")        
        
    decision, rendered_tpl = decide(tool_call)
    out = None
    if protocol == "claude":
        out = claude_output(decision, rendered_tpl)        
    elif protocol == "copilot":
        out = copilot_output(decision, rendered_tpl)

    # copilot use an empty response     
    if out is not None:
        print(out)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  
        print(f"render_template hook error: {exc}\n{traceback.format_exc()}", file=sys.stderr)
        # exit 2 
        #   -> on Claude Code it blocks the Read and feeds stderr to the model,
        #      so the agent sees the error instead of the raw template
        #   -> on Copilot every non-zero exit is a deny, and stderr reaches
        #      the user either way
        sys.exit(2)
    sys.exit(0)