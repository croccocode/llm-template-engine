"""Rendering of *.tpl.md templates with MiniJinja, shared by the hooks.

The adapters (`.claude/hooks/render_template.py` for Claude Code,
`.github/hooks/render_template.py` for Copilot CLI) speak different
protocols but all use this renderer.

Any *.tpl.md anywhere in the repo can be rendered: includes resolve first
against the template's own directory (local partials, short names), then
fall back to the project root (e.g. `{% include "README.md" %}`). Path
traversal (`..`) outside those two roots is rejected by the loader.

Besides includes, a template can interpolate the output of a bash script
with `{{ sh("scripts/now.sh") }}`: same path resolution as includes, the
script's stdout replacing the call. This is arbitrary code execution per
project: the template *is* code, just like a hook.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import minijinja
from functools import lru_cache
from pathlib import Path

TEMPLATE_SUFFIX = ".tpl.md"
_PROBE_STRING = "—"  # em dash: cheap canary for the mis-decode bug below
SCRIPT_TIMEOUT_SECONDS = 30

PROJECT_ROOT = Path(__file__).resolve().parent


class TemplateRenderError(Exception):
    pass


def is_template(file_path: Path) -> bool:
    return file_path.name.endswith(TEMPLATE_SUFFIX)


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
    # Custom loader instead of minijinja.load_from_path: on this Windows
    # setup load_from_path mis-decodes non-ASCII bytes (e.g. em dashes turn
    # into "â€”"). Reading the files ourselves with an
    # explicit UTF-8 decode avoids that. We re-implement load_from_path's
    # traversal guard (reject any candidate that resolves outside its root).
    resolved_roots = [root.resolve() for root in roots]

    def loader(name: str):
        candidate = resolve_in_roots(name, resolved_roots)
        return candidate.read_text(encoding="utf-8") if candidate else None

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


@lru_cache(maxsize=1)
def _native_loader_is_utf8_safe() -> bool:
    """minijinja.load_from_path is known to mis-decode non-ASCII bytes on
    Windows (e.g. an em dash on disk comes back as "â€”"), so we skip it
    there outright. Elsewhere we don't assume anything: we probe it once
    with a real file round-trip and only trust it if that comes back clean.
    """
    if sys.platform == "win32":
        return False
    try:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "probe.txt").write_text(_PROBE_STRING, encoding="utf-8")
            env = minijinja.Environment(loader=minijinja.load_from_path([tmp]))
            return env.render_template("probe.txt") == _PROBE_STRING
    except Exception:  # noqa: BLE001 - any probe failure means "don't trust it"
        return False


def render(file_path: Path) -> str:
    roots = [file_path.resolve().parent, PROJECT_ROOT]
    if _native_loader_is_utf8_safe():
        loader = minijinja.load_from_path([str(root) for root in roots])
    else:
        loader = make_loader(roots)
    env = minijinja.Environment(loader=loader)
    env.add_function("sh", make_sh(roots))
    try:
        return env.render_template(file_path.name)
    except minijinja.TemplateError as exc:
        raise TemplateRenderError(str(exc)) from exc
