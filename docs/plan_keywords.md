# Plan de keywords — documento vivo (2026-08-16)

Plan para atacar el problema de la mala calidad/dispersión de las keywords.
Este archivo es la fuente de verdad del plan; se actualiza a medida que se refina.

---

## ✅ Estado de ejecución (2026-08-23)

| Etapa | Estado | Resultado |
|---|---|---|
| 1. Prompts gemma fusionados (P2+JSON+exactly 5) | ✅ | 162+23 regenerados, **100% exactamente 5/media** (antes 78-100% en 8) |
| 2. Refinar v2 (bugs + auditoría SINONIMOS) | ✅ | `singularizar` protege `-és/-ás/-ís`; `_es_frase_basura` preserva lugares; SINONIMOS auditado y extendido; dry-runs idempotentes (0 cambios) |
| 3. Sonido (mapeo EN→ES + top_k=5) | ✅ | 51 clases traducidas; 242 registros migrados; 0 tokens EN; máx 5/media |
| 3b. **Sonido: umbral + exclusión de corpus** (2026-08-25) | ✅ | `UMBRAL_PROB` 0.05→**0.15** sobre **media por ventana** (era suma); `CLASES_EXCLUIDAS_POR_CORPUS` (transporte acuático/océano/erupción/explosión/serpiente) con exclusión case-insensitive; flags `--umbral-prob`/`--incluir-excluidas`; limpia tags stale en medios sin audio. Re-run: **barco/erupción/explosión/serpiente = 0**; solo `spray`/`flecha` (freq 1, fuera por corte) |
| 4. Video (`ia_keywords_video`) | ✅ | 139 videos con keywords ES (traducción SINONIMOS→glosario→Argos + refinar), cap 5 |
| 5. Nube unificada en `elecciones` | ✅ | `_consulta_tags` consume las 5 claves + filtros + top-25% + MAX_TAGS=200 |
| 6. Descripciones (prefijos colon) | ✅ | 14 registros limpiados (7 legacy), invariante OK |

**Verificación lista "a"** (mismas 4 fuentes): 181→**137 tags**; artefactos eliminados
(`agobo`, `amágimador`, `arrow`, `airscrew`, `artillery fuego`, `auto racing`, `ensalada`,
`agua de ojos`); familias colapsadas (`abandonado→abandono`, `aislado→aislamiento`,
`arquitectónico→arquitectura`, `agrícola→agricultura`, `acera colorida→acera`).

**Backups**: `flujos_20260823_210927_prompt_merge.db` · `flujos_20260823_213845_refinar_v2.db`
· `flujos_20260823_215152_sonido_fix.db` · `flujos_20260823_215849_video_kw.db` ·
`20260823_220326_limpiar_descripciones.db` · `flujos_20260825_010314_sonido_umbral.db`.

## 🔜 Follow-ups (documentados, no ejecutados)

1. **Sonido cross-fuente**: el dict compartido NO es apto para clases de sonido
   (`vehículo→automóvil` angosta erróneamente; dropea compuestos como `ruido de viento`).
   La nube muestra dups `animal`(sonido)/`animales`(visión). Requiere un refiner
   sound-aware con whitelist de clases.
2. **Auditoría de glosario**: se corrigieron `building`/`buildings`→`edificio(s)`
   (estaban →`urbanismo`, legacy del dict viejo). Pueden quedar más entradas
   `db_seed` contaminadas de esa época.
3. **Glosario de sonido completo**: 123 clases AudioSet más se filtrarían si se
   detectaran (script en `%TEMP%\opencode\sim_traduccion_527.py`).
4. **Multiword singularizado**: tags de 2+ palabras terminadas en `-os/-as` pierden
   concordancia en la última palabra (pre-existente, ej. `participantes distribuido`).
5. **`construcción`** quedó libre tras limpiar `urbanismo` — decidir mapeo.
6. **Jerarquías de tags** (diferido por decisión).
7. Video 600 tiene `keywords: ['']` en `video_analysis` (fuente vacía) → 0 tags, correcto.
8. **Re-exportar el deploy** con la clave nueva `ia_keywords_video` y los tags de sonido
   saneados (el snapshot `deploy/db/visualizacion.db` está desactualizado).

---

## §Sonido — calibración del umbral y exclusión de corpus

**Umbral** (`audio_tagging.py`, `UMBRAL_PROB = 0.15`): se aplica sobre la **media por
ventana** (0-1), no sobre la suma. Las probs almacenadas eran suma entre ventanas
(speech llegaba a 23.9), lo que hacía inútil cualquier umbral. Normalizando a media
por ventana: habla/música promedian 0.49-0.81; el ruido de baja confianza 0.06-0.17.
Umbral 0.15 elimina el grueso del ruido conservando 272/314 medios.

**Exclusión** (`CLASES_EXCLUIDAS_POR_CORPUS`): decisión de **dominio** (viaje terrestre
BA→Tucumán 2025). El viento/rodadura de la bici se clasifica como agua, erupción,
explosión o serpiente. Clases excluidas: transporte acuático (`boat, water vehicle`,
`motorboat, speedboat`, `rowboat, canoe, kayak`, `sailboat, sailing ship`), `ocean`,
`eruption`, `explosion`, `snake`. Comparación **case-insensitive** (el modelo las
devuelve con mayúsculas: `'Boat, Water vehicle'`).

**Cuándo revisar**: cualquier futuro contenido con agua/volcanes/pólvora/serpientes
(p. ej. un cruce en ferry o un lago) → eliminar de la lista o usar `--incluir-excluidas`.

**Validación con la lista "a"**: todas las dudosas resultaron falsos positivos. Tras
umbral+exclusión solo quedan `spray`(1) y `flecha`(1) — frecuencia 1, fuera por el
corte por cantidad de la nube (no requieren exclusión explícita).

**Bug corregido**: `--mode update` no limpiaba el `ia_keywords_sonido` stale de medios
sin audio (24 vídeos sin pista) → quedaban dudosas obsoletas. Ahora se borra el
`CLAVE_SALIDA` en las ramas "sin audio" y "sin etiquetas".

---

## 0. Hallazgos establecidos

- Espacio de tags: **2037 tags** en la unión de las 4 fuentes, **~63% freq-1**.
  Lista "a" como muestra: 181 tags, 108 freq-1 (60%).
- Cantidad por medio: visión 5 · gemma **8** (satura el contrato "5-8") · sonido 10.
  → El contrato de cantidad es el acelerador de la dispersión.
- minicpm (visión): tolera prompts EN simples/medianamente estructurados; se
  degrada con prompts largos/complejos o en español.
- gemma (texto): tolera prompts largos estructurados (el P2 de 7 reglas ganó el A/B).
- `refinar_keywords`: tiene bugs (`singularizar` `estrés`→`estr`, `_es_frase_basura`
  borra lugares de 3+ palabras como `carmen de areco`), entradas lossy/erradas, y
  hoy está aplicado solo a `ia_keywords`.
- Sonido: 4 clases AudioSet sin traducir (`arrow`, `airscrew`, `artillery fuego`,
  `auto racing`). Artefactos de gemma: `agobo`, `amágimador`.
- **Video**: 0 videos con `ia_keywords`; solo `video_analysis` (139 videos, 207
  escenas, ~1.5 escenas/video, keywords EN). 87 videos con whisper (37 con keywords;
  50 descartados por transcripción corta). 115 videos con audio_tagging.

## 1. Decisiones (cerradas)

| Tema | Resolución |
|---|---|
| Nube | **Multi-fuente**: visión + transcripción + texto + sonido + video |
| Jerarquías de tags | **NO por ahora → colapsar** (diferido) |
| Prompt gemma (transcripciones/textos) | **Fusionado**: P2 (7 reglas) + JSON `{"tags":[...]}` + **exactly 5** |
| JSON estructurado | SÍ (parsear → almacenar coma-separado) |
| Auditoría de SINONIMOS | SÍ (corregir erradas + decidir lossy + extender familias) |
| `video_analysis` | **Mismo camino que las fotos** (difiere solo en la extracción de frames, ya existente en `analyze_video.py`) |
| Sonido | `top_k=5` + corregir clases EN sin traducir |

## 2. Prompt fusionado (gemma — transcripciones y textos)

```text
Analizá la transcripción (o: este **texto**) y extraé las keywords del SENTIDO de lo que se dice
(de qué trata realmente, no de las palabras sueltas).
Reglas OBLIGATORIAS:
1. Formato: SOLO un objeto JSON válido con exactamente 5 keywords en ESPAÑOL:
   {"tags": ["a", "b", "c", "d", "e"]}. Sin texto adicional. El ejemplo es solo formato;
   sus tags NO pertenecen a este texto.
2. Las keywords salen del SIGNIFICADO: temas, lugares, actividades, personas, emociones,
   clima, objetos, transporte, comida, sensaciones.
3. PROHIBIDO palabras vacías o muletillas: bien, buen, buena, bueno, finalmente, falta,
   tranquilo, cuidado, solo, siempre, después, ya, cosa, algo, 'luz' (salvo tema central).
4. NO copies errores de transcripción: si una palabra es artefacto de voz, ignorala.
5. Sé FIEL: no agregues interpretaciones que el texto no sostenga (si se ayudaron →
   'solidaridad', nunca 'sociedad individualista').
6. Escribí bien las compuestas: respetá género y número.
7. Preferí palabras de contenido concreto antes que adverbios o adjetivos genéricos.
```

Variantes: `PROMPT_KEYWORDS_TRANSCRIPCION` (cabecera "Analizá la transcripción",
terminador `Transcripción:\n`) y `PROMPT_KEYWORDS_TEXTO` (cabecera "Analizá este
**texto**", terminador `Texto:\n`).

## 3. Rediseño de refinamiento (colapsar, sin jerarquía)

1. **Arreglar bugs**:
   - `singularizar`: no tocar palabras que terminen en vocal acentuada + `s`
     (`-és`, `-ás`, `-ís`) → evita `estrés`→`estr`, `interés`→`interé`.
   - `_es_frase_basura`: preservar lugares/entidades de 3+ palabras
     (`carmen de areco`, `san andrés de giles`).
2. **Auditar SINONIMOS**:
   - Corregir entradas semánticamente erradas (`urbanismo→[edificios, rascacielos...]`).
   - Decidir caso a caso las lossy (`caballos→animales`, `aventura→viaje`,
     `equipaje→mochila`, `naturaleza→campo`).
3. **Extender con familias reales** (de la lista "a"):
   `abandonado→abandono`, `aislado→aislamiento`, `arquitectónico→arquitectura`,
   `agrícola→agricultura`, `agua fangosa/turbia/potable→agua`,
   `acera colorida→acera`, `ambiente urbano/arquitectura urbana→urbano`,
   `apoyo mutuo/social/grupal→apoyo`, `ayuda/ayuda mutua→apoyo`,
   `animales→animal`.
4. **Descartar genéricas**: `actividad humana`, `alta potencia`,
   `asientos exteriores`, `actividad` (sola).
5. **Detectar artefactos**: `agobo`, `amágimador` (y patrón general de
   palabras con doble acento/imposibles).

## 4. Limpiezas complementarias

- **Sonido**: corregir mapeo de clases EN→ES sin traducir en `audio_tagging.py`
  (`arrow`, `airscrew`, `artillery fuego`, `auto racing`); `top_k=5`.
- **Descripciones** (menor): extender `PREFIJOS_META_ES`/`PREFIJOS_META_EN` con las
  variantes de dos puntos (`...detalle:`) y correr `limpiar_descripciones.py`
  (7 registros legacy).

## 5. Nube unificada

`elecciones._consulta_tags` consume las 4 claves actuales **+ la clave de video
traducida** + filtros (`len>2`, `KEYWORDS_A_IGNORAR`, `KEYWORDS_SENSIBLES`) +
top-25% + `MAX_TAGS=200`, sobre valores canonicalizados por `refinar` (corrido por
clave con el mismo diccionario).

## 6. Etapas de implementación

1. **Prompts gemma** — fusionado + exactly 5 → backup → smoke (~10) → regenerar
   (162 transcripciones + 23 textos).
2. **Refinar v2** — bugs + auditoría + capas nuevas → dry-runs (0 = idempotente)
   → aplicar por clave.
3. **Sonido** — mapeo EN→ES + `top_k=5`.
4. **Video** — traducir `video_analysis` EN→ES (glosario + motor clásico, NO-AI)
   → agregar por video (dedupe de escenas) → refinar → clave para la nube.
5. **Nube unificada** — `elecciones` multi-clave.
6. **Descripciones** — prefijos colon + limpiar 7 legacy.

## 7. Verificación

- Re-correr la lista "a": menos variantes, menos freq-1, 0 artefactos.
- Dry-runs idempotentes (0 cambios tras aplicar).
- Backups en `db/backups/` antes de cada mutación.