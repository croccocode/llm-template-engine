"""PreToolUse hook on Read: expands *.tpl.md template files with MiniJinja.

Fires on every Read. Non-template files are allowed through immediately
(cheap suffix check, no I/O). Template files are rendered and the Read is
denied with a redirect to the compiled sibling file.
"""

import json
import sys
import traceback
from pathlib import Path

TEMPLATE_SUFFIX = ".tpl.md"
COMPILED_SUFFIX = ".compiled.md"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_ROOT = PROJECT_ROOT / "prompts"


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


def render(file_path: Path) -> Path:
    import minijinja

    relative_name = file_path.resolve().relative_to(PROMPTS_ROOT.resolve()).as_posix()
    env = minijinja.Environment(loader=minijinja.load_from_path([str(PROMPTS_ROOT)]))
    try:
        rendered = env.render_template(relative_name)
    except minijinja.TemplateError as exc:
        raise TemplateRenderError(str(exc)) from exc

    compiled_path = file_path.with_name(file_path.name[: -len(TEMPLATE_SUFFIX)] + COMPILED_SUFFIX)
    compiled_path.write_text(rendered, encoding="utf-8")
    return compiled_path


def main():
    payload = json.load(sys.stdin)

    if payload.get("tool_name") != "Read":
        allow()

    file_path = Path(payload.get("tool_input", {}).get("file_path", ""))
    if not file_path.name.endswith(TEMPLATE_SUFFIX):
        allow()

    try:
        compiled_path = render(file_path)
    except ValueError:
        # .tpl.md fuori da prompts/: non sappiamo risolvere gli include relativi, lascialo passare grezzo.
        allow()
        return
    except TemplateRenderError as exc:
        deny(f"Errore nel render di '{file_path.name}': {exc}")
        return
    except Exception as exc:  # noqa: BLE001 - fail open: mai bloccare tutte le Read per un bug nell'hook
        print(f"render_template hook error: {exc}\n{traceback.format_exc()}", file=sys.stderr)
        allow()
        return

    deny(f"'{file_path.name}' e' un template sorgente. Leggi invece il file renderizzato: {compiled_path}")


if __name__ == "__main__":
    main()
