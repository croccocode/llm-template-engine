"""Render di template *.tpl.md con MiniJinja, condiviso fra gli hook.

Gli adapter (`.claude/hooks/render_template.py` per Claude Code,
`.github/hooks/render_template.py` per Copilot CLI) parlano protocolli
diversi ma usano tutti questo render.

Any *.tpl.md anywhere in the repo can be rendered: includes resolve first
against the template's own directory (local partials, short names), then
fall back to the project root (e.g. `{% include "README.md" %}`). Path
traversal (`..`) outside those two roots is rejected by the loader.
"""

import sys
import tempfile
import minijinja
from functools import lru_cache
from pathlib import Path

TEMPLATE_SUFFIX = ".tpl.md"
_PROBE_STRING = "—"  # em dash: cheap canary for the mis-decode bug below

PROJECT_ROOT = Path(__file__).resolve().parent


class TemplateRenderError(Exception):
    pass


def is_template(file_path: Path) -> bool:
    return file_path.name.endswith(TEMPLATE_SUFFIX)


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
