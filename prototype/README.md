# llm-template-engine — prototype

Prototipo di template engine per prompt LLM: un hook `preToolUse` intercetta ogni lettura di file `.tpl.md`, li espande con **MiniJinja** (include risolti prima nella cartella del file, poi nella root del progetto; `sh("…")` per interpolare l'output di uno script bash) e restituisce il contenuto già renderizzato direttamente nel messaggio di `deny`, senza scrivere file compilati su disco.

Lo stesso motore gira su due agenti: **Claude Code** e **GitHub Copilot CLI**. Un solo hook serve entrambi: cambia il protocollo, non la logica.

## Requisiti

Solo [uv](https://docs.astral.sh/uv/). Nient'altro: né Python installato, né venv, né `pip install`.

L'hook gira con `uv run --script` e dichiara le sue dipendenze in un header [PEP 723](https://peps.python.org/pep-0723/), quindi uv si procura interprete (`>=3.14`) e MiniJinja da sé al primo avvio. Per installarlo in un altro repo bastano `hooks/render_template.py`, `template_engine.py` accanto e il file di config dell'host: nessun `pyproject.toml` richiesto nel repo di destinazione, che può anche non essere un progetto Python.

Il primo render paga il provisioning una volta sola (~0,7 s a cache vuota per la sola MiniJinja; qualche decina di secondi se uv deve anche scaricare l'interprete), poi l'overhead è ~50 ms per chiamata. Conviene scaldare la cache fuori banda, prima di avviare l'agente — su Copilot un timeout dell'hook è fail-closed:

```powershell
echo '{}' | uv run --script hooks\render_template.py --protocol claude
```

## Struttura

```
template_engine.py              # render MiniJinja
hooks/render_template.py        # hook unico: tabella PROTOCOLS + decisione
prompts/main.tpl.md             # template di prova
prompts/_partials/…             # partial incluse
scripts/now.sh                  # script di prova: data e ora di sistema
.claude/settings.json               # registra l'hook: --protocol claude
.claude/commands/run-prompt.md      # slash command /run-prompt
.github/hooks/render-template.json  # registra l'hook: --protocol copilot
.github/agents/run-prompt.agent.md  # custom agent (equivalente della slash command)
```

Leggere stdin, filtrare il suffisso, chiamare il render e gestire gli errori è codice comune. Dell'host dipendono solo tre cose, che stanno nel dizionario `PROTOCOLS`: i nomi delle chiavi in input, la forma del JSON in output, e se l'allow va dichiarato o basta il silenzio.

Chi ci sta invocando lo dice l'host stesso via `--protocol` (o `LTE_HOOK_PROTOCOL`), perché i due file di config sono comunque separati. Non lo deduciamo dal payload se non come fallback: la forma è ambigua, perché Copilot supporta anche un formato *VS Code compatible* con le stesse chiavi `tool_name` / `tool_input` di Claude.

## Script shell nei template

Oltre agli include, un template può interpolare l'output di uno script bash:

```jinja
{{ sh("scripts/now.sh") }}
```

`sh(script, *args)` risolve il path con le **stesse regole degli include** (prima la cartella del template, poi la root del progetto; `..` fuori da entrambe è scartato), esegue lo script con `bash` — cwd fissa sulla root, così lo stesso template rende uguale da qualunque directory sia partito l'agente — e sostituisce la chiamata con il suo **stdout**, senza il newline finale. Gli argomenti extra arrivano allo script come `$1`, `$2`, … e non passano da una shell: niente `shell=True`, niente riquoting.

Se lo script manca, esce ≠ 0, o supera `SCRIPT_TIMEOUT_SECONDS` (30 s), il render **fallisce** e l'agente riceve l'errore con lo stderr dentro, al posto del template. Un prompt che non parte è meglio di uno che mente su un buco silenzioso.

Su Windows `bash` è quello di Git for Windows (o WSL), preso dal `PATH`. Attenzione al `%Z` di `date`: su Git for Windows torna vuoto, `scripts/now.sh` usa `%z`.

È esecuzione di codice arbitrario a ogni lettura di un `.tpl.md`, per progetto e senza conferma — esattamente come un hook. Un template *è* codice: trattalo come tale in review e non renderizzare template che non hai scritto tu.

## Claude Code

`PreToolUse` con `matcher: "Read"`. Riceve `tool_name` / `tool_input.file_path`, risponde con `hookSpecificOutput.permissionDecision` = `allow` oppure `deny` + `permissionDecisionReason`.

## Copilot CLI

Config in `.github/hooks/*.json` (repo) o `~/.copilot/hooks/*.json` (utente), con comandi separati per `bash` e `powershell`. Differenze rispetto a Claude:

| | Claude Code | Copilot CLI |
|---|---|---|
| evento | `PreToolUse` | `preToolUse` |
| registrazione | `.claude/settings.json` | `.github/hooks/*.json` (`version: 1`) |
| matcher per tool | sì (`matcher`) | no: l'hook vede tutte le tool call e filtra da sé |
| tool di lettura | `Read` | `view` |
| campo del path | `tool_input.file_path` | `toolArgs.path` |
| tipo di `toolArgs` | oggetto | **stringa JSON** (oggetto nell'SDK) |
| allow | JSON esplicito | stdout vuoto, exit 0 |
| deny | JSON annidato in `hookSpecificOutput` | `{"permissionDecision":"deny","permissionDecisionReason":…}` piatto, su una riga |
| errori dell'hook | fail-open | **fail-closed**: exit ≠ 0 = deny |

Il comando invoca l'hook con un path **relativo** (`hooks/render_template.py`): Copilot non espone una variabile tipo `${CLAUDE_PROJECT_DIR}`, ma esegue l'hook con la cwd sulla directory del progetto — quella che contiene il `.github/` da cui ha letto il config. Verificato lanciando `copilot` sia da `prototype/` sia dalla directory padre: in entrambi i casi l'hook scatta. Non serve quindi cercare la root del progetto.

Due conseguenze pratiche su Copilot:

- L'hook cattura ogni eccezione ed esce sempre `0`, e anche il comando nel JSON termina con `exit 0`: un bug qui bloccherebbe *tutte* le tool call della sessione. Vale anche per il caso "host sconosciuto": stdout vuoto, che sia su Copilot sia su Claude significa "procedi".
- Il contenuto renderizzato viaggia nel `permissionDecisionReason`, come su Claude. Non è solo parità estetica: su Copilot CLI `additionalContext` di `preToolUse` non viene inoltrato al modello ([copilot-cli#2585](https://github.com/github/copilot-cli/issues/2585)), quindi il `deny` è l'unico canale documentato per far arrivare testo nostro all'agente.

Alternative possibili su Copilot, non usate qui per mantenere la parità con Claude: `modifiedArgs` per dirottare `view` su un file temporaneo già renderizzato, o un hook `postToolUse` che sostituisce `modifiedResult.textResultForLlm` (l'agente vedrebbe una lettura riuscita invece di un rifiuto).

## Slash command

Copilot CLI **non** supporta slash command custom: `.github/prompts/*.prompt.md` funziona in VS Code ma non nella CLI ([#618](https://github.com/github/copilot-cli/issues/618), [#1113](https://github.com/github/copilot-cli/issues/1113)). L'equivalente più vicino è un **custom agent** in `.github/agents/*.agent.md` (o `~/.copilot/agents/`), invocabile con `/agent` e selezionabile in automatico dal modello. Qui `run-prompt.agent.md` fa la stessa cosa di `.claude/commands/run-prompt.md`.

## Provare

```powershell
# Claude Code
claude            # poi: /run-prompt

# Copilot CLI
copilot           # poi: /agent → run-prompt

# hook a mano
'{"toolName":"view","toolArgs":"{\"path\":\"prompts/main.tpl.md\"}"}' |
  uv run --script hooks\render_template.py --protocol copilot
```

Se `view` viene negato con dentro il template espanso (partial e README inclusi, data e ora di sistema al posto della chiamata a `sh`), l'hook funziona.
