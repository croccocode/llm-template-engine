# llm-template-engine — prototype

Prototype of a template engine for LLM prompts: a `preToolUse` hook intercepts every read of a `.tpl.md` file, expands it with **MiniJinja** (includes resolved first in the file's own directory, then in the project root; `sh("…")` to interpolate the output of a bash script) and returns the already-rendered content directly in the `deny` message, without writing compiled files to disk.

The same engine runs on two agents: **Claude Code** and **GitHub Copilot CLI**. A single hook serves both: the protocol changes, not the logic.

## Requirements

Only [uv](https://docs.astral.sh/uv/). Nothing else: no installed Python, no venv, no `pip install`.

The hook runs with `uv run --script` and declares its dependencies in a [PEP 723](https://peps.python.org/pep-0723/) header, so uv gets the interpreter (`>=3.14`) and MiniJinja by itself on first run. To install it in another repo all you need is `hooks/render_template.py`, `template_engine.py` next to it and the host's config file: no `pyproject.toml` required in the target repo, which need not even be a Python project.

The first render pays for provisioning once (~0.7 s with a cold cache for MiniJinja alone; a few tens of seconds if uv also has to download the interpreter), after which the overhead is ~50 ms per call. It's worth warming the cache out of band, before starting the agent — on Copilot a hook timeout is fail-closed:

```powershell
echo '{}' | uv run --script hooks\render_template.py --protocol claude
```

## Layout

```
template_engine.py              # MiniJinja rendering
hooks/render_template.py        # single hook: PROTOCOLS table + decision
prompts/main.tpl.md             # test template
prompts/_partials/…             # included partials
scripts/now.sh                  # test script: system date and time
.claude/settings.json               # registers the hook: --protocol claude
.claude/commands/run-prompt.md      # /run-prompt slash command
.github/hooks/render-template.json  # registers the hook: --protocol copilot
.github/agents/run-prompt.agent.md  # custom agent (slash command equivalent)
```

Reading stdin, filtering on the suffix, calling the renderer and handling errors is common code. Only three things depend on the host, and they live in the `PROTOCOLS` dictionary: the names of the input keys, the shape of the output JSON, and whether the allow must be declared or silence is enough.

Who is invoking us is stated by the host itself via `--protocol` (or `LTE_HOOK_PROTOCOL`), since the two config files are separate anyway. We don't infer it from the payload except as a fallback: the shape is ambiguous, because Copilot also supports a *VS Code compatible* format with the same `tool_name` / `tool_input` keys as Claude.

## Shell scripts in templates

Besides includes, a template can interpolate the output of a bash script:

```jinja
{{ sh("scripts/now.sh") }}
```

`sh(script, *args)` resolves the path with the **same rules as includes** (first the template's directory, then the project root; `..` outside both is discarded), runs the script with `bash` — cwd pinned to the root, so the same template renders identically no matter which directory the agent started from — and replaces the call with its **stdout**, without the trailing newline. Extra arguments reach the script as `$1`, `$2`, … and don't go through a shell: no `shell=True`, no requoting.

If the script is missing, exits ≠ 0, or exceeds `SCRIPT_TIMEOUT_SECONDS` (30 s), the render **fails** and the agent receives the error with the stderr inside it, instead of the template. A prompt that doesn't start is better than one that lies about a silent hole.

On Windows `bash` is the one from Git for Windows (or WSL), taken from the `PATH`. Watch out for `date`'s `%Z`: on Git for Windows it comes back empty, so `scripts/now.sh` uses `%z`.

This is arbitrary code execution on every read of a `.tpl.md`, per project and without confirmation — exactly like a hook. A template *is* code: treat it as such in review and don't render templates you didn't write yourself.

## Claude Code

`PreToolUse` with `matcher: "Read"`. Receives `tool_name` / `tool_input.file_path`, replies with `hookSpecificOutput.permissionDecision` = `allow` or `deny` + `permissionDecisionReason`.

## Copilot CLI

Config in `.github/hooks/*.json` (repo) or `~/.copilot/hooks/*.json` (user), with separate commands for `bash` and `powershell`. Differences from Claude:

| | Claude Code | Copilot CLI |
|---|---|---|
| event | `PreToolUse` | `preToolUse` |
| registration | `.claude/settings.json` | `.github/hooks/*.json` (`version: 1`) |
| per-tool matcher | yes (`matcher`) | no: the hook sees every tool call and filters by itself |
| read tool | `Read` | `view` |
| path field | `tool_input.file_path` | `toolArgs.path` |
| type of `toolArgs` | object | **JSON string** (object in the SDK) |
| allow | explicit JSON | empty stdout, exit 0 |
| deny | JSON nested in `hookSpecificOutput` | flat `{"permissionDecision":"deny","permissionDecisionReason":…}`, on one line |
| hook errors | fail-open | **fail-closed**: exit ≠ 0 = deny |

The command invokes the hook with a **relative** path (`hooks/render_template.py`): Copilot doesn't expose a variable like `${CLAUDE_PROJECT_DIR}`, but it runs the hook with the cwd set to the project directory — the one containing the `.github/` it read the config from. Verified by launching `copilot` both from `prototype/` and from the parent directory: in both cases the hook fires. So there's no need to look for the project root.

Two practical consequences on Copilot:

- The hook catches every exception and always exits `0`, and the command in the JSON also ends with `exit 0`: a bug here would block *all* tool calls in the session. This holds for the "unknown host" case too: empty stdout, which on both Copilot and Claude means "proceed".
- The rendered content travels in the `permissionDecisionReason`, as on Claude. It's not just cosmetic parity: on Copilot CLI the `additionalContext` of `preToolUse` is not forwarded to the model ([copilot-cli#2585](https://github.com/github/copilot-cli/issues/2585)), so the `deny` is the only documented channel for getting text of ours through to the agent.

Alternatives available on Copilot, not used here in order to keep parity with Claude: `modifiedArgs` to redirect `view` to an already-rendered temporary file, or a `postToolUse` hook that replaces `modifiedResult.textResultForLlm` (the agent would see a successful read instead of a refusal).

## Slash command

Copilot CLI does **not** support custom slash commands: `.github/prompts/*.prompt.md` works in VS Code but not in the CLI ([#618](https://github.com/github/copilot-cli/issues/618), [#1113](https://github.com/github/copilot-cli/issues/1113)). The closest equivalent is a **custom agent** in `.github/agents/*.agent.md` (or `~/.copilot/agents/`), invocable with `/agent` and selectable automatically by the model. Here `run-prompt.agent.md` does the same thing as `.claude/commands/run-prompt.md`.

## Trying it

```powershell
# Claude Code
claude            # then: /run-prompt

# Copilot CLI
copilot           # then: /agent → run-prompt

# hook by hand
'{"toolName":"view","toolArgs":"{\"path\":\"prompts/main.tpl.md\"}"}' |
  uv run --script hooks\render_template.py --protocol copilot
```

If `view` is denied with the expanded template inside it (partials and README included, system date and time in place of the `sh` call), the hook works.
