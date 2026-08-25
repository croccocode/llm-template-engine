#  
⚠️ Warning: this could lead in untrusted code and template injection! (see below, #Security)

A ClaudeCode/Copilot hook that render templated prompt files on the fly using [MiniJinja](https://github.com/mitsuhiko/minijinja).

Evey tool call burns tokens. Often prompts include references to other files and instructions 
that few months ago would have been just few lines of script
This simple prompt will cost 3 Tool API call and probably 4 API Call (input token, output token), ~90k tokens
```prompt
Read the instruction at ../another/file.md 
and perform those instruction on any *.json file in the folder ./zoo  
```

**llm-template-engine** cut the token usage by ~30%, by rewriting the template as follow:
```jinja

Apply these instructions
{% include ../another/file.md %}

To each of these files
{{ sh("ls -1") }}
```

## 30% token reduction? Really?
This is what we see in the templates we are rewriting. We are working on publi benchmarks

# How does it work
You need to have [uv](https://docs.astral.sh/uv/getting-started/installation/) installed in your system.
Register the script `render_template.py` as `preToolUse` hook. It will intercepts every read of files.
If the file is a template, it will be rendered in a temporary folder and the hook will redirect the tool to 
the rendered template.

A file is considered a template accordingly to its name:
- By default, `*.tpl.md` and `*.tpl.txt` files are considered a template
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
[Copilot reads both Claude Code hooks and Copilot hooks](https://docs.github.com/en/copilot/reference/hooks-reference?utm_source=chatgpt.com#hooks-locations) 
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
reading untrusted files (cloned repos, downloads) executes their content. Narrow the selection with --include.


# Developer
Run tests and linter
```shell
/.shMakefile test
```