"""Hook pre-tool-use unico per Claude Code e Copilot CLI.

Intercetta la lettura di un file `*.tpl.md`, lo espande con MiniJinja
(`template_engine.py`) e restituisce il contenuto renderizzato all'agente
dentro il motivo del rifiuto. I file non-template passano subito (check sul
suffisso, nessuna I/O).

La logica e' una sola: cambia solo il *protocollo* dell'host, descritto nella
tabella PROTOCOLS qui sotto — come si chiamano le chiavi in input, che forma
ha il JSON in output, e se l'allow va dichiarato o basta il silenzio.

Quale protocollo usare lo dice l'host che ci invoca, perche' i due file di
config sono comunque separati: `--protocol claude` da `.claude/settings.json`,
`--protocol copilot` da `.github/hooks/render-template.json`. In mancanza si
prova a dedurlo dalla forma del payload, che pero' e' ambigua: Copilot ha
anche un formato "VS Code compatible" con le stesse chiavi di Claude.

Su qualunque errore inatteso si esce 0 senza output. Non e' pignoleria: i
command hook `preToolUse` di Copilot sono *fail-closed*, un exit != 0
bloccherebbe ogni tool call della sessione.
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
        return None  # stdout vuoto = nessuna decisione, Copilot procede
    return json.dumps(
        {"permissionDecision": decision, "permissionDecisionReason": reason},
        ensure_ascii=False,
    )


PROTOCOLS = {
    "claude": {
        # Chiavi del payload in ingresso.
        "tool_name_keys": ("tool_name",),
        "tool_args_keys": ("tool_input",),
        # Tool di lettura file da intercettare (confronto in minuscolo).
        "read_tools": {"read"},
        # Chiavi dove cercare il path dentro gli argomenti del tool.
        "path_keys": ("file_path",),
        # Come si scrive la decisione su stdout. None = non stampare nulla.
        "output": _claude_output,
    },
    "copilot": {
        "tool_name_keys": ("toolName", "tool_name"),
        # `toolArgs` e' una *stringa* JSON nei command hook della CLI, un
        # oggetto nell'SDK: parse_tool_args() gestisce entrambi.
        "tool_args_keys": ("toolArgs", "tool_input", "tool_args"),
        # `view` e' il tool nativo; gli altri sono alias difensivi.
        "read_tools": {"view", "read", "read_file", "str_replace_editor"},
        # Copilot passa `path`; gli altri sono alias difensivi.
        "path_keys": ("path", "file_path", "filePath", "absolute_path"),
        "output": _copilot_output,
    },
}


def pick_protocol(argv: list[str]) -> dict | None:
    """--protocol dalla riga di comando, poi env, poi niente."""
    name = os.environ.get("LTE_HOOK_PROTOCOL")
    for index, arg in enumerate(argv):
        if arg == "--protocol" and index + 1 < len(argv):
            name = argv[index + 1]
        elif arg.startswith("--protocol="):
            name = arg.split("=", 1)[1]
    if not name:
        return None
    protocol = PROTOCOLS.get(name.lower())
    if protocol is None:  # typo nel config: dillo, non cadere zitto sullo sniffing
        print(f"protocollo '{name}' sconosciuto, provo a dedurlo dal payload",
              file=sys.stderr)
    return protocol


def sniff_protocol(payload: dict) -> dict | None:
    """Fallback: le chiavi camelCase sono solo di Copilot. Le snake_case sono
    ambigue (Claude, ma anche Copilot in formato VS Code) e le trattiamo come
    Claude, che e' l'unico dei due a *pretendere* un output esplicito."""
    if "toolName" in payload or "toolArgs" in payload:
        return PROTOCOLS["copilot"]
    if "tool_name" in payload or "tool_input" in payload:
        return PROTOCOLS["claude"]
    return None


def first_value(source: dict, keys: tuple[str, ...]):
    return next((source[key] for key in keys if source.get(key)), None)


def parse_tool_args(payload: dict, protocol: dict) -> dict:
    args = first_value(payload, protocol["tool_args_keys"])
    if isinstance(args, str):  # command hook di Copilot: stringa JSON
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return {}
    return args if isinstance(args, dict) else {}


def read_payload() -> dict:
    # utf-8-sig: alcune shell (PowerShell) prependono un BOM su stdin.
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
        # Copilot puo' passare path relativi alla cwd della sessione.
        file_path = Path(payload.get("cwd") or Path.cwd()) / file_path

    try:
        rendered = render(file_path)
    except TemplateRenderError as exc:
        return "deny", f"Errore nel render di '{file_path.name}': {exc}"

    return "deny", (
        f"'{file_path.name}' e' un template sorgente. "
        f"Eccoti il contenuto del file: {rendered}"
    )


def main():
    sys.stdout.reconfigure(encoding="utf-8")  # il rendered puo' contenere non-ASCII
    payload = read_payload()

    protocol = pick_protocol(sys.argv[1:]) or sniff_protocol(payload)
    if protocol is None:
        return  # host sconosciuto: stdout vuoto, che ovunque vale "procedi"

    line = protocol["output"](*decide(payload, protocol))
    if line is not None:
        print(line)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - mai uscire != 0: su Copilot sarebbe un deny
        print(f"render_template hook error: {exc}\n{traceback.format_exc()}", file=sys.stderr)
    sys.exit(0)
