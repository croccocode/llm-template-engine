# llm-template-engine

A ClaudeCode/Copilot hook that render templated prompt files on the fly using [MiniJinja](https://github.com/mitsuhiko/minijinja).

Evey tool call burn tokens. Often prompts include references to other files and instructions that could few months ago
would have been just few lines of code:

```prompt
Read the instruction at ../another/file.md 
and perform those instruction on any *.json file in the folder ./zoo  
```

This simple prompt will cost 3 Tool API call and probably 4 API Call (input token, output token)
This code allows you to reqrite the template as follows:
```jinja
# Instructions
{% include ../another/file.md %}

For each of this file
{% set ns = exec("
from pathlib import Path
files = sorted(p.name for p in Path('./files').iterdir() if p.is_file())
") %}

{% for f in ns.files %}
- {{ f }}
{% endfor %}

```


# How does it work
Add a `preToolUse` hook intercepts every read of a `.tpl.md` file, expands it with **MiniJinja** (includes resolved first in the file's own directory, then in the project root; `sh("…")` to interpolate the output of a bash script) and returns the already-rendered content directly in the `deny` message, without writing compiled files to disk.

The same engine runs on two agents: **Claude Code** and **GitHub Copilot CLI**. A single hook serves both: the protocol changes, not the logic.

By default every `.md` and `.txt` file is a template. Two optional flags change the selection,
both matched against the file name only, both repeatable:

- `--include=<regexp>`: only names matching the regexp are templates (replaces the default suffix rule);
- `--exclude=<regexp>`: names matching the regexp are never templates (wins over `--include`).


## Claude Code
```shell
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Read|Bash",
        "hooks": [
          {
            "type": "command",
            "command": "node \"${CLAUDE_PROJECT_DIR}\\hooks\\render_template.mjs\" --protocol claude",
            "timeout": 60
          }
        ]
      }
    ]
  }
}
```
## Copilot
!! OCCHIO !!
Scrivi che copilot legge anche gli hook di claude 
https://docs.github.com/en/copilot/reference/hooks-reference?utm_source=chatgpt.com#hooks-locations
il codice si aspetta il payload dell'evento in formato claude sempre

For Copilot CLI, a new file `.github/hooks/render-template.json`:

```json
{
  "version": 1,
  "hooks": {
    "PreToolUse": [
      {
        "type": "command",
        "matcher": "Read|Bash",
        "bash": "uv run --script /Users/totomz/Documents/croccocode/llm-template-engine/template_engine.py",
        "timeoutSec": 60
      }
    ]
  }
}
```

## Dbug and troubleshooting
Com faccio a sapere se funiziona? per chè mi ritrovo questo nel prompt!
```
Read prompt.md Denied by preToolUse hook: 'prompt.md' is a source template. Here is the content of the file
```

To enable the debug log, add the `--debug` flag to the tool:
```
"bash": "uv run --script /Users/totomz/Documents/croccocode/llm-template-engine/template_engine.py --debug",
```

# Requirements
Either NodeJS and npx or Python + uv 
Overhead is ~100 ms per call, all of it Node and WASM startup. 
There is nothing to provision on first run, so no cache to warm up beforehand.

# Developer
## Run tests and linter
```shell
/.shMakefile test
```