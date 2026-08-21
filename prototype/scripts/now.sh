#!/usr/bin/env bash
# Data e ora correnti del sistema, interpolate nei template con
# `{{ sh("scripts/now.sh") }}`. Lo stdout finisce dentro il prompt: una riga
# sola, niente log, niente rumore.
set -euo pipefail

# %z (offset numerico) e non %Z: su Git for Windows il nome della timezone
# torna vuoto e lascerebbe spazi in coda dentro il prompt.
date '+%A %d %B %Y, %H:%M:%S %z'
