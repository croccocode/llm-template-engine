"""Render di template *.tpl.md con MiniJinja, condiviso fra gli hook.

Gli adapter (`.claude/hooks/render_template.py` per Claude Code,
`.github/hooks/render_template.py` per Copilot CLI) parlano protocolli
diversi ma usano tutti questo render.

Any *.tpl.md anywhere in the repo can be rendered: includes resolve first
against the template's own directory (local partials, short names), then
fall back to the project root (e.g. `{% include "README.md" %}`). Path
traversal (`..`) outside those two roots is rejected by the loader.

Oltre agli include, un template puo' interpolare l'output di uno script bash
con `{{ sh("scripts/now.sh") }}`: stessa risoluzione dei path degli include,
stdout dello script al posto della chiamata. E' esecuzione di codice
arbitrario per progetto: il template *e'* il codice, come per gli hook.
"""

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
    """Primo file esistente fra `roots`, nell'ordine. Come load_from_path,
    scarta i candidati che (via `..`) escono dalla root da cui partono."""
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


def make_sh(roots: list[Path]):
    """La funzione `sh(script, *args)` esposta ai template.

    Cerca lo script con le stesse regole degli include, lo esegue con bash e
    restituisce il suo stdout (senza il newline finale, per interpolarlo
    inline). Un'uscita != 0 e' un errore di render, non una stringa vuota
    silenziosa: meglio un prompt che non parte di uno che mente.
    """
    resolved_roots = [root.resolve() for root in roots]

    def sh(script: str, *args) -> str:
        path = resolve_in_roots(script, resolved_roots)
        if path is None:
            raise TemplateRenderError(
                f"script '{script}' non trovato in "
                f"{', '.join(str(root) for root in resolved_roots)}"
            )
        # Su Windows bash arriva da Git for Windows o da WSL; in entrambi i
        # casi sta nel PATH. Niente shell=True: gli argomenti restano tali.
        bash = shutil.which("bash")
        if bash is None:
            raise TemplateRenderError(
                f"'{script}': bash non trovato nel PATH"
            )
        try:
            result = subprocess.run(
                [bash, str(path), *(str(arg) for arg in args)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=SCRIPT_TIMEOUT_SECONDS,
                # cwd fissa: lo stesso template deve rendere uguale da
                # qualunque directory l'agente sia stato lanciato.
                cwd=PROJECT_ROOT,
            )
        except subprocess.TimeoutExpired:
            raise TemplateRenderError(
                f"script '{script}' non finito entro {SCRIPT_TIMEOUT_SECONDS}s"
            ) from None
        if result.returncode != 0:
            raise TemplateRenderError(
                f"script '{script}' uscito con codice {result.returncode}: "
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
