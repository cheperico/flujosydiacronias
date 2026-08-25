# Roadmap — Flujos

## Pipeline

```
Etapa 1: PREPARAR MEDIOS     →  Limpieza de tandas, redimensionar, etc.
Etapa 2: INGESTA              →  Ingesta en DB + metadatos base + colores
Etapa 3: MEJORA DB            →  Etiquetado IA, transcripción, colores, timestamps, GPS
Etapa 4: ENRIQUECIMIENTO      →  Geocodificación, clima, día, gradientes
Etapa 5: INSTALACIÓN          →  TouchDesigner + motor de deriva
```

---

## Etapa 1: Preparar medios

| Item | Prioridad | Estado |
|---|---|---|
| Limpieza de tandas (seleccionar mejores imágenes por tanda) | Alta | ✅ `limpiar_tandas.py`, `mover_descartadas.py` |
| Redimensionar fotos | Media | ❌ Se hace con IrfanView |

---

## Etapa 2: Ingesta

| Item | Prioridad | Estado |
|---|---|---|
| Escaneo + fingerprint rápido | Alta | ✅ |
| Extracción EXIF (GPS, timestamps, cámara, autor) | Alta | ✅ |
| Extracción de colores dominantes | Alta | ✅ `color_utils.py` (Redmean, anti-gray bias, centrality) |
| Deduplicación por contenido | Alta | ✅ |
| Ingesta incremental (skip por file_hash) | Alta | ✅ |
| `ingest_batch_id` + `duration_secs` como columnas | Alta | ✅ |
| Guardar raíz de ingesta en config | Alta | ✅ |
| Undo-ingest por batch_id | Alta | ✅ |
| `end_time` para consultas por rango temporal | Alta | ✅ |
| GPS sign bug (lat/lon positivo en Argentina) | **Corregido** | ✅ Fixeado en ingest.py (`_es_sur_oeste()`, `_parse_gps_position()`) y verificado en los registros con GPS (signo negativo correcto) |
| Keywords IPTC en JSON (hoy string Python) | Media | ❌ |
| Content hash de video **eliminado** (frame a 0.5s era débil): quitada la opción CLI de hash de video, la función de hash de video y la notificación de duplicados por hash de contenido para video (para imágenes se mantiene con phash) | Baja | ✅ Hecho (Fase 0, 2026-08-11) |
| **Detección de repetidos (video/sonido)**: pipeline ligero de sospecha N1-N4 — N1 duración idéntica (±0.5s), N2 file_hash (tamaño+fecha, ya se calcula), N3 **banda sonora** (cross-correlación RMS, local — `repetir_contenido.py`), N4 **confirmación visual veloz** (aún pendiente). Mismo pipeline para sonidos. No marca automático: genera lista de candidatos para revisión humana | Alta | ✅ Parcial (Fase 1, 2026-08-11): `repetir_contenido.py` + `detectar_contenedores.py` + `audio_frame_crossref.py`; N4 visual pendiente |
| **Inferencia de hora de textos por posición en track GPX** (posición → tiempo; textos sin fecha obtienen su timestamp interpolando su punto contra el track, corte `--umbral`) | Media | ⚠️ Aplicado y REVERTIDO (2026-08-17): los textos de viajeros.md son crónicas históricas (1729–2024), no parte del viaje 2025 — no llevan timestamp. `inferir_hora_textos.py` queda como utilidad para textos que sí pertenezcan al viaje. Extensión multi-ubicación sigue pendiente |

---

## Etapa 3: Mejora DB

| Item | Prioridad | Estado | Ejecuta vía |
|---|---|---|---|
| Colores dominantes (reprocesar) | Alta | ✅ | `improve-db --steps colors --mode` |
| Etiquetado por keywords con IA (keywords libres) | Alta | ✅ | `improve-db --steps keywords --mode` |
| Descripción de imágenes con IA | Alta | ✅ | `improve-db --steps descriptions --mode` |
| Transcripción de audios/videos con timestamp | Alta | ✅ | `improve-db --steps transcribe --mode` |
| Keypoints (segmentos de transcripción) | Alta | ✅ | `improve-db --steps keypoints --mode` |
| **Inferencia de GPS** desde medios cercanos | Alta | ✅ | `improve-db --steps gps --mode` |
| **Inferencia de timestamps faltantes** | Alta | ✅ | `improve-db --steps timestamps --mode` |
| Pipeline unificado (10 pasos) con skip/update/replace | Alta | ✅ | `improve_db.py` + flujos.py TUI |
| Etiquetado combinado (keywords + descripción en 1 llamada de visión) | Media | ✅ | `improve-db --steps combinado --mode` |
| Pipeline IA EN → ES (visión genera EN, traducción ES con pipeline NO-AI glosario+motor clásico) | Alta | ✅ | `improve_db.py` + `traducir_metadata.py` |
| Refinar/unificar keywords (sinónimos) | Media | ✅ | `refinar_keywords.py` |
| Audio tagging (sonidos ambientales, sherpa-onnx local) | Media | ✅ | `audio_tagging.py` |
| Verificación de Ollama antes de pasos IA | Alta | ✅ | `_verificar_ollama()` en flujos.py |
| Threading en llamadas Ollama (2 workers) | Media | ✅ | `ThreadPoolExecutor` en improve_db.py |
| Inferencia de autor desde medios cercanos | Baja | ❌ | — |
| Detección/corrección de offset de reloj en cámaras | Media | ❌ | — |
| Merge de metadatos para contenido duplicado | Baja | ❌ | — |
| **Buscar medios repetidos** (TUI Mejorar DB): corre N1-N4 (ver Etapa 2 — detección de repetidos) y deja la lista de candidatos con señales y nivel de sospecha para que el humano confirme; exportable. Incluye 1e: audio largo = contenedor de audios cortos (huecos de silencio / cortes de tema en `whisper_segments`) | Alta | ❌ Plan 2026-08-09 | — |
| **Rediseño análisis de contenido de video**: pasar todo a minicpm-v4.6 (moondream descartado, responde mal en español), muestreo = scene detection → ~10 imágenes por escena → selección por nitidez (pipeline de tandas) → tags; límite de tags por escena y por duración de escena (máx 20/escena). Revisar moondream en `batch_selector`/`clustering`/`tag_images` | Media | ✅ Hecho (Fase 3, 2026-08-11) — `analyze_video.py` rediseñado | — |
| **Keypoints semánticos de video** (prioridad > embeddings): saber cuándo se dice/aparece X → fragmento [inicio, fin] para mostrar según keywords; por contenido transcripto (`whisper_segments`). Pendiente de mejora: keywords de sonidos **no hablados** aún no probadas (esa parte queda a futuro) | Media | ✅ Hecho (Fase 3, 2026-08-11) — `keypoints_video.py` (`escena`/`keyword`); sonidos no hablados sigue pendiente | — |
| **Keypoints de contexto (devenir geográfico de video/sonido)**: interpolar posición continua del medio sobre el track GPX en su intervalo `[timestamp_utc, end_time]` y marcar los cambios de contexto (municipio, provincia, clima, twilight, elevación) en `media_keypoints` con keys `contexto_*` (source `track_interpolado`). Pipeline en 3 fases: F1 interpolar 30-60s (local), F2 transiciones baratas sin API (elevación/twilight/velocidad), F3 enriquecer candidatos con APIs en frecuencia gruesa 5-10 min + cache por tramo (georef municipio/provincia, open-meteo clima por tramo horario). Permite fragmentar el medio por contexto y combinarlo con keypoints semánticos | Media | ✅ Hecho (Fase 4, 2026-08-11) — `keypoints_contexto.py` (F1-F4 + multi-track + sentinel) | — |
| **Embeddings desactivados** (modelo/prompt/fuentes/usos no útiles aún): quitar opción del TUI (Hoja 3 "1) Embeddings"), pasar a rediseño profundo antes de re-exponer | Baja | ✅ Retirado del TUI 2026-08-11 | — |

---

## Etapa 4: Enriquecimiento

| Item | Prioridad | Estado | Ejecuta vía |
|---|---|---|---|
| **Geocodificación inversa** (provincia, municipio, localidad) | Alta | ✅ | `geocode.py --mode` (Georef API Argentina) |
| **Clima histórico** (temperatura, humedad, lluvia, nubes) | Alta | ✅ | `fetch_weather.py --mode` (Open-Meteo ERA5-Land) |
| **Día de la semana** en español | Alta | ✅ | `dia_semana.py --mode` |
| **Gradientes de ruta** (distancia Haversine, elevación, pendiente) | Alta | ✅ | `gradiente.py --mode` (Python puro, sin numpy) |
| **Posición del sol / twilight** (NOAA) | Alta | ✅ | `astronomia.py --mode` (sin dependencias externas) |
| Keywords del sentido de transcripciones | Media | ✅ | `keywords_transcripciones.py` |
| Keywords de sonidos **no hablados** (extender audio tagging → sentido semántico; no probado aún) | Media | ❌ Pendiente | — |
| Embeddings vectoriales (búsqueda semántica) — **desactivados**, rediseño profundo pendiente; dirección de diseño en `docs/embeddings_rediseno.md` (2026-08-15) | Media | ✅ Desactivado | `generate_embeddings.py` (retirado del TUI) |

---

## Etapa 5: Instalación (TouchDesigner)

| Item | Prioridad | Estado |
|---|---|---|
| Motor de deriva (lógica de navegación no determinista) | Alta | ❌ Concepto en VISION.md |
| Línea de tiempo como eje ordenador | Alta | ❌ Diseño en docs/linea_de_tiempo.md |
| Salida a 5 pantallas (1 interacción + 4 360°) | Alta | ❌ |
| Detección de pico de ruido como input | Media | ❌ |
| Caché de consultas frecuentes / recorridos predefinidos | Baja | ❌ |
| **"Fluir" (multiselector OSC, decisión 2026-08-08/09)**: como en la web (`#btn-fluir`), el visitante **acumula** elecciones y al presionar el botón **"Fluir"** se envía **todas las selecciones juntas** (la ráfaga sobre 9001 ES el "Fluir", verificada con `osc_probe.py`; formato `/flujos/seleccion/<grupo> <valor>`). **Lado Python implementado (2026-08-09)**: recibe la ráfaga por 9001, la acumula por grupo, detecta fin con **debounce** y genera el loop; el retorno va por **canal separado 9002** (`osc_in2`/`osc_in2_callbacks`), NO toca `osc_in1`/9000. **Rediseño por tipo**: `loop_db.generar_loop` filtra por **rango de horas [min,max]** (duro), **municipios** (duro si vienen), **colores y tags como prioridad** (score, no descartan), ordena por hora asc + score desc, y devuelve tablas **por tipo** (`por_tipo`: image/video/audio/text) con `keypoint` (= `t_loop`) en cada medio; chiches limitados a **clima+astronomía**. `puente_td.py` envía por 9002 el contrato por tipo (`/flujos/fluir/resumen`, `/tabla`, `/medio <id> <ruta> <keypoint> <hora> <tipo>`, `/chiche`, `/fin`). Pendiente: armado TD de `osc_in2` + tablas `fluir_*` (checklist en `docs/retorno_fluir_td.md`) | Alta | 🚧 Lado Python ✅ — falta receptor en TD |
| **Refactor `elecciones_ui.dat` al patrón manual actual**: el toe se arma a mano (Replicator COMP + `elec_<id>_container<N>` + `boton_<id>_N`); opción futura: que el script genere lo mismo. Por ahora se sigue armando manualmente | Baja | ❌ Opción diferida |

---

## Visualización web (deploy)

| Item | Prioridad | Estado |
|---|---|---|
| **Reproducción de videos en la web** (hoy el bloque Videos es solo una lista de descripciones; sin `<video>` en el lienzo; el Range ya está soportado) | Media | ❌ Pendiente (regulares) / ✅ 360° |
| **Visualización de videos 360° equirectangulares**: **implementado 2026-08-25** — bloque "Videos 360°" + visor Three.js local (`js/three.min.js`), HTTP Range en `servir_medio.php`, param `subtipo` en `medios_filtrados.php`, transcode `--transcode --transcode-360-largo 1440` (con skip-if-exists). Detalle en `docs/videos_360_web.md` | Media | ✅ Implementado (falta transcode completo de los 44) |
| **Detección de videos 360 reales**: **44 videos marcados** `subtype='360'` + `xmp_spherical=True` (censo 2026-08-25) | Media | ✅ Resuelto |

---

## Gestión de DB

| Item | Prioridad | Estado |
|---|---|---|
| `flujos.py` entry point unificado + TUI | Alta | ✅ |
| `relocate.py` — cambiar raíz de medios | Alta | ✅ |
| `reset-db` — backup + borrar datos y reiniciar | Media | ✅ |
| `backup-db` / `restore-db` — copias de seguridad | Media | ✅ |
| `backfill-end-time` — poblar end_time en registros existentes | Alta | ✅ |
| `improve-db` — comando unificado de mejora (9 pasos, 3 modos) | Alta | ✅ |
| Todos los scripts con `--mode skip/update/replace` unificado | Alta | ✅ |
| `_preguntar_modo()` en TUI para todas las operaciones DB | Alta | ✅ |
| `_verificar_ollama()` antes de pasos IA en TUI | Alta | ✅ |
| **Mapa de datos centralizado** (qué escribe cada script y dónde) | Media | ✅ Documentado en AGENTS.md |
| Soporte para tracks GPS (GPX) | Baja | ✅ `ingest_gpx.py` + tablas `tracks`/`waypoints` |
| Desktop Telegram (chats, mensajes, multimedia) | Media | ✅ `import_telegram.py` |

---

## Documentación

| Item | Prioridad | Estado |
|---|---|---|
| `AGENTS.md` documentación exhaustiva (schema, mapa de datos, scripts, TUI, convenciones, pitfalls) | Alta | ✅ |
| `README.md` actualizado con todos los scripts, comandos, TUI, schema, enriquecimiento | Alta | ✅ |
| `ROADMAP.md` actualizado con todas las etapas | Alta | ✅ |
| `docs/geocodificacion_reversa.md` — estrategias de geocodificación | Media | ✅ |
| `docs/limpieza_tandas_resultados.md` — comparativa de estrategias | Media | ✅ |
| `docs/arquitectura_motor.md` — TD puro vs híbrido | Baja | ✅ |
| `docs/flujo_de_medios.md` — flujo en el motor | Baja | ✅ |
| `docs/linea_de_tiempo.md` — diseño de timeline | Baja | ✅ |

---

## Mejoras de robustez

| Item | Prioridad | Estado |
|------|-----------|--------|
| Timeout en ThreadPoolExecutor (keywords/descriptions) para evitar colgado por suspensión | Media | ❌ Pendiente |
| Verificar timeouts en ollama_client.py (requests.post) | Media | ❌ Pendiente |

---

## Historial

- **2026-08-17:** **Revert: textos históricos sin timestamp.**
  - La inferencia de timestamp aplicada el 2026-08-16 con `inferir_hora_textos.py`
    fue revertida la misma semana: los textos de `textos/viajeros.md` son crónicas
    históricas (Gillespie 1806-07, Gervasoni 1729, Concolorcorvo 1771, Mac Cann 1853,
    Sarmiento 1845, Tschiffely 1925, Di Fazio 2024, etc.) que NO pertenecen al
    viaje 2025 — interpolarlos contra el track GPX asignaba fechas falsas.
  - Se limpiaron `timestamp_original`/`timestamp_utc`/`geolocation_source` (12 registros).
    `inferir_hora_textos.py` queda disponible para textos que sí pertenezcan al viaje.
  - Los textos sin hora son seguros: elecciones horas los excluye
    (`timestamp_utc IS NOT NULL`) y el loop usa `HORA_DEFECTO_TEXTO=12.0`.
  - Pendiente de diseño: cómo tratar textos históricos en el eje temporal (pueden
    necesitar un plano de "era histórica" separado o narración multi-ubicación, no
    timestamps del viaje).

- **2026-08-17:** **Backfill de municipio rural + decisión E híbrida (departamento + municipio) pendiente.**
  - Contexto: 502 medios rurales tienen `departamento` pero `municipio` NULL (el polígono del
    municipio cubre solo el ejido urbano; el punto en ruta cae dentro del departamento pero
    fuera de todo municipio). Ver `docs/geocodificacion_reversa.md`.
  - Decisión inmediata: **backfill** asignando a cada medio sin municipio el **municipio más
    cercano DENTRO del mismo departamento** (fuente: API Georef `/api/municipios` con
    centroides, no los puntos de la DB — hay departamentos con 0 municipios de referencia).
  - Decisión futura: **E híbrida** — departamento como unidad de elección/filtro (cobertura
    100%) + municipio como detalle cuando exista. **PENDIENTE de estudio** (cambia la lógica
    de DB/elecciones/loop de forma considerable). Estar listos: el backfill debe marcar el
    origen del municipio (`georef_api` vs `inferido_vecino`) para poder revertir/re-calcular.
  - Pendiente de conversar: qué mostrar en la instalación para rurales, impacto en
    `elecciones.py`/`loop_db.py`/`puente_td.py`, y si el departamento reemplaza al municipio
    en las nubes de elecciones TD.

- **2026-08-17:** **Mapas por municipio (`scripts/mapas_municipio.py`).**
  - Nueva opción para generar diferentes mapas (extensible a otros tipos a futuro).
    Por ahora genera **un mapa HTML por municipio recorrido** (74 municipios) con 4
    variantes: `ruta` (puntos + línea), `puntos` (solo marcadores), `contexto`
    (puntos destacados sobre la ruta completa en gris) y `gradiente` (segmentos
    por pendiente + leyenda). Reutiliza helpers de `mapa_ruta.py` (Folium).
  - Nombre de archivo: `mapa_municipio_<municipio>_<variante>.html` (slug ASCII
    sin acentos: espacios→`_`, `'Río Hondo'`→`Rio_Hondo`, `mapa_municipio_Rio_Hondo_ruta.html`).
    Salida en `mapas/`.
  - CLI: `flujos.py mapa-municipios` (alias `mapas`); TUI: Visualizaciones→2.
    Args: `--variantes`, `--municipio` (substring), `--output`, `--db`, `--dry-run`.
  - Verificado: 74 municipios × 4 variantes = 296 archivos, 0 errores (~16 s).

- **2026-08-17:** **Ruta de los mapas desde el track GPX + reporte de discrepancias.**
  - Problema: `mapa_ruta.py` y `mapas_municipio.py` dibujaban la línea de ruta con los
    GPS embebidos de los medios (mayormente `inferido_tiempo`/`track_gps`, derivados del
    track). La fuente correcta de la ruta es el track GPX (`Al_FaB_Tucuman.gpx`, 3920 pts).
  - Cambio: la línea de ruta de `mapa_ruta.py` usa el **track completo**; las variantes
    `ruta`/`gradiente` de `mapas_municipio.py` usan el **tramo del track** por rango
    temporal de los medios del municipio; `contexto` usa el track completo. Los medios
    quedan como marcadores con su GPS propio. **Heatmap eliminado**.
  - Nuevo helper `scripts/track_gpx.py` (cargar_tracks, interpolar_posicion, tramo_temporal,
    haversine, medir_discrepancias/reportar_discrepancias). Envuelve la lógica ya existente
    en `ubicar_videos_gpx.py`/`keypoints_contexto.py`.
  - Reporte de discrepancias media vs track: flag `--tolerancia-metros` (default 1000) en
    ambos scripts; compara GPS embebido (`metadata`/`manual`) contra el track interpolado
    en el timestamp; solo reporta, no escribe DB. Con la DB actual: 10 discrepancias
    >1000 m (2.2–2.6 km en Colón, Bell Ville, Río Hondo).
  - Verificado: 296/296 mapas regenerados con tramos del track, 0 errores.
  - Modo de generación: `mapas_municipio.py --mode skip|update` (default `update`); `skip`
    genera solo los faltantes en disco (los existentes se saltan). En la TUI se pregunta
    `?Generar solo los que faltan?` con default Sí, y las variantes se confirman con
    default Sí (`S/n`).

- **2026-08-24:** **Gaps del track: no fabricar posiciones falsas + aviso en mapas.**
  - Problema real: el video `INSTA 5 ..._152.mp4` (id 1376, Colonia Caroya) mostraba en el
    mapa un punto que no coincidía con el video. Causa: el track GPX tiene un **gap de 9110 s
    (2.5 h)** (25-ago 00:40Z → 03:12Z) y el video arranca dentro de ese hueco. El script
    interpolaba linealmente a través del gap y fabricaba una posición falsa (offset 0 =
    `-31.405066,-64.212150`), que además quedaba etiquetada como "Fin" por ser el último
    medio del municipio.
  - Cambio en `ubicar_videos_gpx.py`: las muestras dentro de gaps > `--umbral-gap`
    (default 1800 s = 30 min, antes 600 s solo flaggeaba) **no se emiten**; en
    update/replace se limpia el GPS previo de track si el video queda sin cobertura.
  - Aviso en los mapas: `--umbral-gap-aviso` (default 1800) marca en naranja los medios
    con gap del track ≥ umbral y el popup avisa "posición incierta". Aplica a
    `mapa_ruta.py` y `mapas_municipio.py`.
  - Verificado: DB actualizada (1376 ahora con lat/lon real, 7 videos con GPS limpiado),
    296/296 mapas regenerados.

- **2026-08-16:** **Inferencia de hora de textos por interpolación en el track GPX.**
  - Los textos (`type='text'`) sin fecha solo obtienen su fecha/hora interpolando
    su posición (lat/lon) contra el track GPX (posición → tiempo): los 2 puntos
    del track más cercanos con ponderación por distancia inversa y corte
    `--umbral` (default 2000 m; más lejos se skipea). Script:
    `inferir_hora_textos.py` (marca `geolocation_source='track_interpolado'`).
  - Fix previo: `ingest_textos.py` guardaba la cadena literal `'None'`
    (`str(None)`) en campos vacíos de frontmatter/metadata de sección; limpieza
    one-time en la DB (23 timestamps + 49 filas `media_metadata`).
  - **Pendiente de diseño**: algunos textos narran una trayectoria MÁS ALLÁ de
    su punto (ej: "De Saladillo a Bell Ville"); hoy la hora se infiere en el
    punto único. A futuro los textos deberían soportar MÚLTIPLES ubicaciones
    (inicio/fin del segmento narrado) para que la interpolación (y la futura
    línea de tiempo/visualización) abarque toda la trayectoria narrada.

- **2026-08-15:** **Nota de dirección para el rediseño de embeddings.**
  - Motivación: pares de ideas distintas pero cercanas que el léxico no unifica
    ("identidad nacional" ~ "monumento") y textos de viajeros (crónicas históricas
    que hacen a la identidad nacional) que deberían agruparse con las fotos.
  - Dirección: embeddings = agrupación SEMÁNTICA entre tipos de medio (imagen ↔
    texto ↔ audio); fuentes = ia_keywords + ia_description + ia_keywords_texto +
    texto_completo + ia_keywords_transcripcion; NO duplicar SINONIMOS (falsos
    sinónimos ya descartados).
  - Documentado en `docs/embeddings_rediseno.md`; pendiente de decisión e implementación.

- **2026-08-15:** **Opciones de visualización 360° web documentadas.**
  - La web (deploy/) no reproduce videos (bloque Videos = lista de descripciones).
  - Opciones para videos 360° equirectangulares: A) Three.js esfera (recomendada,
    sin build), B) WebGL custom sin deps, C) A-Frame, D) librerías nicho
    (Panolens/cloudimage/Photo Sphere Viewer).
  - Requisitos de pipeline: flag `es_360` en snapshot (`exportar_visualizacion.py`),
    HTTP Range en `servir_medio.php`, transcode 360 `--transcode-360-largo 1920`,
    renderer 360 en `app.js` + fallback a lista.
  - Datos: 139 videos, 0 marcados `xmp_spherical` (detectar con ffprobe pendiente).
  - Documentado en `docs/videos_360_web.md`; pendiente de decisión e implementación.

- **2026-08-09:** **Plan: repetidos, embeddings y análisis de video.**
  - Detección de repetidos (video/sonido): pipeline de sospecha N1-N4 con banda sonora
    (chromaprint) como técnica central + confirmación visual veloz; misma técnica para
    sonidos; opción TUI "Buscar medios repetidos" que deja candidatos para revisión humana;
    limpieza de tandas en 2 niveles (rápido duración+peso → profundo huella acústica).
    el hash de contenido de video se elimina (frame a 0.5s, débil) con toda su herencia.
    `clustering.py` descartado para video.
  - Embeddings desactivados (rediseño profundo pendiente, fuera del TUI).
  - Análisis de video: minicpm-v4.6 en todo (moondream descartado), muestreo scene detection
    → ~10 imágenes/escena → selección por nitidez → tags con límite por escena/duración
    (máx 20/escena). Keypoints semánticos (por contenido transcripto) prioridad > embeddings;
    keywords de sonidos no hablados pendiente.
  - Fecha de decisión: prioridades — repetidos (Alta), keypoints semánticos (Media, sobre
    embeddings), análisis de video (Media), embeddings (Baja).

- **2026-08-11:** **Plan: keypoints de contexto (devenir geográfico de video/sonido).**
  - Concepto: un medio con duración tiene un intervalo real `[timestamp_utc, end_time]`; en vez
    de ubicación estática, interpolar su posición continua sobre el track GPX y detectar los
    instantes donde el contexto cambia (municipio, provincia, clima, twilight, elevación).
  - Pipeline F1-F4: interpolar 30-60s (local) → transiciones baratas sin API (elevación,
    twilight, velocidad) → enriquecer candidatos con APIs (georef municipio/provincia cada
    5-10 min con cache por tramo, open-meteo clima por tramo horario) → escribir keypoints
    `contexto_*` en `media_keypoints` (source `track_interpolado`).
  - Uso: fragmentar el medio por contexto y combinarlo con keypoints semánticos (qué parte
    mostrar según keywords + contexto del visitante).
  - Decisiones (defaults): fotos no aplica (puntuales); GPX sin `time` → estimación lineal
    con source `estimado`; medio fuera del track → solo contexto estático inicial.
  - Pendientes de confirmar: frecuencia gruesa de APIs (5-10 min) y prioridad track vs GPS
    propio del medio como fuente de devenir.

- **2026-07-13:** Pipeline completo documentado. Bug ExifTool fixeado.
  `flujos.py` creado. `relocate.py` creado. Tabla `config` agregada.
- **2026-07-13 (2da ronda):** `duration_secs` e `ingest_batch_id` en schema.
  `undo-ingest` implementado. README y ROADMAP actualizados.
- **2026-07-15:** `end_time` agregado a schema, ingest y flujos.py.
  `backfill-end-time` subcomando. Timestamps faltantes a prioridad alta.
- **2026-07-15 (2da ronda):** Tabla `media_keypoints` en schema.
  `scripts/improve_db.py` creado con 7 pasos y 3 modos.
- **2026-07-15 (3ra ronda):** Fixes: timestamp fallback, relocate sidecars,
  numpy serialization, check_db/check_gps refactor, --db en varios comandos.
- **2026-07-16:** **Mejoras mayores:**
  - `color_utils.py`: Redmean distance, anti-gray bias (1.5×), centrality boost,
    relative saturation, grey variants, olive→verde, fuchsia→violeta
  - `geocode.py`, `gradiente.py`, `fetch_weather.py`, `dia_semana.py`:
    todos con `--mode skip/update/replace` unificado
  - TUI: `_preguntar_modo()` en todas las operaciones DB
  - TUI: `_verificar_ollama()` verifica Ollama antes de pasos IA
  - TUI: nuevo submenú "Mantenimiento DB" (relocate, gradient, backfill, backup, restore, reset)
  - TUI: backup-db / restore-db implementados
  - TUI: resumen DB simplificado (6 líneas)
  - GPS sign bug fixeado en `ingest.py` (South/West text completo)
  - `AGENTS.md`: reescritura completa como documentación exhaustiva
  - `README.md`: actualización completa con todos los scripts, comandos, TUI, schema, enriquecimiento
  - `ROADMAP.md`: actualización con todas las etapas y nuevo historial
  - **Mapa de datos centralizado** agregado a AGENTS.md
