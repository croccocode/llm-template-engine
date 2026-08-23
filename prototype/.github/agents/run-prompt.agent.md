---
name: run-prompt
description: Runs the project's main prompt (prompts/main.tpl.md), expanded by the preToolUse hook
---

Open `prompts/main.tpl.md` and follow the instructions inside it.

The file is a template: the `preToolUse` hook intercepts the read, expands it
with MiniJinja and returns the rendered content to you instead of the source.
Do not try to read it with `bash`/`cat`: that path does not go through the
hook and you would see the `{% include %}` tags unexpanded.
