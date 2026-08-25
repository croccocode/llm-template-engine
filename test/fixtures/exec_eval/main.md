{% set ns = exec("
from pathlib import Path
files = sorted(p.name for p in Path('./test_files').iterdir() if p.is_file())
") %}
# Files
{% for f in ns.files %}
- {{ f }}
{% endfor %}

# Rules
{% include "rule.md" %}

2 ** 10 = {{ eval("2 ** 10") }}
