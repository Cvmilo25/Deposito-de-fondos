# The Metro · Vocab

Sistema interactivo para aprender las 146 palabras del vocabulario de la lectura *The Metro*.

- `src/words-1.js` … `src/words-4.js`: fichas de cada palabra (IPA, significado en español, explicación sencilla, ejemplo del material, 2 ejemplos nuevos con traducción, colocaciones, sinónimos, errores comunes y truco de memoria).
- `src/app.html`: la aplicación (estudio guiado, flashcards con mazos y direcciones, 7 tipos de práctica, repetición espaciada, desafío, fichas y panel de progreso).
- `build.py`: une datos y aplicación en `index.html`, la página publicada como Artifact.

```
python3 vocab-metro/build.py
```

Correcciones respecto del material original: *exquisite* es adjetivo (no verbo); *tenement* y *voodoo* son sustantivos usados delante de otro sustantivo; *ridicule* y *well* se explican en su forma base («make fun of», «fill with liquid»). El material no incluye las frases originales de la lectura, así que la app muestra la frase de ejemplo de la lista y lo indica.
