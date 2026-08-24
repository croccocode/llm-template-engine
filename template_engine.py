# /// script
# requires-python = "==3.14.0"
# dependencies = ["minijinja==2.22.0"]
# ///
# ^ PEP 723: uv provides interpreter and dependencies by itself


import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import traceback
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import minijinja

logger = logging.getLogger("lltpl")


class Tool(StrEnum):
    ClaudeCode = "claudecode"
    CopilotCLI = "copilot_cli"
    UNKOWN = "-"

SCRIPT_TIMEOUT_SECONDS = 30

# `cat <path>` and nothing else: one argument, optionally quoted, no flags and
# no shell metacharacters. The three groups are the quoting styles.
CAT_COMMAND = re.compile(
    r"""^\s*cat\s+(?:'(?P<single>[^']+)'|"(?P<double>[^"]+)"|(?P<bare>[^\s;|&<>()'"]+))\s*$"""
)

# What separates two commands in a chain. `|` and `>` are not here on purpose:
# a `cat` inside a pipeline or a redirection feeds something else, and swapping
# its argument would change what that something else receives. The group is
# capturing so that re.split keeps the separators and the command can be
# rebuilt as it was.
COMMAND_SEPARATOR = re.compile(r"(&&|\|\||;|\n)")

class TemplateRenderError(Exception):
    pass

@dataclass(frozen=True)
class ToolCall:
    is_read: bool  # whether tool_name is one of the host's file-reading tools
    file_path: str
    cwd: Path
    tool_args: dict = field(default_factory=dict)  # the host's original tool input
    path_key: str = ""  # which key of tool_args carries what the agent reads


def flag_regex(argv: list[str], flag: str) -> re.Pattern[str] | None:
    values = [arg.removeprefix(flag) for arg in argv if arg.startswith(flag)]
    if not values:
        return None
    return re.compile("|".join(values))


def is_template(file_path: Path, include: re.Pattern[str] | None, exclude: re.Pattern[str] | None) -> bool:
    if exclude is not None and exclude.search(file_path.name):
        return False
    if include is not None:
        return include.search(file_path.name) is not None
    
    # bool(file_path.stem) -> returns the file name without extension
    # this  handle weirdo case like ".md"
    # this is clearly a mental masturbation from Claude Code, but why not.
    return bool(file_path.stem) and file_path.suffix in (".md", ".txt")


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
        # We check if a file is_template only to "activate" the template engine. 
        return source
    return loader


def make_sh(tool_call: ToolCall):
    """The `sh(command)` function exposed to templates.

    Runs the command with `bash -c` from the session cwd and returns its
    stdout (without the trailing newline, so it can be interpolated inline).
    A non-zero exit is a render error, not a silent empty string: better a
    prompt that doesn't start than one that lies.
    """

    def sh(command: str) -> str:
        # On Windows bash comes from Git for Windows or from WSL; either way
        # it is on the PATH.
        bash = shutil.which("bash")
        if bash is None:
            raise TemplateRenderError("bash not found on the PATH")
        try:
            result = subprocess.run(
                [bash, "-c", command],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=SCRIPT_TIMEOUT_SECONDS,
                # fixed cwd: the same template must render identically no
                # matter which directory the agent was launched from.
                cwd=tool_call.cwd,
                # the return code is inspected below, to report it verbatim
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise TemplateRenderError(
                f"'{command}' did not finish within {SCRIPT_TIMEOUT_SECONDS}s"
            ) from None
        if result.returncode != 0:
            raise TemplateRenderError(
                f"'{command}' exited with code {result.returncode}: "
                f"{(result.stderr or '').strip()}"
            )
        return result.stdout.rstrip("\r\n")

    return sh


def make_eval():
    """The `eval(expression)` function exposed to templates.

    Evaluates a single Python expression and hands its value back to the
    template, so `{{ eval("2 ** 10") }}` interpolates 1024.

    """

    def _eval(expression: str):
        try:
            return eval(expression, {})  # the template *is* code
        except Exception as exc:  # any failure is a render failure
            raise TemplateRenderError(f"eval({expression!r}): {exc}") from exc

    return _eval


def make_exec():
    """The `exec(code)` function exposed to templates.

    `exec` returns nothing, so the values a snippet computes would be lost.
    The trick is to run it against a fresh namespace and return that namespace
    as a dict: minijinja resolves attribute access on a map by key, so the
    template gets the snippet's variables back as `ns.<name>`.

        {% set ns = exec("files = sorted(...)") %}
        {% for f in ns.files %}

    Dunder entries are dropped, `__builtins__` above all: it is injected into
    every exec namespace and is not something a template should walk.
    """

    def _exec(code: str) -> dict:
        namespace: dict = {}
        try:
            # dedent: in a template the snippet is usually indented to match
            # the surrounding markup, which on its own is an IndentationError.
            exec(textwrap.dedent(code), namespace)  # noqa: S102
        except Exception as exc:  # any failure is a render failure
            raise TemplateRenderError(f"exec(): {exc}") from exc
        return {
            name: value
            for name, value in namespace.items()
            if not name.startswith("__")
        }

    return _exec

def render(file_path: Path, tool_call: ToolCall) -> str:
    roots = [file_path.resolve().parent, tool_call.cwd]
    env = minijinja.Environment(loader=make_loader(roots))
    
    env.add_function("sh", make_sh(tool_call))
    env.add_function("eval", make_eval())
    env.add_function("exec", make_exec())

    try:
        return env.render_template(file_path.name)
    except minijinja.TemplateError as exc:
        raise TemplateRenderError(str(exc)) from exc


def write_rendered(file_path: Path, rendered: str) -> Path:
    """Parks the rendered template in a scratch file, the one the agent is then
    pointed at. The name is derived from the source path, so a template always
    lands on the same file instead of littering the temp dir, and it never ends
    in a template suffix, or reading it would trigger this very hook again."""
    digest = hashlib.sha256(str(file_path).encode("utf-8")).hexdigest()[:12]
    out_dir = Path(tempfile.gettempdir()) / "llm-template-engine"
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / f"{file_path.name}.{digest}.rendered"
    out_path.write_text(rendered, encoding="utf-8")
    return out_path


def rewrite_cat_command(command: str, rendered_path: Path) -> str:
    parts = COMMAND_SEPARATOR.split(command)
    for index, part in enumerate(parts):
        if CAT_COMMAND.match(part) is None:
            continue
        parts[index] = f' cat "{rendered_path}" '
        break
    return "".join(parts)


def claude_output(reason: str | None, updated_args: dict | None) -> str:
    payload = {"hookEventName": "PreToolUse", "permissionDecision": "allow"}
    if reason is not None:
        payload["permissionDecisionReason"] = reason
    if updated_args is not None:
        payload["updatedInput"] = updated_args
    return json.dumps({"hookSpecificOutput": payload}, ensure_ascii=False)


def copilot_output(reason: str | None, updated_args: dict | None) -> str:
    payload = {"permissionDecision": "allow"}
    if reason is not None:
        payload["permissionDecisionReason"] = reason
    if updated_args is not None:
        payload["modifiedArgs"] = updated_args
    return json.dumps(payload, ensure_ascii=False)

def sniff_protocol() -> Tool:
    # logger.debug("sniff_protocol: env list")
    # for key, value in os.environ.items():
    #     logger.debug("%s=%s", key, value)
    
    if "CLAUDE_PROJECT_DIR" in os.environ:
        return Tool.ClaudeCode
    elif "COPILOT_PROJECT_DIR" in os.environ:
        return Tool.CopilotCLI
    
    return Tool.UNKOWN


def parse_hook_read_payload(payload: dict) -> ToolCall:
    # handle both Copilot format and Claude Code format
    
    # claude: .tool_name
    # copilot: .toolName
    tool_name = payload.get("tool_name") or payload.get("toolName") or ""

    # the arguments of the call, to be given back rewritten
    # claude: .tool_input
    # copilot (native): .toolArgs is a jsonized string
    tool_args = payload.get("tool_input") or json.loads(payload.get("toolArgs") or "{}")

    # file path is messy
    # claude: .tool_input.file_path
    # copilot (native): .toolArgs is a jsonize string, the path is in .path
    # copilot (claude format): .tool_input.path
    path_key = ""
    file_name = ""
    for key in ("file_path", "path"):
        if tool_args.get(key):
            path_key = key
            file_name = tool_args[key]
            break

    is_read = tool_name.lower() in ("read", "view")

    # claude: .cwd
    # copilot: .cwd
    working_dir = payload.get("cwd", "") or ""

    # An agent can read a file with `cat` instead of the Read tool, on its own
    # or chained with other commands. Only the first cat of the chain is
    # handled: one call reads one template.
    if tool_name.lower() == "bash":
        command = tool_args.get("command") or ""
        for segment in COMMAND_SEPARATOR.split(command):
            match = CAT_COMMAND.match(segment)
            if match is None:
                continue
            is_read = True
            path_key = "command"
            file_name = match["single"] or match["double"] or match["bare"]
            logger.error(
                "bash command '%s' reads the file '%s' with cat: that cat will "
                "be pointed at the rendered template",
                command,
                file_name,
            )
            break

    if file_name and not Path(file_name).is_absolute():
        file_name = str(Path(working_dir) / file_name)

    return ToolCall(
        is_read=is_read,
        file_path=file_name,
        cwd=Path(working_dir),
        tool_args=tool_args,
        path_key=path_key,
    )

def read_payload() -> dict:
    # utf-8-sig: some shells (PowerShell) prepend a BOM on stdin.
    raw = sys.stdin.buffer.read().decode("utf-8-sig").strip()
    logger.debug("=== raw ===")
    logger.debug(raw)
    logger.debug("=== raw ===")
    return json.loads(raw) if raw else {}


def decide(call: ToolCall, include: re.Pattern[str] | None, exclude: re.Pattern[str] | None) -> tuple[str | None, dict | None]:
    if not call.is_read or not call.file_path:
        logger.debug("tool call is not file_read, PASS call=%s", call)
        return None, None

    file_path = Path(call.file_path)
    if not is_template(file_path, include, exclude):
        logger.debug("file is not a template, PASS call=%s", call)
        return None, None

    if not file_path.is_absolute():
        # Copilot may pass paths relative to the session cwd.
        file_path = Path(call.cwd or Path.cwd()) / file_path

    logger.debug("rendering template for file=%s", file_path)
    try:
        rendered = render(file_path, call)
    except TemplateRenderError as exc:
        logger.error("error parsing template, let the tool to read it error=%s", exc)
        return f"Error rendering '{file_path.name}': {exc}", None

    rendered_path = write_rendered(file_path, rendered)
    logger.debug("template rendered in file=%s", rendered_path)

    updated_args = dict(call.tool_args)
    if call.path_key == "command":
        updated_args["command"] = rewrite_cat_command(updated_args["command"], rendered_path)
    else:
        updated_args[call.path_key] = str(rendered_path)

    return (
        f"[llm-template-engine] '{file_path.name}' is a MiniJinja template: "
        f"the call reads its rendered version in '{rendered_path}' instead. "
        f"This is the project's own template engine at work, not an error."
    ), updated_args


if __name__ == "__main__":
    
    # debug logging
    log_level = logging.ERROR    
    if "--debug" in sys.argv[1:]:
        log_level = logging.DEBUG
        
    logging.basicConfig(
        filename="promptpl.log",
        level=log_level,
        format="%(asctime)s %(levelname)s %(message)s",
        force=True,
    )

    try:
        args = sys.argv[1:]
        include_regexp = flag_regex(args, "--include=")
        exclude_regexp = flag_regex(args, "--exclude=")
        
        protocol = sniff_protocol()
        if protocol == Tool.UNKOWN:
            raise RuntimeError("unknown protocol - where are you invoking this hook from?")
    
        logger.debug("protocol=%s", protocol)
        
        # parse the hook event payload
        # copilot can generate an event compatible with claude code
        # when configured with the hook name "PreToolUse"
        payload = read_payload()
        tool_call : ToolCall = parse_hook_read_payload(payload)
        logger.debug("tool_call=%s", tool_call)
        
        reason, updated_args = decide(tool_call, include_regexp, exclude_regexp)
        logger.debug("reason=%s", reason)
        logger.debug("updated_args=%s", updated_args)

        # empty stdout = no decision: with nothing to say about this call the
        # hook must stay silent, not approve it on the user's behalf
        if reason is not None or updated_args is not None:
            if protocol == Tool.ClaudeCode:
                print(claude_output(reason, updated_args))
            elif protocol == Tool.CopilotCLI:
                print(copilot_output(reason, updated_args))
        
    except Exception as mainexc:  # noqa: BLE001 - the hook must report *any* failure  
        print(f"render_template hook error: {mainexc}\n{traceback.format_exc()}", file=sys.stderr)
        # exit 2 
        #   -> on Claude Code it blocks the Read and feeds stderr to the model,
        #      so the agent sees the error instead of the raw template
        #   -> on Copilot every non-zero exit is a deny, and stderr reaches
        #      the user either way
        sys.exit(2)
    sys.exit(0)