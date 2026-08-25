# Flujos — Documentación del Proyecto (referencia de agentes)

Instalación interactiva que documenta un viaje Buenos Aires → Tucumán en bicicleta.
Pipeline de ingesta, enriquecimiento, consulta y exportación de metadatos multimedia
con SQLite como índice central y TouchDesigner como motor de reproducción.

Idioma del proyecto: **español** (variables, comentarios, commits, menús).
Este archivo es contexto de alto valor para agentes; la info detallada vive en los archivos
referenciados en **Dónde está la información** (al final) — consultar bajo demanda.

---

## Stack y herramientas

| Herramienta | Uso |
|---|---|
| Python | Scripts del pipeline (`scripts/`) |
| ffmpeg / ffprobe | Transcode y análisis de medios |
| ExifTool | EXIF/IPTC/XMP |
| Ollama | Servicio en background; modelos de visión, texto y embeddings |
| deep-translator (Google) / argos-translate (offline) | Motores clásicos del pipeline NO-AI de traducción EN→ES (`glosario.py`) |
| faster-whisper | Transcripción de audio/video |
| sherpa-onnx + onnxruntime | Audio tagging local (CED-mini, 527 clases AudioSet) |
| Pillow + webcolors | Colores dominantes, thumbnails, naming CSS → español |
| numpy | Cross-correlación de audio (`repetir_contenido.py`, `audio_frame_crossref.py`) y clustering de embeddings |
| SQLite | Base embebida (`db/flujos.db`) |

**Modelos por tarea**: visión=`minicpm-v4.6:latest`, traducción EN→ES=**NO-AI** (`glosario.py` + motor clásico — Google vía `deep_translator` por defecto, Argos offline opcional; `--motor ollama` conserva el pipeline legacy `translategemma`), curación/limpieza=`moondream:latest`, embeddings=`nomic-embed-text`, transcripción=`faster-whisper` (modelo `small`), audio tagging=`sherpa-onnx CED-mini` (local), keywords del sentido (texto)=`gemma3:latest` (Ollama, ganó A/B Ago 2026 contra qwen2.5:3b).

**Audio tagging**: `pip install onnxruntime sherpa-onnx`; el modelo CED-mini se auto-descarga en el primer uso de `audio_tagging.py` (`--no-descargar` para deshabilitar). Detalle en @README.md.

---

## Estructura del proyecto

```
/
├── flujos.py                 # Entry point: TUI + CLI routing
├── gui_fluir.py              # GUI tkinter "Fluir": selector de medios → envío OSC 9002 (delega en puente_td)
├── opencode.json
├── AGENTS.md                 # Este archivo
├── CHANGELOG.md
├── VISION.md
├── README.md
├── ROADMAP.md
├── db/
│   ├── schema.sql            # Schema completo (fuente de verdad)
│   ├── migrate.py            # Migraciones centralizadas v1→v4
│   ├── util.py               # abrir/conectar/resolver_db + ModoHelper
│   └── flujos.db             # Base de datos (no versionada)
├── scripts/
│   ├── ingest.py, improve_db.py, query.py, relocate.py, geocode.py,
│   │   gradiente.py, astronomia.py, limpiar_tandas.py, fetch_weather.py,
│   │   dia_semana.py, color_utils.py, ingest_gpx.py, import_telegram.py,
│   │   exportar_csv.py, mover_media.py, mapa_ruta.py, mapas_municipio.py,
│   │   track_gpx.py, tiles_offline.py, consolidar_medios.py,
│   │   fix_gps_sign.py, mover_descartadas.py, ingest_textos.py,
│   │   keypoints_contexto.py, detectar_contenedores.py,
│   │   repetir_contenido.py, audio_frame_crossref.py, exportar_visualizacion.py,
│   │   limpiar_descripciones.py, limpiar_stickers.py, inferir_hora_textos.py,
│   │   diagnosticar_camaras_360.py, ubicar_videos_gpx.py
│   ├── check_db.py, check_gps.py, check_db_data.py, test_gradiente.py
│   ├── ai_media/
│   │   ├── ollama_client.py, image_analysis.py, transcribe.py, transcribe_media.py,
│   │   │   traducir_metadata.py, analyze_video.py, keypoints_video.py, tag_images.py,
│   │   │   batch_selector.py, clustering.py, generate_embeddings.py, refinar_keywords.py,
│   │   │   keywords_transcripciones.py, audio_tagging.py, checkpoint.py, proxy.py,
│   │   │   glosario.py, generar_glosario.py, generar_sinonimos_localidades.py
│   │   └── loop_engine.py, loop_db.py, test_motor_loop.py
│   └── td/
│       └── puente_td.py, elecciones.py, osc_probe.py, util_enter.py
├── td/
│   ├── osc_callbacks.dat, elecciones_ui.dat,
│   │   fluir_callbacks.dat, crear_tablas_fluir.dat, flujos.toe
├── docs/
│   ├── diseno_instalacion.md, motor_loop.md, arquitectura_motor.md, flujo_de_medios.md,
│   │   linea_de_tiempo.md, geocodificacion_reversa.md, semantica_color.md, calculo_astronomico.md,
│   │   visualizaciones.md, limpieza_tandas_resultados.md, ideas_externas.md, lecciones_elecciones_td.md, inferencia_autor.md, armado_de_tandas.md,
│   │   deploy.md, retorno_fluir_td.md, videos_360_web.md, embeddings_rediseno.md
├── models/audio/
│   └── sherpa-onnx-ced-mini-audio-tagging-2024-04-19/   # Modelo auto-descargado
└── .opencode/
    ├── agents/  (orquestador, touchdesigner, gis, ia-media)
    └── skills/  (sqlite, ffmpeg, exiftools, ia-media, python-media, touchdesigner)
```

---

## Base de datos (`db/flujos.db`)

Schema completo y versionado en `db/schema.sql` (SQLite WAL, foreign_keys=ON; migraciones en `db/migrate.py`, v1→v4).

| Tabla | Propósito | Columnas / claves CLAVE |
|---|---|---|
| `media` | Tabla principal (~55 cols): identidad, hashes, sidecar Sony, tiempo, GPS, geocode, gradientes, autor, colores, control | `timestamp_utc`, `latitude`/`longitude` (**NEGATIVAS en Argentina**), `filepath_absoluto` UNIQUE, `file_hash` UNIQUE, `telegram_message_id` FK |
| `media_metadata` | key-value por medio | Claves usadas: `ia_keywords`, `ia_keywords_en`, `ia_description`, `ia_description_en`, `whisper_segments`, `whisper_info`, `whisper_estado`, `weather_*`, `dia_semana`, `ia_keywords_transcripcion`, `ia_keywords_texto`, `ia_keywords_sonido`, `ia_sonido_raw`, `video_analysis`, `contenedor_estado`, `contenedor_streams`, `keypoints_video_estado`, `keypoints_contexto_estado`, `ubicacion_video_estado`, `ubicacion_video_gaps`, `texto_completo`, `titulo_seccion`, `indice_seccion`, `origen_seccion`, `texto_tags`, `texto_ubicacion`, `xml_*`, `xmp_*`, `video_spherical_projection`, `sony_device_*` |
| `media_keypoints` | Segmentos individuales con timestamp | `timestamp_offset_secs`, key=`transcription` (whisper), `contexto_*` (contexto geográfico), `escena`/`keyword` (análisis de video), `ubicacion_video` (posición por tramo) |
| `media_embeddings` | Vectores de búsqueda semántica | `modelo` (default nomic-embed-text), UNIQUE(media_id, modelo) |
| `config` | key-value global | `ingest_root`, `current_ingest_batch` |
| `tracks` | Archivos GPX ingeridos | `name`, `filepath_absoluto`, `start_time`, `end_time`, `total_points` |
| `waypoints` | Puntos de interés de un track | `name`, `category`, `type`, `latitude`, `longitude` |
| `telegram_chats` / `telegram_messages` / `telegram_media` | Exports de Telegram; multimedia vinculado bidireccionalmente a `media` | UNIQUE(chat_id, message_id); `telegram_media.media_id` ↔ `media.id` |

**Advertencia**: `media.localidad` SIEMPRE NULL — la API Georef no devuelve localidad (solo provincia/departamento/municipio). Ver `docs/geocodificacion_reversa.md`.

---

## Mapa de datos: qué script escribe qué (FUENTE ÚNICA DE VERDAD)

Cada script del pipeline escribe datos específicos en la DB. Esta tabla centraliza
**qué datos genera cada etapa, en qué tabla y en qué columnas/claves**:

| Etapa | Script | Datos | Tabla | Columnas / Claves |
|---|---|---|---|---|
| **INGESTA** | `ingest.py` | Metadatos de archivo (nombre, ruta, tamaño, tipo, subtipo) | `media` | filename_original, filepath_absoluto, filepath_relativo, carpeta, type, subtype, size_bytes |
| | | Huellas digitales (SHA-256) | `media` | file_hash, content_hash |
| | | Sidecar Sony XML | `media` | sidecar_xml, sidecar_parsed, sidecar_hash |
| | | Timestamp original + UTC normalizado | `media` | timestamp_original, timestamp_utc, timezone_note |
| | | Duración (videos/audios) | `media` | duration_secs |
| | | GPS (lat, lon, altitud, fuente) | `media` | latitude, longitude, altitude, geolocation_source |
| | | Autor | `media` | author, author_source |
| | | Colores dominantes (3 slots: hex, nombre CSS, categoría básica) | `media` | color_{1,2,3}_hex, color_{1,2,3}_name_css, color_{1,2,3}_name_basic |
| | | Control de ingesta (fecha, batch) | `media` | ingested_at, ingest_batch_id |
| **COLORES** | `improve_db.py --step colors` | Reprocesa colores dominantes (modos skip/update/replace) | `media` | color_{1,2,3}_hex, color_{1,2,3}_name_css, color_{1,2,3}_name_basic, updated_at |
| **KEYWORDS** | `improve_db.py --step keywords` | Palabras clave IA (visión EN → traducción ES, 2 fases) | `media_metadata` | key=`ia_keywords` (ES definitivo), value=texto coma-separado; key=`ia_keywords_en` (intermedio EN) |
| **DESCRIPTION** | `improve_db.py --step descriptions` | Descripción IA (visión EN → traducción ES, 2 fases) | `media_metadata` | key=`ia_description` (ES definitivo); key=`ia_description_en` (intermedio EN) |
| **COMBINADO** | `improve_db.py --step combinado` | Keywords + descripción en UNA llamada de visión (EN) + 1 de traducción (ES) | `media_metadata` | keys `ia_keywords_en`/`ia_description_en` + `ia_keywords`/`ia_description` (ES) |
| **TRANSLATE** | `traducir_metadata.py` | Traducción EN→ES independiente sobre la DB (re-ejecutable sin re-correr visión); **NO-AI** por defecto: glosario (`glosario.py`) + motor clásico (Google `deep_translator` | Argos offline); `--motor ollama` conserva el legacy translategemma | `media_metadata` | `ia_keywords`/`ia_description` desde `ia_keywords_en`/`ia_description_en` |
| **TRANSCRIBE** | `improve_db.py --step transcribe` | Transcripción completa de audio/video con **VAD** (detecta persona hablando, descarta ruido/silencio) + filtro de confianza | `media_metadata` | keys `whisper_segments` (JSON [{inicio, fin, texto, promedio_logprob, no_hay_habla_prob, ratio_compresion}]), `whisper_info` ({language, language_probability}), `whisper_estado` (ok\|sin_voz) |
| **KEYPOINTS** | `improve_db.py --step keypoints` | Segmentos individuales de transcripción con timestamp | `media_keypoints` | media_id, timestamp_offset_secs, timestamp_absolute, key=`transcription`, value=texto, source |
| **TIMESTAMPS** | `improve_db.py --step timestamps` | Timestamps inferidos desde EXIF/ExifTool | `media` | timestamp_original, timestamp_utc, timezone_note, updated_at |
| **GPS** | `improve_db.py --step gps` | GPS inferido desde EXIF/ExifTool | `media` | latitude, longitude, altitude, geolocation_source, updated_at |
| **VIDEO_METADATA** | `improve_db.py --step video_metadata` | ExifTool en videos (cámara, 360°, author) | `media` + `media_metadata` | subtype, author, geolocation_source, updated_at; claves `media_metadata`: `xml_*` (`xml_devicemanufacturer`, `xml_devicemodelname`), `xmp_*` y `video_spherical_projection` (detección 360), además de `sony_device_*` de la ingesta |
| **GEOCODE** | `geocode.py` | Provincia, departamento, municipio (Georef API Argentina; ⚠️ localidad siempre NULL, la API no la devuelve) | `media` | provincia, departamento, municipio, localidad (NULL), geocode_source, geocode_date |
| **WEATHER** | `fetch_weather.py` | Clima histórico (Open-Meteo ERA5-Land) | `media_metadata` | keys: weather_temp_c, weather_humidity_pct, weather_precip_mm, weather_cloud_pct, weather_code, weather_label, weather_wind_speed_kmh, weather_wind_dir_deg, weather_wind_dir_text, weather_pressure_hpa, weather_hour_utc, weather_source |
| **DÍA SEMANA** | `dia_semana.py` | Día de la semana en español | `media_metadata` | key=`dia_semana`, value=lunes\|martes\|...\|domingo |
| **GRADIENTES** | `gradiente.py` | Distancia Haversine, cambio elevación, pendiente % y acumulados | `media` | distance_from_prev_m, elevation_gain_m, gradient_pct, cumul_distance_m, cumul_elevation_gain_m |
| **ASTRONOMÍA** | `astronomia.py` | Posición del sol (NOAA), clasificación twilight, amanecer/atardecer/cenit, tiempos relativos | `media` | sun_elevation, sun_azimuth, sun_distance_au, twilight_period, sunrise_ts, sunset_ts, solar_noon_ts, secs_since_sunrise, secs_to_sunset, secs_since_noon, astronomy_source |
| **KEYWORDS TRANSCRIPCIÓN** | `ai_media/keywords_transcripciones.py --origen transcripcion` (default) | Keywords del SENTIDO de la transcripción (Ollama texto, gemma3:latest) | `media_metadata` | key=`ia_keywords_transcripcion`, value=keywords ES separadas por coma (fuente: whisper_segments) |
| **KEYWORDS TEXTOS** | `ai_media/keywords_transcripciones.py --origen texto` | Keywords del SENTIDO de los textos ingresados (Ollama texto, gemma3:latest) | `media_metadata` | key=`ia_keywords_texto`, value=keywords ES separadas por coma (fuente: texto_completo) |
| **AUDIO TAGGING** | `ai_media/audio_tagging.py` | Sonidos ambientales en audio/video (sherpa-onnx CED-mini, 527 clases AudioSet, local) | `media_metadata` | key=`ia_keywords_sonido` (ES, texto coma-separado); key=`ia_sonido_raw` (JSON [{name, prob}]) |
| **ANÁLISIS VIDEO** | `ai_media/analyze_video.py` | Análisis visual por escenas: scene detection → muestreo ~10 imgs/escena → nitidez → 1 llamada de visión por escena (keywords + descripción) | `media_metadata` | key=`video_analysis` (JSON: escenas [{indice, inicio, fin, duracion, keywords, descripcion, fotogramas}], fotogramas, modelo, fecha) |
| **KEYPOINTS VIDEO** | `ai_media/keypoints_video.py` | Keypoints semánticos por escena (post-análisis de video) | `media_keypoints` | key=`escena` (keywords de la escena, coma separada), key=`keyword` (keyword individual), value=texto, source='ollama' |
| | | Sentinel de procesado (evita reprocesar videos sin keypoints en skip) | `media_metadata` | key=`keypoints_video_estado`, value=ok\|sin_datos |
| **KEYPOINTS CONTEXTO** | `keypoints_contexto.py` | Keypoints de contexto (devenir geográfico): F1 interpola track GPX, F2 transiciones elevación/astronomía/movimiento, F3 georef+clima con cache, F4 escribe cambios | `media_keypoints` | key=`contexto_elevacion`, `contexto_astronomia`, `contexto_ubicacion`, `contexto_clima`, `contexto_movimiento`; value=descripción ES; source=`track_interpolado`\|`estimado`\|`gps_propio` |
| | | Sentinel de procesado (evita reprocesar medios sin posición en skip) | `media_metadata` | key=`keypoints_contexto_estado`, value=ok\|sin_datos |
| **AUDITORÍA CONTENEDORES** | `detectar_contenedores.py` | Estado del contenedor de video/audio con ffprobe (streams faltantes) | `media_metadata` | key=`contenedor_estado`, value=ok\|sin_video\|sin_audio\|sin_contenido\|error_ffprobe\|archivo_faltante; key=`contenedor_streams` (JSON detalle de streams) |
| **UBICAR VIDEOS GPX** | `ubicar_videos_gpx.py` | Ubica videos 360° interpolando su intervalo temporal contra el track GPX (colapsa momentos detenidos: umbral 5 km/h + distancia mínima 100 m; muestreo cada `--intervalo` s). **Gaps**: las muestras dentro de un hueco del track > `--umbral-gap` (default 1800 s = 30 min) NO se emiten (evita posiciones falsas interpoladas a través del hueco); si un video queda sin cobertura, en `update`/`replace` se limpia su `latitude`/`longitude` previo de track | `media` | latitude, longitude, altitude (primera posición emitida con track real), geolocation_source='track_interpolado' |
| | | Keypoints por tramo muestreado | `media_keypoints` | key=`ubicacion_video`, value=`lat,lon[,ele]` (6 decimales), source='track_interpolado' |
| | | Sentinel de procesado | `media_metadata` | key=`ubicacion_video_estado`, value=ok\|sin_datos\|fuera_rango\|sin_track; key=`ubicacion_video_gaps` (JSON lista de gaps) |
| **AUDIO REPETIDO** | `repetir_contenido.py` | Detecta pasajes de audio repetidos entre pares de medios (cross-correlación RMS; **solo reporta**, no escribe) | — | — (reporte por consola / `--json`) |
| **CROSSREF AUDIO-FRAME** | `audio_frame_crossref.py` | Correlaciona sonidos (CED-mini) con frames del video (**solo reporta**, no escribe) | — | — (reporte por consola) |
| **LIMPIEZA DESCRIPCIONES** | `limpiar_descripciones.py` | Recorta meta-intros (eco del prompt) en descripciones; determinista, sin IA; backup automático + `--dry-run`; invariante: ningún registro con apertura legítima modificado | `media_metadata` | keys `ia_description_en` / `ia_description` (recorta PREFIJOS_META_EN de image_analysis.py + PREFIJOS_META_ES local); NO escribe claves nuevas |
| **BACKFILL** | `flujos.py` backfill-end-time | Precalcula end_time = timestamp_utc + duration_secs | `media` | end_time, updated_at |
| **RELOCATE** | `relocate.py` | Actualiza rutas cuando los archivos se mudan de carpeta | `media` | filepath_absoluto, filepath_relativo, carpeta, sidecar_xml |
| **GPX** | `ingest_gpx.py` | Ingesta de archivo GPX: waypoints, registro de track y backfill de altitud | `tracks` | name, filepath_absoluto, filepath_relativo, source_url, start_time, end_time, total_points |
| | | | `waypoints` | name, description, category, type, latitude, longitude |
| | | | `media` | altitude, geolocation_source='track_gps' |
| **TELEGRAM** | `import_telegram.py` | Importa export de Telegram: chats, mensajes, multimedia vinculado | `telegram_chats` | telegram_id, name, chat_type, export_path |
| | | | `telegram_messages` | message_id, type, message_type, es_sistema, from_name, from_id, text, date_unixtime, date_utc, edited_unixtime, reply_to_message_id, reactions, hashtags, action, members |
| | | | `telegram_media` | media_type, file_relative_path, mime_type, file_size, width, height, duration_seconds, media_id |
| | | | `media` | telegram_message_id (FK), columna agregada vía ALTER TABLE |
| **TEXTOS** | `ingest_textos.py` | Ingiere textos `.md` de la carpeta `textos/` como medios type='text' (cada subtítulo `##` = un texto; sin subtítulos = un solo texto; plantilla en `textos/textos.md`) | `media` | filename_original, filepath_absoluto, filepath_relativo, carpeta, type='text', subtype='md', size_bytes, file_hash (SHA-256 del FRAGMENTO, único por texto), content_hash, timestamp_original, timestamp_utc, author |
| | | | `media_metadata` | keys: `texto_completo`, `titulo_seccion`, `indice_seccion`, `origen_seccion` (archivo::índice, clave estable de identidad), `texto_tags`, `texto_ubicacion` |
| **INFERIR HORA TEXTOS** | `inferir_hora_textos.py` | Timestamp de textos `type='text'` sin fecha interpolando SU PUNTO (lat/lon) contra el track GPX (posición → tiempo, ponderación por distancia inversa); `--umbral` (default 2000 m) descarta textos lejos de la ruta. ⚠️ 2026-08-17: los textos de viajeros.md son crónicas históricas (1729–2024), NO parte del viaje 2025 — no se les infiere timestamp (aplicado y revertido). Usar solo para textos que pertenezcan al viaje. | `media` | timestamp_original, timestamp_utc, geolocation_source='track_interpolado' |

> **Nota**: las operaciones que modifican la DB soportan `--mode skip|update|replace`.
> - `skip`: solo procesa registros donde el dato es NULL
> - `update`: actualiza todos los registros (sobrescribe)
> - `replace`: limpia los datos existentes primero, luego regenera
>
> **Excepciones**: `inferir_hora_textos.py` y `mapas_municipio.py` solo soportan
> `skip|update` (no `replace`). `consolidar_medios.py` y `mover_media.py` usan
> `--mode mover|copiar` (semántica distinta, no es skip/update/replace).

### Catálogo de scripts — propósito e integración

Detalle de args CLI de cada script en su **docstring** (o `python script.py --help`).

| Script | Propósito | Integración TUI / CLI |
|---|---|---|
| `flujos.py` | Entry point único: TUI (6 submenús + Ayuda) + CLI routing | `python flujos.py <comando>`; sin args abre TUI |
| `gui_fluir.py` | GUI tkinter "Fluir": selector de medios (horas/municipios/colores/tags con scroll, filtro, contadores, Todo/Nada) que delega el envío por 9002 en `puente_td._procesar_rafaga`; reemplaza chips de TD y listener 9001 | Standalone: `python gui_fluir.py` |
| `db/util.py` | Conexiones DB (`abrir`, `conectar`, `resolver_db`) + `ModoHelper` | `from db.util import abrir, conectar, resolver_db, ModoHelper` |
| `ingest.py` | Escanea carpeta, extrae metadatos/hashes/GPS, inserta en `media` | TUI Ingesta→1; CLI `flujos.py ingest --root <ruta>` |
| `improve_db.py` | 9 pasos post-ingesta (colors, keywords, descriptions, combinado, transcribe, keypoints, timestamps, gps, video_metadata) | TUI Mejorar DB (3 hojas; embeddings retirado del TUI); CLI `flujos.py improve-db --steps X --mode Y` |
| `query.py` | Consultas a DB (distinct, search, where, count, columns, key) | TUI Consultar→Listar; CLI `flujos.py query` |
| `relocate.py` | Actualiza rutas cuando los archivos se mudan | TUI Mantenimiento→1; CLI `flujos.py relocate` |
| `geocode.py` | Geocodificación inversa (Georef API Argentina, batch) | TUI Mejorar DB Hoja 2→6; CLI `flujos.py geocode` |
| `gradiente.py` | Distancia Haversine, elevación, pendiente entre GPS consecutivos | TUI Hoja 2→5; CLI `flujos.py gradient` |
| `astronomia.py` | Posición del sol (NOAA) + clasificación twilight | TUI Hoja 2→9 / Mantenimiento→2; CLI `flujos.py astronomia` |
| `color_utils.py` | Colores dominantes (Pillow) + naming webcolors→español | Usado por `improve_db --step colors` |
| `limpiar_tandas.py` | Limpieza de tandas/bursts; mueve descartadas a `excluir/` | TUI Preparar→1; standalone |
| `fetch_weather.py` | Clima histórico Open-Meteo ERA5-Land | TUI Hoja 2→7; standalone |
| `dia_semana.py` | Día de la semana desde `timestamp_utc` | TUI Hoja 2→8; standalone |
| `ingest_gpx.py` | Ingesta de track GPX (tracks, waypoints, backfill altitud) | TUI Ingesta→2 |
| `import_telegram.py` | Importa exports de Telegram (chats, mensajes, multimedia) | TUI Ingesta→4; CLI `flujos.py import-telegram` / `tg` |
| `ingest_textos.py` | Ingiere textos `.md` de `textos/` como medios type='text' (frontmatter + subtítulos `##` = textos individuales) | TUI Ingesta→5; CLI `flujos.py ingest-textos` / `textos` |
| `exportar_csv.py` | Exporta tablas a CSV en `db/exports/<timestamp>/` | TUI Mantenimiento→7; CLI `flujos.py export-csv` |
| `mapa_ruta.py` | Mapa HTML interactivo (Folium): la línea de ruta usa el **track GPX** (`tracks`, helpers de `track_gpx.py`); los medios quedan como marcadores con su GPS propio. `--road-colors` colorea segmentos del track por pendiente; `--tolerancia-metros` (default 1000) reporta discrepancias media vs track; `--umbral-gap-aviso` (default 1800) marca en naranja los medios con gap del track ≥ umbral (posición incierta, aviso en el popup). Flags: `--no-markers`, `--output/-o`. Heatmap eliminado | TUI Visualizaciones→1; CLI `flujos.py mapa` |
| `mapas_municipio.py` | Genera un mapa HTML por municipio recorrido, con variantes (`ruta`, `puntos`, `contexto`, `gradiente`). La línea de ruta de `ruta`/`contexto`/`gradiente` usa el **tramo del track GPX** por rango temporal de los medios del municipio (helpers de `track_gpx.py`); los medios quedan como marcadores. Nombre: `mapa_municipio_<municipio>_<variante>.html` (slug ASCII: espacios→`_`, **sin acentos** ni símbolos — `'Río Hondo'`→`Rio_Hondo`; convención para evitar problemas de visualización en TouchDesigner). **Tiles de la vista inicial incrustados como data URIs** (capa única vía `tiles_offline.py`): la vista inicial se muestra al instante en TD sin descargar de internet; el zoom/pan posterior carga de CartoDB online. `--mode skip` genera solo los faltantes en disco, `--mode update` (default) regenera todos, `--no-embebido` deshabilita la incrustación. Flag `--tolerancia-metros` (default 1000) para reportar discrepancias media vs track; `--umbral-gap-aviso` (default 1800) marca en naranja los medios con gap del track ≥ umbral (posición incierta, aviso en el popup). Otros flags: `--variantes`, `--municipio` (substring), `--zooms`, `--tiles-cache`, `--output/-o`, `--dry-run` | TUI Visualizaciones→1→2; CLI `flujos.py mapa-municipios` / `mapas` |
| `tiles_offline.py` | Predescarga de tiles de CartoDB positron para los mapas por municipio: `tiles_en_bounds`, `zoom_fit_bounds` (replica el zoom de `fitBounds`), `descargar_tiles_png` (cache en `tiles_cache/`, compartido entre municipios), `data_uris`, `js_capa_base_embebida` (JS de capa única con polling que resuelve data URI→embedded / online). Usado por `mapas_municipio.py`; standalone: `python scripts/tiles_offline.py lat0 lon0 lat1 lon1` para precargar la cache | Importado por `mapas_municipio.py` |
| `track_gpx.py` | Helpers compartidos de tracks GPX: `cargar_tracks` (relee los .gpx de `tracks`), `puntos_track_con_tiempo`, `interpolar_posicion`, `tramo_temporal`, `distancia_haversine` (metros), `medir_discrepancias`/`reportar_discrepancias` (media vs track con tolerancia). Usado por `mapa_ruta.py` y `mapas_municipio.py`; el track NO se persiste en DB, se relee el .gpx | Importado por los scripts de mapas |
| `exportar_visualizacion.py` | Exporta snapshot de la DB → visualizacion.db; deploy genérico a deploy/ (por defecto) con copia de medios y transcode web | TUI Visualizaciones→2; CLI no (solo TUI) |
| `td/puente_td.py` | Puente BD → TouchDesigner vía OSC (9000→TD, 9001←TD, 9002→TD resultado). Modos: `elecciones` (default) y `fluir` — el modo instalación escucha sin límite de tiempo hasta Enter; en `fluir`, si hay municipios elegidos, emite además el chat de Telegram (`/mensaje` ×N → tabla `fluir_telegram`, criterio web: rango de fechas de los medios, `es_sistema=0`, hora local UTC−3, texto ≤250 chars) y las rutas de mapas por municipio (`/mapa` ×N → tabla `fluir_mapas`, ruta al HTML de `scripts/mapas_municipio.py`); flags `--no-enviar-telegram`, `--no-enviar-mapas`, `--no-enviar-medios`, `--una-vez`, `--debounce`, `--loop-secs`, `--spec-salida`, `--grupo` | TUI Visualizaciones→3; CLI `python scripts/td/puente_td.py <modo>` |
| `td/elecciones.py` | Nubes de elecciones (metadatos seleccionables: horas, municipios, colores, tags, días, clima) → TD vía OSC (9000) | Standalone: `python scripts/td/elecciones.py` |
| `td/osc_probe.py` | Eco OSC: escucha lo que llega a un puerto y lo imprime. Test rápido TD→Python sin puente completo; el modo indefinido se detiene con Enter | TUI Visualizaciones→3; standalone: `python scripts/td/osc_probe.py 9001 [segundos]` |
| `td/util_enter.py` | Helper compartido: `detener_con_enter()` devuelve un `threading.Event` que se setea al presionar Enter (salida limpia para escuchas continuas) | Usado por `puente_td.py` (fluir) y `osc_probe.py` |
| `mover_media.py` | Mueve/copia medios y actualiza rutas en DB | TUI Mantenimiento→8; CLI `flujos.py mover` |
| `consolidar_medios.py` | Consolida medios de múltiples raíces absolutas en una estructura unificada (copiar/mover + actualizar DB + `ingest_root`) | Standalone: `python scripts/consolidar_medios.py --new-root <carpeta> [--mode mover\|copiar]` |
| `mover_descartadas.py` | Mueve imágenes descartadas a la carpeta `excluir/` (post-limpieza de tandas) | Usado por `limpiar_tandas` |
| `ai_media/ollama_client.py` | Cliente Ollama compartido (visión/texto/embeddings) + auto-inicio `asegurar_ollama()` | Usado por todos los scripts IA |
| `ai_media/image_analysis.py` | Keywords + descripción de imágenes (visión minicpm, prompts EN) | Usado por `improve_db --step keywords/descriptions` |
| `ai_media/transcribe.py` / `transcribe_media.py` | Transcripción faster-whisper (independiente / desde DB) | `transcribe.py` usado por `--step transcribe` (importa `transcribir_audio`/`clasificar_estado`); `transcribe_media.py` standalone |
| `ai_media/traducir_metadata.py` | Traduce EN→ES sobre la DB (re-ejecutable sin re-correr visión); **NO-AI** por defecto — glosario + motor clásico (`--motor google` default vía `deep_translator`, `argos` offline, `glosario` solo léxico; `--motor ollama` = legacy translategemma) | Standalone: `python scripts/ai_media/traducir_metadata.py --paso keywords --mode update` |
| `ai_media/analyze_video.py` | Análisis de videos con IA: scene detection → ~10 imgs/escena → nitidez → 1 llamada PROMPT_COMBINADO por escena (máx 20 tags); flags `--por-escena`/`--mejores-por-escena` (eliminado `--interval`) | TUI Hoja 3→1; CLI `flujos.py analizar-video` / `analizar` |
| `ai_media/keypoints_video.py` | Keypoints semánticos de video: `media_keypoints` key=`escena`/`keyword`, source='ollama' (desde `video_analysis`); sentinel `keypoints_video_estado` | Standalone (post-`analyze_video`): `python scripts/ai_media/keypoints_video.py [--mode]` |
| `keypoints_contexto.py` | Keypoints de contexto (devenir geográfico): F1 interpola track GPX, F2 transiciones (elevación/astronomía/movimiento), F3 georef+clima con cache, F4 escribe `contexto_*` | TUI Hoja 3→2; CLI `flujos.py keypoints-contexto` / `keypoints` |
| `detectar_contenedores.py` | Audita contenedores de video/audio con ffprobe (streams faltantes); anota `contenedor_estado`/`contenedor_streams` | TUI Mantenimiento→9; CLI `flujos.py detectar-contenedores` / `contenedores` |
| `repetir_contenido.py` | Detecta contenido repetido por audio (cross-correlación RMS; solo reporta, no escribe). Flags: `--contra`, `--limite`, `--umbral` (def 0.80), `--min-duracion-segs` (def 4), `--top` (def 20), `--json` | TUI Mantenimiento Hoja 2→1; CLI `flujos.py repetir-contenido` / `repetidos` |
| `audio_frame_crossref.py` | Correlaciona audio (CED-mini) con frames de video (solo reporta, no escribe) | TUI Mantenimiento Hoja 2→2; CLI `flujos.py audio-frame` / `crossref` |
| `limpiar_descripciones.py` | Recorta meta-intros (eco del prompt) en `ia_description_en`/`ia_description`; determinista, sin IA, backup automático + `--dry-run`; invariante: ningún registro con apertura legítima modificado. Flags: `--solo-en`/`--solo-es`, `--no-backup` | TUI Mantenimiento Hoja 3→1; CLI `flujos.py limpiar-descripciones` / `descripciones` |
| `limpiar_stickers.py` | Elimina stickers de Telegram mal ingeridos en `media` (dry-run + backup automático en `db/backups/`) | Standalone: `python scripts/limpiar_stickers.py [--dry-run]` |
| `fix_gps_sign.py` | Corrección de signo GPS (las coordenadas argentinas son negativas; herramienta de mantenimiento) | Standalone: `python scripts/fix_gps_sign.py [--dry-run]` |
| `diagnosticar_camaras_360.py` | Diagnóstico de relojes de cámaras Insta360 en videos 360°: identifica cámara A/B por bitrate+fps, deduce hora real (embebido `QuickTime:CreateDate`=UTC → −3h), flaggea relojes reconfigurados y filenames atrasados 30 min. Ver `docs/discrepancia_horarios_camaras.md` | Standalone: `python scripts/diagnosticar_camaras_360.py --root <carpeta> [--solo-resumen] [--json]`; **NO TUI** (decisión usuario 2026-08-17) |
| `inferir_hora_textos.py` | Infiere timestamp de textos `type='text'` sin fecha interpolando su posición contra el track GPX (posición → tiempo; `--umbral` default 2000 m) | Standalone: `python scripts/inferir_hora_textos.py [--mode] [--umbral N]` |
| `ubicar_videos_gpx.py` | Ubica videos 360° (y videos en general) interpolando su intervalo temporal contra el track GPX: colapsa momentos detenidos (umbral 5 km/h + distancia mínima 100 m), muestrea cada `--intervalo` s, guarda lat/lon/alt inicial + keypoints `ubicacion_video` por tramo y sentinel `ubicacion_video_estado`/`ubicacion_video_gaps`. Ver `docs/discrepancia_horarios_camaras.md` (convención: track GPX = UTC real, videos 360 con `timestamp_utc` corregido) | Standalone: `python scripts/ubicar_videos_gpx.py [--solo-360] [--mode skip|update|replace] [--intervalo N] [--umbral-movimiento kmh] [--distancia-minima m] [--umbral-gap s] [--sobrescribir-gps] [--dry-run]`; **NO TUI** (decisión usuario 2026-08-17) |
| `ai_media/tag_images.py` | Taggear imágenes (DB o sidecar) | Standalone |
| `ai_media/batch_selector.py` | Selecciona mejor imagen de tanda (moondream; criterio `nitidez` sin IA) | Usado por `limpiar_tandas` |
| `ai_media/clustering.py` | Agrupa por tags/embeddings (moondream, prompts EN) | Usado por `limpiar_tandas` |
| `ai_media/generate_embeddings.py` | Embeddings multi-fuente (nomic-embed-text, MAX 6000 chars) | Retirado del TUI (standalone; rediseño pendiente, ver ROADMAP) |
| `ai_media/refinar_keywords.py` | Refina/unifica keywords (léxico + diccionario de sinónimos) | TUI Hoja 2→2; standalone |
| `ai_media/keywords_transcripciones.py` | Keywords del SENTIDO desde transcripciones (`--origen transcripcion`, default → `ia_keywords_transcripcion`) o desde textos .md ingresados (`--origen texto` → `ia_keywords_texto`) | TUI Hoja 2→1; standalone |
| `ai_media/audio_tagging.py` | Sonidos ambientales (sherpa-onnx CED-mini, local) | TUI Hoja 1→7 |
| `ai_media/glosario.py` | Glosario EN→ES persistente (JSON en raíz: `glosario_keywords.json`) + motores clásicos (`deep_translator` Google default / Argos offline). Traducción NO-AI de keywords/descripciones; reemplazos rioplatenses post-traducción | Usado por `traducir_metadata.py` e `improve_db.py` (pipeline NO-AI) |
| `ai_media/generar_glosario.py` | Genera/amplía el glosario desde fuentes manuales + DB (pares alineados `ia_keywords_en`/`ia_keywords`) y opcional `--extender --motor google|argos` | Standalone: `python scripts/ai_media/generar_glosario.py [--extender --motor google]` |
| `ai_media/generar_sinonimos_localidades.py` | Propone sinónimos de localidades cruzando tags observados en `--clave` (default `ia_keywords`) contra georef/contexto | Standalone: `python scripts/ai_media/generar_sinonimos_localidades.py [--clave ia_keywords]` |
| `ai_media/checkpoint.py` | Checkpoint + detención limpia para procesos IA | Usado por improve_db y otros |
| `ai_media/proxy.py` | Redimensiona imágenes a ~800px | Usado por limpiar_tandas/clustering/batch_selector |
| `ai_media/loop_engine.py` / `loop_db.py` | Motor de loop: arcos horarios, posicionamiento, spec JSON | CLI: `python scripts/ai_media/loop_db.py --horas ... --salida spec.json` |
| `check_db.py` / `check_gps.py` / `check_db_data.py` | Verificación de DB y GPS | TUI Consultar; standalone |

---

## Convenciones y patrones de código

### Estilo
- Español para nombres de variables, funciones, comentarios y commits.
- Docstrings en español.
- Type hints obligatorios en funciones nuevas.
- `log = logging.getLogger(__name__)` al inicio de cada script.

### Patrón de script independiente
Cada script en `scripts/` tiene:
1. `def main(argv=None)` con `argparse.ArgumentParser`.
2. Puede ejecutarse standalone (`python scripts/foo.py --args`) o desde flujos.py.
3. Si modifica la DB, acepta `--mode skip|update|replace` (default: skip).
4. Si es pesado, acepta `--dry-run` para previsualizar sin escribir.

### Patrón de acceso a DB
Usar `db/util.py` para conexiones:

```python
from db.util import abrir, resolver_db, conectar

# Opción 1: abrir/cerrar manual
conn = abrir("db/flujos.db")
# ... operaciones ...
conn.close()

# Opción 2: context manager (commit automático)
with conectar(resolver_db(args.db)) as conn:
    conn.execute("INSERT ...")
```

### Manejo de --mode en scripts
```python
if mode == "replace":
    # Limpiar datos existentes primero
    conn.execute("UPDATE media SET columna = NULL WHERE ...")
    conn.commit()
elif mode == "skip":
    query += " AND columna IS NULL"  # solo pendientes
# mode == "update": procesa todos, sobreescribe
```

El modo `replace` vía `_preguntar_modo()` (flujos.py) crea backup automático en `db/backups/` antes de limpiar.

### Errores comunes que evitar
1. **GPS**: ExifTool sin `-n` devuelve `"South"`/`"West"` y `Composite:GPSPosition` viene como `"31° S, 64° W"`. Usar `_es_sur_oeste()` y `_parse_gps_position()`.
2. **Timezone**: normalizar timestamps a aware UTC antes de operar (`_as_aware_utc()`: normaliza `Z`, `replace(tzinfo=utc)` si naive). Argentina = UTC-3.
3. **webcolors**: las variantes "grey" (inglés británico: `dimgrey`, `slategrey`, etc.) existen en CSS3 y hay que mapearlas explícitamente.
4. **CRLF en Windows**: Git muestra warnings de "LF will be replaced by CRLF". Es normal en Windows, no afecta la ejecución.
5. **Ollama timeout**: las llamadas a modelos de visión pueden tardar 30-60s por imagen. Usar timeout=180s (default real de `ollama_client.py`; el cliente de traducción legacy de `improve_db.py` usa 300s).
6. **Colores casi neutros (sat < 0.15)**: el matching CSS puede etiquetar grises/blancos/negros como colores (lavanda→violeta, gris puro→verde, near-white→azul) por match directo a colores pálidos o por el sesgo anti-gris. La puerta de neutralidad en `color_utils.get_color_names()` (`UMBRAL_SATURACION_NEUTRO = 0.15`) los clasifica por luminancia (`negro`/`blanco`/`gris`). Al regenerar colores (`improve_db --step colors --mode replace`), re-sync `deploy/db/visualizacion.db` (columnas `color_*`).

---

## Qué NO hacer (lecciones operativas)

1. **Signo GPS**: ExifTool sin `-n` devuelve `"South"`/`"West"`; las coordenadas argentinas son SIEMPRE negativas (lat < 0, lon < 0).
2. **moondream responde MAL en español** (regurgita basura tipo "irtiville") → usar prompts EN en clustering/selección (`batch_selector.py`, `clustering.py`).
3. **Traducción EN→ES = NO-AI por defecto** (`glosario.py` + motor clásico: Google `deep_translator` default, Argos offline opcional). NO usar Ollama/`qwen2.5:3b` (produce chino/checklist/slash). El legacy `translategemma` queda disponible solo con `--motor ollama`. El glosario es léxico del dominio + reemplazos rioplatenses post-traducción; nunca traducir por IA sin motivo.
4. **No usar `--workers 2+`** en improve_db: Ollama serializa la inferencia, compiten por memoria y desestabilizan el modelo (síntoma `@@@@@` y tags vacíos; medido 25x más lento).
5. **Transcripción**: usar VAD + `whisper_estado` como marcador de pendiente (no `whisper_segments`, un `sin_voz` no tiene segmentos). NO forzar idioma español (`language=None`).
6. **No usar capa semántica de embeddings en refinar_keywords**: produce falsos sinónimos (`ciclismo→deporte`, `nublado→soleado`). Eliminada por completo.
7. **No copiar el schema en AGENTS.md**: referenciar `db/schema.sql` (fuente de verdad versionada).
8. **minicpm regurgita prompts de descripción con "Give me..."** (eco: "Here's a long description...", "To describe the image, we first observe..."). Usar formulación directa: `"Start directly with the scene, without any preamble"` (A/B Ago 2026: 0/9 eco vs 1/9 con el viejo, sin pérdida de calidad). Mantener `limpiar_meta_intro()` como red de seguridad, nunca depender solo del prompt.

---

## Verificación (tests)

- `python scripts/test_gradiente.py` — tests unitarios de gradiente (10 puntos simulados).
- `python scripts/ai_media/test_motor_loop.py` — 42 asserts del motor de loop (segmentos, cruce nocturno, posición, descarte fuera de arco).
- `python -m pytest db/test_migrate.py -q` — migraciones de schema (v1→v4).
- Helper `scripts/ai_media/checkpoint.py` — verificado: 45 ítems → 3 commits parciales; Ctrl+C → SystemExit(130) con pendiente commiteado.

## Reglas de desarrollo (menú TUI)

- **Regla de agrupación**: siempre que se agregue una opción nueva al TUI, insertarla cerca de opciones temáticamente relacionadas, no al final de la lista.
- **Regla de paginación**: cada hoja soporta hasta **9 opciones** (1-9). Solo al superar 9 se crea una hoja nueva. Navegación: **n** = Siguiente, **p** = Anterior, **0** = Volver. Si la hoja temática está llena, las nuevas van a la hoja siguiente. En hojas que tienen tanto Anterior como Siguiente, **p se lista primero** y luego n.
- **Patrón de helpers**: los menús del TUI se implementan con `_menu(titulo, opciones, db_path, intro=, titulo_ancho=, ...)` (menú simple) o `_menu_paginado(titulo, hojas, db_path)` (hojas de hasta 9 opciones). Las opciones son `dict[clave → (etiqueta, callable)]`; la callable recibe `db_path`. La clave "0" está reservada para Volver/Salir. `_preguntar_sn(pregunta)` para confirmaciones s/N y `_args_sn(args, flags)` para flags booleanos. Para submenús que deben cerrarse tras ejecutar una opción, usar `_menu(..., cerrar_al_ejecutar=True)`. Para menús con cabecera propia (ej: `tui()`), usar `_menu(..., pre_titulo=callable, etiqueta_salir="Salir", on_salir=callable)`.
- El árbol TUI completo NO vive aquí: ver @README.md.

## Subagentes

| Agente | Archivo | Rol |
|--------|---------|-----|
| `@orquestador` | `.opencode/agents/orquestador.md` | **PRIMARY (DEFAULT)**. Orquestador principal. No implementa, delega. |
| `@touchdesigner` | `.opencode/agents/touchdesigner.md` | Experto TouchDesigner: operadores, Python/TD, OSC, MIDI, NDI, Spout, shaders, proyección. |
| `@gis` | `.opencode/agents/gis.md` | Experto GIS: geolocalización de medios, conversión de coordenadas, cálculos de distancia, ubicación relativa. |
| `@ia-media` | `.opencode/agents/ia-media.md` | Experto IA: Ollama visión, faster-whisper transcripción, análisis imágenes/video, selección inteligente. |

## Skills

| Skill | Cuándo usarlo |
|-------|---------------|
| `sqlite` | Crear/consultar BD, migraciones, insertar medios, queries complejas. |
| `ffmpeg` | Transcodificar, extraer metadata, analizar duración/resolución, thumbnails. |
| `exiftools` | Leer/escribir EXIF/IPTC/XMP en imágenes, videos y audios. |
| `ia-media` | Procesamiento con IA: transcripción (faster-whisper), análisis de imágenes (Ollama visión), selección inteligente. |
| `python-media` | Scripts ETL, automatización, pipeline de ingesta, procesamiento batch con Pillow/mutagen/etc. |

## Dónde está la información (documentación externa)

Todo lo que sigue NO está en este archivo para no inflar el contexto.
Se consulta bajo demanda según la necesidad:

| Si la tarea involucra... | Consulta este archivo | Propósito |
|---|---|---|
| Historial de cambios / versiones | `CHANGELOG.md` | Ver qué cambió y por qué |
| Guía de usuario, comandos, pipeline, menú TUI completo | `README.md` | Cómo se usa la herramienta |
| Args CLI detallados de un script | docstring del script (`python script.py --help`) | Flags, modos, ejemplos |
| Schema completo de la DB | `db/schema.sql` | Tablas, columnas, índices exactos |
| Migraciones de schema | `db/migrate.py` | Versiones v1→v4 |
| Diseño de la instalación | `docs/diseno_instalacion.md` | Flujo DB → elecciones → filtros → loop |
| Motor de loop | `docs/motor_loop.md` | Spec del cerebro Python (arcos horarios, chiches, spec JSON) |
| Deploy / visualización web | `docs/deploy.md` | Exportador genérico `exportar_visualizacion.py`, transcode web, snapshot local vs deploy |
| Retorno "Fluir" en TD | `docs/retorno_fluir_td.md` | Contrato OSC 9002, tablas `fluir_*`, armado del receptor en TD |
| GUI "Fluir" en Python | `docs/gui_fluir.md` | `gui_fluir.py`: selector de medios que reemplaza la UI de chips de TD; delega el envío por 9002 en `puente_td._procesar_rafaga` |
| Videos 360° en web | `docs/videos_360_web.md` | Opciones de renderer 360° (Three.js, A-Frame...), requisitos de pipeline |
| Rediseño de embeddings | `docs/embeddings_rediseno.md` | Dirección de diseño de la capa semántica (desactivada) |
| Relojes de cámaras Insta360 desincronizados (videos 360° del viaje) | `docs/discrepancia_horarios_camaras.md` | Identificación de cámara (A=LA +7h / B=UTC+1 −1h / B reconfigurada), cómo deducir la hora real (embebido=UTC−3h), procedimiento reutilizable para videos nuevos |
| Geocodificación / por qué localidad es NULL | `docs/geocodificacion_reversa.md` | Estrategias y alternativas |
| Documentos de diseño | `docs/` (arquitectura_motor, flujo_de_medios, linea_de_tiempo, semantica_color, calculo_astronomico, visualizaciones, limpieza_tandas_resultados, ideas_externas, lecciones_elecciones_td, inferencia_autor, armado_de_tandas, plan_keywords, gui_fluir, videos_360_web, discrepancia_horarios_camaras, embeddings_rediseno) | Entender el "por qué" del diseño |
| Calidad de keywords | `docs/plan_keywords.md` | Plan de keywords (5 exactas, refinar v2, sonido/video, nube unificada) |
| Concepto / roadmap / config OpenCode | `VISION.md`, `ROADMAP.md`, `opencode.json` | Visión de la instalación, prioridades, configuración |
| Scripts TouchDesigner | `td/osc_callbacks.dat`, `td/elecciones_ui.dat` | Callbacks OSC y UI de elecciones (ver operadores esperados abajo) |
| Riesgos operativos (archivos movidos externamente, timeouts de API, espacio en disco) | `README.md` | Mitigaciones |

**Estructura de nombres de operadores TD esperados (mapa REAL, verificado por export OP Find en `td/opfind1.tsv`):**
- `osc_in1` — OSC In DAT (puerto 9000); `osc_in1/osc_in1_callbacks` — DAT interno con `File` → `td/osc_callbacks.dat`, `Sync to File` = ON
- `osc_out1` — OSC Out DAT (**TD → Python**), destino `127.0.0.1:9001; creado UNA SOLA VEZ a mano (no lo genera ningún script); lo usan los `panelexec1` de los botones para `/flujos/seleccion/<grupo> <valor>` (grupo en la dirección + un solo valor; verificado con `osc_probe.py`)
- `elec_<id>` — Table DAT por grupo de elecciones (`elec_horas`, `elec_tags`, `elec_colores`, `elec_municipios`); las llena `osc_callbacks.dat` desde `/flujos/elecciones/<id>` (columnas `[titulo, valor, peso]`)
- `elec_<id>_container<N>` — Container COMP por grupo; dentro cada uno tiene `replicator1` (Replicator COMP) que clona `boton_<id>_N`
- `boton_<id>_0` — Button COMP "semilla"/template en la raíz de `/project2`; los `boton_<id>_N` (uno por opción del grupo) viven dentro del container del grupo
- Hijos fijos de cada botón: `par1` (Parameter CHOP → lee la fila de `elec_<id>`), `text` (Text COMP → etiqueta), `parexec1` (Parameter Execute DAT), `panelexec1` (Panel Execute DAT) → dispara `/flujos/seleccion/<grupo> <valor>` por `osc_out1`
- Tablas de datos del retorno "Fluir" (canal 9002; se crean con `td/crear_tablas_fluir.dat` y las llena `td/fluir_callbacks.dat` desde `/flujos/fluir/*`): `fluir_estado` (clave-valor), `fluir_fotos` / `fluir_videos` / `fluir_sonidos` (medios por tipo, columnas `[media_id, ruta, keypoint, hora, tipo]`), `fluir_textos` (textos; agrega `titulo` + `texto` = contenido real del texto como unidad de medio, vía `/flujos/fluir/texto`), **`fluir_videos_360`** (videos 360° separados del resto — marker `media.subtype='360'`, escrito por `improve_db --step video_metadata`), `fluir_chiches` (hora, texto), **`fluir_telegram`** (chat de Telegram de los municipios elegidos: `[id, from_name, texto, hora, fecha, tipo, fotos, municipio]`, vía `/flujos/fluir/mensaje`; solo si hay municipios, criterio web = rango de fechas de los medios, `es_sistema=0`, hora local UTC−3) y **`fluir_mapas`** (rutas de mapas por municipio: `[municipio, ruta]`, vía `/flujos/fluir/mapa`; solo si hay municipios, la ruta apunta al HTML generado por `scripts/mapas_municipio.py`)
- **NO existen** (eliminados o legacy): `movie1`, `tabla_colores`, `nube_container`, `nube_datos`, `color_actual`, `seleccion_actual`, `info_imagen`. Los handlers de `osc_callbacks.dat` para colores/slideshow apuntan a ops todavía no recreadas (pipeline visual en construcción); los modos legacy de `puente_td.py` que enviaban a esas ops (colores, nube, imágenes de un color, loop completo) fueron eliminados — el CLI solo soporta `elecciones`/`fluir`

## Riesgos conocidos

### 1. Suspensión de la computadora durante procesos largos

Si la PC entra en suspensión (S3 Sleep o Modern Standby S0) durante un proceso del pipeline:

| Componente | Riesgo |
|---|---|
| Ollama (localhost) | Bajo — la request puede colgar hasta el timeout (180s) |
| Open-Meteo / Georef API | Medio — socket muerto, puede colgar 60-120s hasta que Python detecta el error |
| faster-whisper / ExifTool / ffprobe (local) | Muy bajo — la computación se congela y reanuda limpio |
| SQLite WAL | Muy bajo — transacciones atómicas, checkpoint recupera al reanudar |
| ThreadPoolExecutor (`keywords`, `descriptions`) | **Resuelto** — checkpoint + patrón `wait(FIRST_COMPLETED)` con ventana de "sin progreso" + `pool.shutdown(cancel_futures=True)` evitan el pool congelado. ⚠️ NO usar `as_completed(timeout=X)`: su timeout es un presupuesto TOTAL del loop y cancela lotes legítimos largos a los X segundos (bug real: 1019 imgs → se cortaba a las ~19 por el default 300s). El timeout anti-cuelgue real es el de `ollama_client.py` (180s por request) |

**Qué se pierde**: solo tiempo de procesamiento; los datos commiteados sobreviven. `--mode skip` retoma pendientes.

### Otros riesgos operativos

Archivos movidos externamente (solución: `relocate.py` / `mover_media.py`), timeouts de APIs externas y espacio en disco (~50 GB de modelos) → detalle en @README.md.

### 2. Relojes desincronizados en cámaras Insta360 (videos 360°)

Los videos 360° del viaje provienen de **dos cámaras Insta360 con relojes mal sincronizados** (cámara A = hora Los Ángeles UTC−7; cámara B = UTC+1, y a veces reconfigurada). El `QuickTime:CreateDate` embebido es **UTC** y la hora real local = `CreateDate − 3 h`. Los `filename` `VID_YYYYMMDD_HHMMSS` **NO son confiables** (muestran el reloj de la cámara). Procedimiento de diagnóstico completo (identificación por bitrate/fps, tabla de offsets por fecha, validación GPX/luz) en `docs/discrepancia_horarios_camaras.md` — consultar antes de ingerir cualquier video 360°.
