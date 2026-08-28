# Revisión pre-presentación — Flujos

Informe de auditoría exhaustiva del proyecto en preparación de la presentación.
Fecha: 2026-08-28. Alcance: `db/*`, `flujos.py`, `scripts/*.py`,
`scripts/ai_media/*`, `scripts/td/*`, `td/*.dat`, `docs/*`, `deploy/js/app.js`.
Máquina de presentación asumida lista (Ollama, ExifTool, ffmpeg, TD, `.toe`).
Demo: **deploy web + TouchDesigner**, todos los filtros, fluir en vivo, tags de
todo tipo, volumen ≈ 2× lo actual. Tiempo de ingesta/mejora no es constraint.

Cada hallazgo tiene severidad, archivo:línea y estado. Se trabaja de a poco;
marcar estado al corregir.

---

## Leyenda de estado

- `⏳ pendiente` — sin resolver
- `🔧 en curso` — se está trabajando
- `✅ resuelto` — corregido y verificado

---

## Plan acordado (Fase 0-4) — este orden alimenta lo que se ve

### Fase 0 — Foto actual DB (esta máquina, estima 2×)
Conteos `media` por `type`, por `provincia`/`municipio`, nulos
`latitude/timestamp_utc/color_1_hex`, conteos `media_metadata` por clave
(`ia_keywords`, `ia_keywords_transcripcion/texto/sonido`, `video_analysis`,
`whisper_estado`, `weather_*`, `dia_semana`), `gradiente/astronomía` poblados.
→ Estima volumen y detecta huecos antes de replicar en máquina de demo.

### Fase 1 — Ingesta masiva (máquina de demo)
Usar `--full-hash` si se quiere deduplicación estricta (mitiga fingerprint
`ingest.py:74` size-mtime). Re-correr queries Fase 0.

### Fase 2 — Mejoras completas (alimenta todos los filtros)
`colors` → `geocode` → `gradiente` → `astronomia` → `fetch_weather` →
`dia_semana` plus keywords todo tipo: `combinado` (visión) +
`keywords_transcripciones --origen transcripcion/texto` + `audio_tagging` +
`analyze_video` + `keypoints_video/contexto`. Regla: `--workers 1`.

### Fase 3 — Artefactos visuales
`exportar_visualizacion.py` deploy fresco + `mapas_municipio.py --mode update`
(Esri autocontenidos) + `mapa_ruta.py`. Unificar nube tags GUI vs TD.

### Fase 4 — Verificación fluir vivo (bloqueante)
Fix B1 race 9001 con `Lock` — único fix pre-demo que toca fluir vivo con todos
los filtros. Smoke `puente_td fluir --una-vez` con todos los grupos y apertura
offline de `deploy/index.html` + 2 mapas municipio.

---

## BLOQUEANTE pre-demo (toca fluir vivo con todos los filtros)

### B1. Race sin lock en el listener 9001 (`puente_td.py:1335-1399`)

- **Severidad:** crítica (fluir en vivo, todos los filtros)
- **Estado:** ✅ resuelto (2026-08-28) — `lock_selecciones = threading.Lock()`, callback con `with lock`, loop con snapshot `dict+clear` bajo lock, `_procesar_rafaga` fuera del lock.
- **Descripción:** `selecciones` y `ultimo_mensaje` sin `threading.Lock` entre el
  thread `ThreadingOSCUDPServer` y el loop principal. Con todos los filtros y
  toques rápidos: selecciones duplicadas/perdidas o `RuntimeError`.

---

## Medio con impacto en filtros (corregir antes de ingesta masiva)

### M1. Universo de tags distinto entre GUI y TD (`gui_fluir.py:108` vs `elecciones.py:108`)

- **Severidad:** media (tags de todo tipo → filtros incompletos)
- **Estado:** ✅ resuelto (2026-08-28) — `gui_fluir.py:105` ahora consulta 5 claves con `IN (?,...)` igual que `elecciones.py` CLAVES_TAGS.
- **Descripción:** `gui_fluir.py` consultaba solo `ia_keywords`; `elecciones.py`
  consulta 5 claves (`ia_keywords`, `ia_keywords_transcripcion`,
  `ia_keywords_texto`, `ia_keywords_sonido`, `ia_keywords_video`). Nube distinta.

### M2. `check_combinado` / `run_combinado` skip query (`improve_db.py:123-134,722-730`)

- **Severidad:** media (si se usa `combinado` en `skip`)
- **Estado:** ✅ resuelto (2026-08-28) — `check_combinado` ahora `NOT EXISTS kw OR NOT EXISTS desc` (ES) y `run_combinado skip` `OR` sobre `kw_en`/`desc_en`.
- **Descripción:** `NOT EXISTS (key IN ('kw_en','desc_en'))` salteaba imágenes con
  solo una de las dos claves. Contador subestimaba, `skip` dejaba a medio hacer.

---

## Deuda técnica post-demo (no tocar pre-presentación)

### D1. `carpeta` en `mover_media.py:232,347`
- **Estado:** ✅ resuelto (2026-08-28) — ahora `basename(dirname)` con `None` si directo en root + `SIDECAR_EXTS` case-insensitive.

### D2. Fingerprint vs hash (`ingest.py:74-83,982-985`)
- **Estado:** ✅ resuelto (2026-08-28) — docstring `fast_fingerprint` aclara que no es SHA y remite a `--full-hash`.

### D3. `relocate.py:112-114` REPLACE no-prefijo
- **Estado:** ✅ resuelto (2026-08-28) — `? || substr(..., length(?)+1)` prefijo-only en vez de `REPLACE`.

### D4. `gradiente.py:108` `skip` recalcula todo
- **Estado:** ✅ resuelto (2026-08-28) — documentado como intencional (determinista y barato).

### D5. Imports sin guard (`ollama_client.py:35`, `transcribe.py:37`, `puente_td.py:42`, `clustering.py:143`)
- **Estado:** ✅ resuelto (2026-08-28) — `try/except ImportError` con mensaje `pip install` + guard en `ollama_client.OllamaVision` y `transcribe.transcribir_audio` + compat `clustering.py` dict/objeto.

### D6. Modelos Ollama (`asegurar_ollama`)
Máquina de demo ya lista; `ollama list` opcional. Post-demo: auto-pull.

### D7. `analyze_video.py` sin `--mode`; `mapas_municipio`/`inferir_hora_textos` sin `replace`
Diseño intencional según `AGENTS.md`. No arreglar.

---

## Diseño intencional — no arreglar

- `mapa_ruta.py` tiles online (solo assets autocontenidos) vs `mapas_municipio.py`
  tiles embebidos → intencional (mapa global pequeño vs 296 municipios offline).
- `gradiente.py` `skip==update` → intencional, documentado.

---

## Menor / pulido

### L1. Docstring CartoDB (`tiles_offline.py:291`)
- **Estado:** ✅ resuelto (2026-08-28) — "CartoDB" → "Esri".

### L2. `check_db.py:91,112` / `check_db_data.py:11` hardcodean `db/flujos.db`
- **Estado:** ✅ resuelto (2026-08-28) — usan `resolver_db` + `--db` en `check_db_data.py` + lista completa de tablas.

### L3. `ingest_gpx.py` sin CLI en `flujos.py`
- **Estado:** ✅ resuelto (2026-08-28) — `flujos.py ingest-gpx` / `gpx` + ayuda.

### L4. Código muerto / menores
- **Estado:** ✅ resuelto (2026-08-28) — `import_telegram.py:179` eliminado, `check_gps.py --folder` expone `check_gps_folder`, `db/util.py:update_flag_cols` documentado como helper futuro, `mover_media.py:SIDECAR_EXTS` case-insensitive, `query.py:_where_seguro` también en `distinct_column`.
- Pendiente menor no bloqueante: `except OperationalError: pass` en migraciones (intencional `IF NOT EXISTS`).

### L5. `ingest.py:691` orden `migrate_db` antes de `schema.sql`
- **Estado:** ✅ resuelto (2026-08-28) — schema primero, luego `migrate_db()`; fallback reordenado.

### L6. `db/migrate.py` no cubre gradiente/astronomía
Dos caminos migración (central `migrate.py` + per-script `migrar_db`). Intencional — `ingest.py:init_db` ahora corre schema primero y `migrate_db` después, evitando enmascarar errores.

### L7. `query.py:_where_seguro` y `clustering.py` embeddings
- **Estado:** ✅ resuelto (2026-08-28) — `query.py` valida `--where` también en `--distinct` y `distinct_column` hace defensa en profundidad; `clustering.py:170` maneja dict vs objeto (`ollama<0.3` vs `>=0.3`) + guard `numpy`/`ollama` missing.

---

## Cobertura de tests

| Test | Comando | Estado |
|---|---|---|
| Gradiente | `python scripts/test_gradiente.py` | ✅ existe |
| Motor loop | `python scripts/ai_media/test_motor_loop.py` (42 asserts) | ✅ existe |
| Migraciones | `python -m pytest db/test_migrate.py -q` | ✅ existe |
| `tiles_offline` proyección | — | ❌ falta (post-demo) |
| `dia_semana` UTC-3 | — | ❌ falta |
| `inferir_hora_textos` peso inverso | — | ❌ falta |
| `mover_media` rutas | — | ❌ falta |
| `import_telegram` JSON | — | ❌ falta |

---

## Foto Fase 0 — esta máquina (2026-08-28, `db/flujos.db` integrity ok, schema 4)

- `media` total **1391**: image 1048, video 183, audio 131, text 29. Subtype 360: 44, md: 29.
- `provincia/municipio` poblados: georef_api 876, municipio_cercano 291, departamento 211, NULL 9 (0.6%), manual 4. Estimado 2× → ~2780 medios.
- Nulos: `latitude` 10 (0.7%), `timestamp_utc` 29 (2.1% = textos sin fecha), `color_1_hex` 343 (24.7% = videos/audio/text), `sun_elevation` 73 (5.2%), `distance_from_prev_m` 74 (5.3%).
- `media_metadata` por clave: `dia_semana` 1318, `weather_*` 1318, `ia_description/en` 1048, `ia_keywords/en` 1048, `ia_keywords_transcripcion` 162, `ia_keywords_texto` 23, `ia_keywords_sonido` 272, `video_analysis` 139, `ia_sonido_raw` 290. `whisper_estado` 0 (pero `whisper_segments` 218 → estado no poblado), `contenedor_estado` 0, `keypoints_video_estado` 0.
- `media_keypoints`: transcription 1367, ubicacion_video 376, contexto_* 270 c/u (movimiento 77).
- `tracks` 1, `waypoints` 28, `telegram_messages` 1744, `telegram_media` 826, `media_embeddings` 808.
- `twilight_period`: dia 939, noche 124, blue 67, golden 61, civil 58, astro 38, nautico 31, NULL 73.
- Tests: `test_gradiente` ✅ 32 verifs, `test_motor_loop` ✅ 47 ok, `db/test_migrate` ✅ 8 passed (corregido `db/migrate.py:184` guard sin tabla media).
- Deploy: `deploy/db/visualizacion.db` 2.4 MB existe, `deploy/index.html` 1.3 KB, `td/chat_fluir.html` 2.9 MB, `td/textos_fluir.html` 13 KB. `mapas_municipio/` no existe (no generado en esta máquina).
- Estado B1/M1: ✅ resueltos en esta sesión (B1 lock, M1 5 claves).
- Estado final 2026-08-28: **toda la auditoría completada** — B1, M1-M2, D1-D5, L1-L3, L5, L7 ✅. L4 parcialmente (restan `except: pass` intencionales). Tests 8/8, 32/32, 47/47. Listo para ingesta 2× y demo deploy+TD.
