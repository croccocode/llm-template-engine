# llm-template-engine

A template engine for LLM prompts: a `preToolUse` hook intercepts every read of a `.tpl.md` file, expands it with **MiniJinja** (includes resolved first in the file's own directory, then in the project root; `sh("…")` to interpolate the output of a bash script) and returns the already-rendered content directly in the `deny` message, without writing compiled files to disk.

The same engine runs on two agents: **Claude Code** and **GitHub Copilot CLI**. A single hook serves both: the protocol changes, not the logic.

`prototype/` holds the original Python implementation, kept as a reference. It is not used at runtime.

## Requirements

Only `node` on the PATH. Nothing else: no `npm install`, no `node_modules`, no network.

MiniJinja is vendored under `vendor/` as the official [`minijinja-js`](https://www.npmjs.com/package/minijinja-js) WASM build (~1.1 MB, version recorded in `vendor/package.json`). To install the engine in another repo you copy `template_engine.mjs`, `hooks/render_template.mjs`, `vendor/` and the host's config file — the target repo need not be a JS project at all.

Overhead is ~100 ms per call, all of it Node and WASM startup. There is nothing to provision on first run, so no cache to warm up beforehand.

## Adding this to an existing project

The engine is four things to copy and one file to register. The target repo does
not need to be a JS project, does not need a `package.json`, and does not need
`npm` at all.

**1. Copy the engine** into the root of the target repo:

```
template_engine.mjs
hooks/render_template.mjs
vendor/                     <- the whole directory, verbatim
```

Copy `vendor/` wholesale — never a hand-picked subset of its files. The
`package.json` inside it is what stops Node from interpreting the CommonJS glue
as ESM; see *Why `.mjs`* below.

**2. Register the hook** with whichever agent you use. For Claude Code, in
`.claude/settings.json` (merge into `hooks` if the file already exists):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Read",
        "hooks": [
          {
            "type": "command",
            "command": "node \"${CLAUDE_PROJECT_DIR}/hooks/render_template.mjs\" --protocol claude",
            "timeout": 60
          }
        ]
      }
    ]
  }
}
```

For Copilot CLI, a new file `.github/hooks/render-template.json`:

```json
{
  "version": 1,
  "hooks": {
    "preToolUse": [
      {
        "type": "command",
        "powershell": "node hooks/render_template.mjs --protocol copilot; exit 0",
        "bash": "node hooks/render_template.mjs --protocol copilot; exit 0",
        "timeoutSec": 60
      }
    ]
  }
}
```

Keep the trailing `; exit 0` on the Copilot commands: its hooks are fail-closed,
so a non-zero exit denies every tool call in the session.

**3. Make sure `vendor/` is committed.** Many `.gitignore` templates carry a
`vendor/` rule (Go's has it, commented, with an invitation to enable it). If it
is active, the 1 MB `.wasm` never lands in the repo and the engine breaks for
everyone who clones. Check with `git check-ignore -v vendor/minijinja_js_bg.wasm`.

**4. If you will use `sh()`**, add `*.sh text eol=lf` to `.gitattributes` before
committing any script. On Windows with `core.autocrlf` on, a CRLF script dies
under bash with `$'\r': command not found`.

**5. Write a template** and check it end to end:

```bash
echo '{"tool_name":"Read","tool_input":{"file_path":"your/file.tpl.md"}}' \
  | node hooks/render_template.mjs --protocol claude
```

A `deny` whose reason contains the expanded text means it works. An `allow`
means the path or the suffix did not match.

## Layout

```
template_engine.mjs             # MiniJinja rendering
hooks/render_template.mjs       # single hook: PROTOCOLS table + decision
vendor/                         # vendored minijinja-js, copied verbatim
prompts/main.tpl.md             # test template
prompts/_partials/…             # included partials
scripts/now.sh                  # test script: system date and time
.claude/settings.json               # registers the hook: --protocol claude
.claude/commands/run-prompt.md      # /run-prompt slash command
.github/hooks/render-template.json  # registers the hook: --protocol copilot
.github/agents/run-prompt.agent.md  # custom agent (slash command equivalent)
CLAUDE.md                           # project instructions for coding agents
prototype/                          # original Python implementation, reference
```

Reading stdin, filtering on the suffix, calling the renderer and handling errors is common code. Only three things depend on the host, and they live in the `PROTOCOLS` object: the names of the input keys, the shape of the output JSON, and whether the allow must be declared or silence is enough.

Who is invoking us is stated by the host itself via `--protocol` (or `LTE_HOOK_PROTOCOL`), since the two config files are separate anyway. We don't infer it from the payload except as a fallback: the shape is ambiguous, because Copilot also supports a *VS Code compatible* format with the same `tool_name` / `tool_input` keys as Claude.

## Only `*.tpl.md` files are templates

The suffix is not just what the hook matches on — it decides how an included
file is treated. A `{% include %}` of another `*.tpl.md` is rendered as a
template; **anything else is inlined verbatim**, wrapped in `{% raw %}` by the
loader, so its `{{ }}` and `{% %}` are emitted as literal text.

That is deliberate, and not only for tidiness: `sh()` is in scope for the whole
render, so evaluating an ordinary `.md` would turn "include this markdown file"
into arbitrary code execution. A plain document can never do anything.

The practical consequences:

- This README documents `{{ sh("scripts/now.sh") }}` in a code fence and is
  itself included by `prompts/main.tpl.md`. It shows up in the rendered prompt as
  the literal example, not as a timestamp.
- A partial that genuinely needs template logic has to be named `.tpl.md`.
  `prompts/_partials/shared_context.md` is plain text, so `.md` is right for it.
- A literal raw-terminator in the text would close the wrapper early, so the
  loader steps around each one and re-opens. That is what keeps a document that
  *describes* the engine inlinable — including this very section.

## Shell scripts in templates

Besides includes, a template can interpolate the output of a bash script:

```jinja
{{ sh("scripts/now.sh") }}
```

`sh(script, *args)` resolves the path with the **same rules as includes** (first the template's directory, then the project root; `..` outside both is discarded), runs the script with `bash` — cwd pinned to the root, so the same template renders identically no matter which directory the agent started from — and replaces the call with its **stdout**, without the trailing newline. Extra arguments reach the script as `$1`, `$2`, … and don't go through a shell: no requoting.

If the script is missing, exits ≠ 0, or exceeds `SCRIPT_TIMEOUT_SECONDS` (30 s), the render **fails** and the agent receives the error with the stderr inside it, instead of the template. A prompt that doesn't start is better than one that lies about a silent hole.

On Windows `bash` is the one from Git for Windows (or WSL), taken from the `PATH`. Watch out for `date`'s `%Z`: on Git for Windows it comes back empty, so `scripts/now.sh` uses `%z`.

This is arbitrary code execution on every read of a `.tpl.md`, per project and without confirmation — exactly like a hook. A template *is* code: treat it as such in review and don't render templates you didn't write yourself.

## Callbacks must never throw

MiniJinja here is a WASM module, and a JS callback that throws across that boundary **traps it**: the real message is replaced by `RuntimeError: unreachable`, and once trapped, later error paths in the same process degrade the same way. Upstream states it plainly: *"If the engine panics, the WASM runtime corrupts."*

So neither the loader nor `sh()` ever throws. They park the failure in a variable and return a neutral value; `render()` checks it once the render has returned and raises then. Keep it that way when adding template functions — this is why error messages survive at all.

## Why `.mjs`, and why `vendor/` is copied whole

A plain `.js` is ambiguous: Node decides ESM-vs-CommonJS from the *nearest* `package.json`, which in a host repo may be absent, present without `"type"`, or `"type": "module"`. `.mjs` means "ESM, always", independent of the host.

That protects our files but not the vendored glue, which is CommonJS. Its protection is the `package.json` that ships inside the upstream `dist/node/` directory: it acts as the module-resolution boundary, stopping Node from walking up to the host repo's `package.json`. **Copy `vendor/` verbatim — never a hand-picked subset of its files**, or a host repo declaring `"type": "module"` breaks the engine with `ReferenceError: module is not defined`.

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

The command invokes the hook with a **relative** path (`hooks/render_template.mjs`): Copilot doesn't expose a variable like `${CLAUDE_PROJECT_DIR}`, but it runs the hook with the cwd set to the project directory — the one containing the `.github/` it read the config from.

Two practical consequences on Copilot:

- The hook catches every exception and always exits `0`, and the command in the JSON also ends with `exit 0`: a bug here would block *all* tool calls in the session. This holds for the "unknown host" case too: empty stdout, which on both Copilot and Claude means "proceed".
- The rendered content travels in the `permissionDecisionReason`, as on Claude. It's not just cosmetic parity: on Copilot CLI the `additionalContext` of `preToolUse` is not forwarded to the model ([copilot-cli#2585](https://github.com/github/copilot-cli/issues/2585)), so the `deny` is the only documented channel for getting text of ours through to the agent.

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
  node hooks\render_template.mjs --protocol copilot
```

If `view` is denied with the expanded template inside it (partials and README included, system date and time in place of the `sh` call), the hook works.
