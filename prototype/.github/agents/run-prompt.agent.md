---
name: run-prompt
description: Esegue il prompt principale del progetto (prompts/main.tpl.md), espanso dall'hook preToolUse
---

Apri `prompts/main.tpl.md` e segui le istruzioni contenute al suo interno.

Il file e' un template: l'hook `preToolUse` intercetta la lettura, lo espande
con MiniJinja e ti restituisce il contenuto renderizzato al posto del sorgente.
Non provare a leggerlo con `bash`/`cat`: quel percorso non passa dall'hook e
vedresti i tag `{% include %}` non espansi.
