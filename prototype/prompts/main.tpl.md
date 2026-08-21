# Prompt di test

Questo e' un prompt di prova per il template engine.

{% include "_partials/shared_context.md" %}

## Data e ora di sistema

{{ sh("scripts/now.sh") }}

{% include "README.md" %}

Domande:

1. In base al README qui sopra, quale motore di template usa questo progetto per espandere gli include?
2. Che giorno e' oggi?

Rispondi in una riga per domanda, in modo conciso.
