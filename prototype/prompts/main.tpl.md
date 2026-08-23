# Test prompt

This is a test prompt for the template engine.

{% include "_partials/shared_context.md" %}

## System date and time

{{ sh("scripts/now.sh") }}

{% include "README.md" %}

Questions:

1. Based on the README above, which template engine does this project use to expand includes?
2. What day is it today?

Answer in one line per question, concisely.
