---
description: Test end-to-end del pre-hook di templating (Read su main.tpl.md -> redirect -> file compilato)
---

Esegui questo test end-to-end del pre-hook di templating, senza modificare nessun file:

1. Prova a leggere `prompts/main.tpl.md` con lo strumento Read.
2. L'hook dovrebbe negare la lettura e indicarti il path di un file `*.compiled.md` da leggere al suo posto. Leggi quel file.
3. Verifica che il contenuto di `prompts/_partials/shared_context.md` ("Contesto condiviso...") compaia espanso dentro il file compilato.
4. Riportami in breve: l'hook e' scattato? il contenuto incluso e' presente e corretto? eventuali errori nell'output dell'hook?
