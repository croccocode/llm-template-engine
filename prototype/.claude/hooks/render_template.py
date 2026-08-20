"""PreToolUse hook on Read: expands *.tpl.md template files with MiniJinja.

Fires on every Read. Non-template files are allowed through immediately
(cheap suffix check, no I/O). Template files are rendered and the Read is
denied with the rendered content embedded directly in the deny reason.

Any *.tpl.md anywhere in the repo can be rendered: includes resolve first
against the template's own directory (local partials, short names), then
fall back to the project root (e.g. `{% include "README.md" %}"). Path
traversal (`..`) outside those two roots is rejected by MiniJinja itself.
"""

import json
import sys
import tempfile
import traceback
import minijinja
from functools import lru_cache
from pathlib import Path

TEMPLATE_SUFFIX = ".tpl.md"
_PROBE_STRING = "—"  # em dash: cheap canary for the mis-decode bug below

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def allow():
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }
    }))
    sys.exit(0)


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


class TemplateRenderError(Exception):
    pass


def make_loader(roots: list[Path]):
    # Custom loader instead of minijinja.load_from_path: on this Windows
    # setup load_from_path mis-decodes non-ASCII bytes (e.g. em dashes turn
    # into "â€”"). Reading the files ourselves with an
    # explicit UTF-8 decode avoids that. We re-implement load_from_path's
    # traversal guard (reject any candidate that resolves outside its root).
    resolved_roots = [root.resolve() for root in roots]

    def loader(name: str):
        for root in resolved_roots:
            candidate = (root / name).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8")
        return None

    return loader


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
    try:
        return env.render_template(file_path.name)
    except minijinja.TemplateError as exc:
        raise TemplateRenderError(str(exc)) from exc


def main():
    payload = json.load(sys.stdin)

    if payload.get("tool_name") != "Read":
        allow()

    file_path = Path(payload.get("tool_input", {}).get("file_path", ""))
    if not file_path.name.endswith(TEMPLATE_SUFFIX):
        allow()

    try:
        rendered = render(file_path)
    except TemplateRenderError as exc:
        deny(f"Errore nel render di '{file_path.name}': {exc}")
        return
    except Exception as exc:  # noqa: BLE001 - fail open: mai bloccare tutte le Read per un bug nell'hook
        print(f"render_template hook error: {exc}\n{traceback.format_exc()}", file=sys.stderr)
        allow()
        return

    deny(f"'{file_path.name}' e' un template sorgente. Eccoti il contenuto del file: {rendered}")


if __name__ == "__main__":
    main()
