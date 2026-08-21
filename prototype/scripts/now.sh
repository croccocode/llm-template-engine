#!/usr/bin/env bash
# Current system date and time, interpolated into templates with
# `{{ sh("scripts/now.sh") }}`. The stdout ends up inside the prompt: a single
# line, no logs, no noise.
set -euo pipefail

# %z (numeric offset) and not %Z: on Git for Windows the timezone name comes
# back empty and would leave trailing spaces inside the prompt.
date '+%A %d %B %Y, %H:%M:%S %z'
