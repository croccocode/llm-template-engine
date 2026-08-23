# llm-template-engine

## What this project is

A template engine for LLM prompts. A `preToolUse` hook intercepts every read of
a `*.tpl.md` file, expands it with MiniJinja and hands the rendered text back to
the agent inside the *denial reason* of the tool call — no compiled files are
ever written to disk. One hook serves both Claude Code and GitHub Copilot CLI:
only the wire protocol differs, and it lives in the `PROTOCOLS` table in
`hooks/render_template.mjs`.

The engine is plain Node with MiniJinja vendored as WASM under `vendor/`. There
is no `npm install`, no `node_modules`, no network access required.

## Read-only repository

**Do not make git operations in this repository.** No commits, no branches, no
tags, no pushes, no merges, no rebases, no stashes, no `git add`. Read the
history if it helps you answer something, and stop there.

If work you did should be committed, say so and let the human do it.

## `prototype/` is reference material — read only

`prototype/` holds the original Python implementation of this same engine
(`uv run --script` + PEP 723 + the `minijinja` PyPI bindings). It is kept purely
as a reference for how the design worked before the Node port.

- Read it freely when you need to compare behaviour or recover intent.
- **Never modify anything inside `prototype/`.** Not the code, not the README,
  not its `.claude/` or `.github/` config.
- It is not used at runtime. Nothing at the repo root imports from it.

Note that `prototype/` has its own hook registration. Which one fires depends on
the directory the agent was launched from, so a change at the root does not
affect a session started inside `prototype/`.

## Things that will bite you

**Only `*.tpl.md` files are templates.** The loader inlines any other included
file *verbatim*, wrapped in `{% raw %}`. A plain `.md` must never have its
`{{ }}` / `{% %}` evaluated — `sh()` is in scope during a render, so treating
markdown as a template would make including any file arbitrary code execution.
If you add a partial that genuinely needs template logic, name it `.tpl.md`.

**Callbacks must never throw.** MiniJinja is a WASM module here, and a JS
callback that throws across that boundary traps it: the real message becomes
`RuntimeError: unreachable`, and every later error path in the same process
degrades the same way. The loader and `sh()` therefore park failures in a
`failure` variable and return a neutral value; `render()` raises after the
render returns. Keep that shape when adding template functions.

**The hook must always exit 0.** Copilot's `preToolUse` command hooks are
fail-closed: a non-zero exit denies *every* tool call in the session. The
top-level `catch` and the trailing `process.exit(0)` in
`hooks/render_template.mjs` are load-bearing, not defensive clutter.

**Watch out for backslashes in shell-authored files.** Writing the JSON configs
or the Windows path regexes through a shell heredoc can silently eat one level
of escaping, which produces invalid JSON (`\h` is not a legal escape) or a regex
that no longer matches `C:\`. Use file-writing tools for those, and re-parse the
JSON afterwards to confirm.

**A template is code.** Rendering a `.tpl.md` executes its `sh()` calls, per
project and without confirmation. Review templates the way you would review a
hook, and don't render one you didn't write.

## Verifying a change

```bash
# render directly
node -e "import('./template_engine.mjs').then(m => console.log(m.render('prompts/main.tpl.md')))"

# the hook, as each host invokes it
echo '{"tool_name":"Read","tool_input":{"file_path":"prompts/main.tpl.md"}}' | node hooks/render_template.mjs --protocol claude
echo '{"toolName":"view","toolArgs":"{\"path\":\"prompts/main.tpl.md\"}"}'   | node hooks/render_template.mjs --protocol copilot
```

A correct render contains the partial, the README inlined with its `{{ sh(…) }}`
example still *literal*, exactly one timestamp, and no unexpanded `{% include %}`
tags. Both hook invocations must exit 0.
