# llm-template-engine

## TODO
- [] render only .md and .txt
- [] add an `--include=<regexp>` param that threat only the filename that match the regexp as template (override the default, allow multiple values) 
- [] add an `--exclude=<regexp>` param that threat only the filename that match the regexp as template (oonly file that do not match the regexp are threated as template) 

A template engine for LLM prompts base on MiniJinja template engine.

It allows you to merge prompts from different file together, or inject the output of shell scipts and ommands driectly 
in the prompt before are evaualted by your LLM engine di fiducia.

## Why
Evey tool call burn tokens. Something lik

```prompt
Read the other prompt ../another/file.md and for each *.son file in this folder, do blabla 
```
This will cost at least 4 LLM api call an 250k tokens

## How does it work
Add a `preToolUse` hook intercepts every read of a `.tpl.md` file, expands it with **MiniJinja** (includes resolved first in the file's own directory, then in the project root; `sh("…")` to interpolate the output of a bash script) and returns the already-rendered content directly in the `deny` message, without writing compiled files to disk.

The same engine runs on two agents: **Claude Code** and **GitHub Copilot CLI**. A single hook serves both: the protocol changes, not the logic.

## How To Use It
By default, this engine will process each `.md` and `.txt` file as template.



```shell
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Read",
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

# Requirements
Either NodeJS and npx or Python + uv 
Overhead is ~100 ms per call, all of it Node and WASM startup. 
There is nothing to provision on first run, so no cache to warm up beforehand.

