# llm-template-engine
⚠️ Warning: this could lead to untrusted code execution and template injection! (see below, #Security)

A ClaudeCode/Copilot hook that renders templated prompt files on the fly using [MiniJinja](https://github.com/mitsuhiko/minijinja).

Every tool call burns tokens. Often prompts include references to other files and instructions 
that a few months ago would have been just a few lines of script.
This simple prompt will cost 3 tool API calls and probably 4 API calls (input token, output token), ~90k tokens
```prompt
Read the instruction at ../another/file.md 
and perform those instructions on any *.json file in the folder ./zoo  
```

**llm-template-engine** cuts the token usage by rewriting the template as follows:
```jinja

Apply these instructions
{% include "../another/file.md" %}

To each of these files
{{ sh("ls -1 ./zoo/*.json") }}
```

## 30% token reduction? Really?
This is what we see in the templates we are rewriting. We are working on public benchmarks.

# How does it work
You need to have [uv](https://docs.astral.sh/uv/getting-started/installation/) installed in your system.
Register the script `template_engine.py` as `preToolUse` hook. It will intercept every read of files.
If the file is a template, it will be rendered in a temporary folder and the hook will redirect the tool to 
the rendered template.

A file is considered a template according to its name:
- By default, `*.tpl.md` and `*.tpl.txt` files are considered templates;
- flag `--include=<regexp>`: only names matching the regexp are templates (replaces the default rule);
- flag `--exclude=<regexp>`: names matching the regexp are never templates (wins over `--include`).

## Claude Code
Register this hook in `.claude/settings.json`
```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Read|Bash",
        "hooks": [
          {
            "type": "command",
            "command": "uv run --script https://raw.githubusercontent.com/croccocode/llm-template-engine/main/template_engine.py",
            "timeout": 60
          }
        ]
      }
    ]
  }
}
```


## Copilot
⚠️ Duplicate hook execution!
[Copilot reads both Claude Code hooks and Copilot hooks](https://docs.github.com/en/copilot/reference/hooks-reference#hooks-locations).
Do not register the Copilot hook if you have already registered the Claude hook. 

To register the hook in Copilot, add a new file `.github/hooks/render-template.json`:
```json
{
  "version": 1,
  "hooks": {
    "PreToolUse": [
      {
        "type": "command",
        "matcher": "Read|Bash",
        "bash": "uv run --script https://raw.githubusercontent.com/croccocode/llm-template-engine/main/template_engine.py",
        "timeoutSec": 60
      }
    ]
  }
}
```

# Debug and troubleshooting

To enable the debug log, add the `--debug` flag to the tool:
```
uv run --script https://raw.githubusercontent.com/croccocode/llm-template-engine/main/template_engine.py --debug
```

To specify custom include or exclude filters:
```
uv run --script https://raw.githubusercontent.com/croccocode/llm-template-engine/main/template_engine.py --include=<regexp> --exclude=<regexp>
```


# Security
By default, every `.tpl.md` and `.tpl.txt` read is rendered, and `sh()`, `eval()` and `exec()` run with your privileges. 
Reading untrusted files (cloned repos, downloads) executes their content. Narrow the selection with `--include`.


# Developer
Run tests and linter
```shell
./shMakefile test
```