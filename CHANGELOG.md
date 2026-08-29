# Changelog

Todos los cambios significativos del proyecto Flujos.

Formato basado en [Keep a Changelog](https://keepachangelog.com/).
Las versiones corresponden a entregas funcionales, no a releases semánticas.

---

## [Entrega 51] — 2026-08-29 — Deploy en dos modos (local completo / hosting) + limpieza TUI

### Añadido
- **Deploy local completo sin copiar medios**: nuevo `deploy/includes/rutas.php`
  con `flujos_resolver_archivo($ruta_absoluta, $carpeta, $archivo)` — resolución
  compartida de rutas (absoluta local en `--snapshot-local`, `media/<carpeta>/<archivo>`
  en deploy), usada por `servir_medio.php` (servir binario) y `medios_filtrados.php`
  (disponibilidad 360°). Con esto el bloque **Videos 360° funciona también en el
  deploy local** (antes solo en hosting, porque el filtro solo miraba `deploy/media/`).
- **Dos modos de deploy documentados** (`docs/deploy.md` § Cómo regenerar):
  **LOCAL COMPLETO** (`--snapshot-local`: DB con rutas absolutas, consume los medios
  desde su ubicación local, no copia; solo sirve en la misma máquina) y **HOSTING**
  (modo deploy: copia a `deploy/media/` + DB con `media/...`; subir `deploy/` completo).

### Cambiado
- `deploy/api/servir_medio.php` y `deploy/api/medios_filtrados.php`: refactor a usar
  `flujos_resolver_archivo()` (un único criterio de resolución: "disponible" y
  "se sirve" siempre coinciden). `medios_filtrados` ahora incluye `ruta_absoluta`
  en el SELECT.

### Corregido
- **Etiquetas de videos en el deploy** (`scripts/exportar_visualizacion.py`): el
  SELECT de `media_metadata` y el `unir_keywords()` de `type='video'` ahora incluyen
  `ia_keywords_video` — los videos que solo tienen esa clave ya no quedan con
  `keywords NULL` en el snapshot (antes ~26% de videos sin etiquetas en la web).

### Eliminado
- **TUI: opción "Regenerar spec del loop (deploy/spec.json)"** (`flujos.py`, menú
  EXPORTAR VISUALIZACION WEB): obsoleta — `deploy/spec.json` y `api/loop.php` ya
  no existen (el prototipo `pruebas/` se eliminó en la limpieza del workspace) y el
  spec del loop para TD lo genera `puente_td.py` en runtime → `td/spec_fluir.json`.
  El menú queda: 1) Deploy a `deploy/` 2) Deploy a otra carpeta 3) Snapshot local
  4) Dry-run. `README.md` y `docs/deploy.md` actualizados.

---

## [Entrega 50] — 2026-08-28 — Deploy hub con keypoints + offset -5s en transcripción

### Añadido
- **Deploy hub con 3 visualizaciones** (`deploy/index.html` → hub, `deploy/panel/index.html` lienzo con `<base href="../">`, `deploy/keypoints/transcripciones/` + `contexto/`): hub estático con 3 cards, panel movido sin romper `api/`/`css/`/`vendor/`/`js/`, y 2 SPAs dinámicas de keypoints con `common.css`/`common.js` compartidos. Cada carga pide 50 al azar vía `api/keypoints.php?tipo=&limite=` (`ORDER BY RANDOM()`, sin filtros v1), player con `api/servir_medio.php` (Range), mapa Leaflet vendored (Esri Light Gray) y carrusel lazy de 10 fotos vía `api/fotos_cercanas.php?kp_id=` (Haversine + fallback temporal).
- **Snapshot `keypoints` en `visualizacion.db`** (`scripts/exportar_visualizacion.py:755`): nueva tabla `keypoints` (id, media_id, kp_key, value, offset_secs, timestamp_absolute, media_tipo/subtipo, archivo/carpeta, latitud/longitud/posicion_fuente, fecha/hora) con 3 índices. Posición materializada: `media.lat/lon` o interpolación GPX (`track_gpx.cargar_tracks`/`interpolar_posicion`, con `BASE` en `sys.path`). Verificado: 2630 filas (transcription 1367, contexto_* 887, ubicacion_video 376), 2630 con posición.
- **Endpoints `api/keypoints.php` y `fotos_cercanas.php`**: whitelist de `tipo`, clamp de `limite`, prepared statements, `RANDOM()` por carga y `EXISTS`/`julianday` para fotos cercanas (aprox equirectangular + Haversine exacto para `dist_m`).

### Cambiado
- **Offset -5 s en transcripciones** (`deploy/keypoints/common.js:114`, `scripts/generar_galeria_keypoints.py:384`): galerías de transcripción arrancan `Math.max(0, offset-5)` para dar contexto auditivo; `contexto` mantiene offset exacto.
- **TUI Visualizaciones** (`flujos.py:1898`): intro del menú ahora explica hub/panel/keypoints y que el snapshot incluye keypoints. `Exportar visualización` aclara hub+panel+keypoints y los 2 endpoints nuevos. `Galerías de keypoints` diferencia `pruebas/` local (`file://`) vs `deploy/keypoints/` portable.

### Documentación
- `docs/deploy.md` reescrito (§ Estructura, Cómo regenerar, Endpoints API con `keypoints`/`fotos_cercanas`, Hub+Panel+Keypoints).

## [Entrega 49] — 2026-08-28 — Revisión pre-presentación completa

### Añadido
- **Galerías de keypoints en TUI** (`scripts/generar_galeria_keypoints.py` + `flujos.py:opcion_galerias_keypoints`): nueva opción **Visualizaciones→4** con submenú `1 transcripción / 2 contexto` (50 al azar, player + mapa Leaflet online + slideshow 10 fotos con fundido 3s loop infinito). HTML en `pruebas/keypoints_*.html`.

### Corregido
- **Race en fluir vivo** (`scripts/td/puente_td.py:1328`): `selecciones`/`ultimo_mensaje` sin `Lock` entre thread `ThreadingOSCUDPServer` y loop principal → selecciones perdidas/duplicadas. Ahora `lock_selecciones` + snapshot bajo lock.
- **Nube tags GUI vs TD** (`gui_fluir.py:105`): solo `ia_keywords` → ahora 5 claves (`ia_keywords`, `ia_keywords_transcripcion/texto/sonido/video`) igual que `elecciones.py:108`.
- **Combinado skip** (`scripts/improve_db.py:123,722`): `NOT EXISTS IN (...)` dejaba a medio hacer → ahora `OR` con dos `NOT EXISTS` separados (check y query).
- **Carpeta en mover** (`scripts/mover_media.py:232,347`): `dirname` guardaba path completo → ahora `basename(dirname)` + `None` si en root + `SIDECAR_EXTS` case-insensitive.
- **Relocate prefijo** (`scripts/relocate.py:112`): `REPLACE` reemplazaba todas las ocurrencias → ahora `? || substr(..., length(?)+1)` solo prefijo.
- **Migración v4** (`db/migrate.py:184`): `ALTER TABLE media` fallaba en DB vacía (`no such table`) → guard `sqlite_master`.
- **Init DB orden** (`scripts/ingest.py:691`): `migrate_db` antes de `schema.sql` enmascaraba errores → ahora schema primero, luego migraciones.
- **Query WHERE** (`scripts/query.py:91`): `distinct_column` sin validar `--where` → ahora `_where_seguro` también en `--distinct`.
- **Clustering embeddings** (`scripts/ai_media/clustering.py:170`): `resp["embedding"]` fallaba con `ollama>=0.3` (objeto) → ahora maneja dict y objeto + guard `numpy`/`ollama`.
- **Imports sin guard** (`ollama_client.py`, `transcribe.py`, `puente_td.py`): crash si falta dependencia → ahora `try/except ImportError` + mensaje `pip install`.

### Cambiado
- **Ingest fingerprint doc** (`scripts/ingest.py:75`): aclara que `fast_fingerprint` no es SHA y remite a `--full-hash`.
- **Gradiente skip doc** (`scripts/gradiente.py:108`): documentado como intencional (determinista).
- **Check DB** (`scripts/check_db.py`/`check_db_data.py`): usan `resolver_db` + `--db` y lista completa de tablas.
- **Check GPS** (`scripts/check_gps.py`): expone `--folder` para `check_gps_folder` + `resolver_db`.
- **Ingest GPX CLI** (`flujos.py`): nuevo `ingest-gpx` / `gpx` (antes solo TUI Ingesta→2).
- **Tiles doc** (`scripts/tiles_offline.py:291`): CartoDB → Esri.

### Verificado
- `db/test_migrate` 8/8, `test_gradiente` 32/32, `test_motor_loop` 47/47. `docs/revision_pre_presentacion.md` v2 con foto Fase 0 (1391 medios) y todo el plan Fase 0-4 completado.

## [Entrega 48] — 2026-08-26

### Cambiado
- **Filtro duro por tags en el loop** (`scripts/ai_media/loop_db.py`): las tags elegidas en TD ahora **filtran** (antes solo eran prioridad de `score`, por eso al elegir tags seguían llegando TODAS las imágenes del arco).
  - `_filtrar_media`: nueva condición `EXISTS` sobre `media_metadata` con **OR de `LIKE '%tag%'`** sobre las 5 fuentes de keywords (`CLAVES_TAGS_LOOP`: `ia_keywords`, `ia_keywords_transcripcion`, `ia_keywords_texto`, `ia_keywords_sonido`, `ia_keywords_video` — el mismo universo que arma la nube `elec_tags` en `scripts/td/elecciones.py`). Un medio pasa si contiene **alguna** de las tags elegidas.
  - `generar_loop`: **fallback a prioridad** — si el arco queda con menos de `MIN_MEDIOS_FALLBACK_TAGS` (default 1) medios con las tags, se re-genera **sin** filtro de tags (todo el filtro base + `score`) para que la instalación nunca se quede sin contenido; el resumen anota `FALLBACK a prioridad` en `notas`.
  - Los **colores** siguen siendo prioridad (no filtran), como antes.
- **Diagnóstico previo**: mi consulta "semáforo tiene 0 ocurrencias" fue un falso negativo (busqué `%semafor%` sin acento; el valor real es `semáforo` con tilde y `LIKE` de SQLite distingue á/a). `semáforo` SÍ está en `elec_tags` (9 ocurrencias) y filtra correctamente (7 medios).

### Pendiente
- Reemplazar el fallback de tags por un **aviso real de insuficiencia** ("no hay suficientes medios seleccionados") cuando el arco quede sin medios con las tags elegidas.

### Documentación
- `docs/motor_loop.md` (§4): fila de Tags = filtro duro OR sobre 5 fuentes + fallback + pendiente.
- `docs/retorno_fluir_td.md` (§9): decisión nº 15.

---

## [Entrega 47] — 2026-08-26

### Corregido
- **Auto-scroll de los textos en TD** (`scripts/td/puente_td.py` → `_PLANTILLA_TEXTOS_HTML`, HTML `td/textos_fluir.html`): el scroll se repetía "varias veces en la duración del texto" (baja → sube → repite) porque `tick()` usaba un bucle `% slotMs` infinito y anclaba el reloj a `idx * ROTACION_SEG` (índice del texto en la lista), que **no** es el slot temporal real — en preview `idx` era enorme (epoch) y el texto nunca se movía; en loop avanzado aparecía directo al final.
  - **Comportamiento nuevo**: un texto que no entra en pantalla se lee **una sola vez** con un único descenso por slot de 30 s: **5 s** de pausa arriba (se ve el comienzo) → **23 s** de descenso continuo (easeInOutSine) → **2 s** de pausa al final → rota al siguiente texto. **Sin vuelta al comienzo** (es un texto que se lee, no un loop visual).
  - **Fix técnico**: el reloj del descenso se ancla al **offset dentro del slot actual** (`relojSlot = t0 si ?t0, o la carga en preview`; `t = (Date.now() - relojSlot) % SLOT_MS`), el mismo reloj que usa `indiceActual()`; al completar el slot, `cancelarAnim()` (sin bucle infinito). `iniciarScroll(tarjeta)` ya no recibe `idx`.
  - Textos que caben: centrados, sin scroll (sin cambios).

---

## [Entrega 46] — 2026-08-26

### Cambiado
- **Servidor de tiles: CartoDB → Esri World Light Gray** (Carto dejó de servir tiles públicos sin API key el 26-ago-2026, overlay "API KEY REQUIRED"; ver `home-assistant/frontend#53800`).
  - `scripts/mapa_ruta.py`: nuevo `TILE_ESRI_URL` (`https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}` — **orden `{z}/{y}/{x}`**, distinto de Carto, sin `{s}` de subdominios) y `ATTR_ESRI` como atribución por defecto. `TILE_CARTO`/`ATTR_CARTO` quedan **deprecados** en comentario (requieren API key).
  - `scripts/tiles_offline.py`: `TILE_URL_CARTO` → `TILE_URL_ESRI`; eliminado `SUBDOMINIOS` (Esri no usa subdominios); la descarga de tiles y el JS de la capa embebida (`js_capa_base_embebida`) usan la URL Esri sin `subdomains`. **Cache versionada por proveedor**: `tiles_cache/` → `tiles_cache/esri/` (evita mezclar estilos Carto/Esri).
  - `scripts/mapas_municipio.py`: importa `ATTR_ESRI` (y `TILE_DEFAULT` que ahora apunta a Esri); docstrings actualizados.
  - `deploy/js/app.js`: el mapa Leaflet del deploy web (`crearMapaLeaflet`) también usaba CartoDB — ahora usa la URL Esri (sin `subdomains`).
- Verificado: tile Esri descarga `image/jpeg` 200 sin key; mapa de ruta y 296/296 mapas de municipio regenerados con Esri (**0** con `cartocdn`, **0** con overlay "API KEY REQUIRED", **0** sin tiles); segunda corrida usa `tiles_cache/esri/` sin re-descargar.

### Documentación
- **AGENTS.md**: filas de `mapa_ruta.py`, `mapas_municipio.py` y `tiles_offline.py` (Esri Light Gray en vez de CartoDB; cache `tiles_cache/esri`).

---

## [Entrega 45] — 2026-08-26

### Cambiado
- **Rediseño del render de textos en TouchDesigner** (`scripts/td/puente_td.py`, plantilla `_PLANTILLA_TEXTOS_HTML` → `td/textos_fluir.html`):
  - **Look markdown moderno**: fondo `#000`, texto blanco, sans-serif system UI, columna de lectura `65ch` centrada, sin decoraciones (reemplaza la versión editorial serif con regla, capitular y acento ámbar).
  - **Tipografía un 50% más grande**: `--escala: clamp(21px, min(100vw,100vh)×0.042, 69px)` (antes `14px/0.028/46px`); todo en unidades relativas → sigue adaptándose a cualquier resolución/aspecto del Web Render TOP.
  - **Rotación fija de 30 s por texto** en ambos modos (preview y loop): antes el loop repartía `loop_secs/N` entre los textos (60 s con ~5 textos); ahora cicla cada 30 s por orden de ruta, alineado al arranque del loop vía `t0`.
  - **Aire vertical de `18vh` arriba/abajo** dentro de la tarjeta: separa el texto de los bordes y forma parte del viaje del scroll.
  - **Autoscroll = un único ciclo baja-subir por texto**, ocupando todo su slot (2 s arriba → baja con easing easeInOutSine → 2 s abajo → sube): antes corría a velocidad fija ~25 px/s dando varias vueltas dentro del mismo texto. Si el texto entra completo queda centrado sin scroll.
  - **`.tarjeta` conserva `flex-shrink:0`** (crítico: sin él flex comprime la tarjeta al alto del contenedor y la medición del autoscroll nunca detecta overflow — causa real del fallo de un intento previo de rediseño).
- **`escHtml()` robustecido** en la plantilla de textos: las entidades se construyen con `String.fromCharCode(38)` + `'amp;'/'lt;'/'gt;'` — la plantilla ya no depende de caracteres `&` literales que cualquier procesamiento pueda alterar (bug real detectado: reemplazos sin escapar en una iteración previa).

### Documentación
- **AGENTS.md**: nuevo riesgo conocido #3 — doble caché al cambiar plantillas HTML del puente (proceso Python importa módulos una sola vez + CEF del Web Render TOP cachea aunque cambie `par.url`; el `?t0=` no es confiable sobre `file://`). Actualizadas las descripciones de `web_render_textos` (catálogo de scripts y mapa de operadores TD).
- **`docs/retorno_fluir_td.md`**: bloque "Textos HTML" reescrito (rotación fija 30 s, aire 18vh, ciclo único baja-subir, look markdown); advertencia operativa de doble caché agregada al checklist de armado; aclarado que la serif Georgia es tema propio del chat.

---

## [Entrega 44] — 2026-08-25

### Añadido
- **HTML de mapas 100% autocontenido (cero red)** (`scripts/tiles_offline.py` + `scripts/mapas_municipio.py` + `scripts/mapa_ruta.py`): aunque los tiles de la vista inicial ya viajaban incrustados, cada mapa seguía cargando en runtime **10 assets JS/CSS de Folium desde CDN** (Leaflet, jQuery, Bootstrap, FontAwesome, awesome-markers) en `<head>` — bloqueantes para inicializar el mapa. El Web Render TOP de TouchDesigner usa **CEF con un proceso de navegador separado por TOP y una cache temporal que se borra al salir** → cada mapa re-descargaba esos CDN desde cero (con N TOPs = N×10 conexiones), y la página quedaba en blanco hasta resolverlos. Ahora los HTML se guardan autocontenidos:
  - `tiles_offline.py`: `descargar_assets` (cache en `assets_cache/`, regenerable) baja los JS/CSS que Folium referencia por CDN (leídos de `folium.folium._default_js/_default_css` para ir en sync) + las fuentes de íconos (FontAwesome solid `fa-solid-900.woff2`, glyphicons `.woff`); `inline_fuentes` reescribe los `url(../webfonts/…)`/`url('../fonts/…')` rotos a **data URIs**; `guardar_autocontenido` reemplaza los tags `<script src>`/`<link href>` de CDN por su contenido inline (render normal, **no** `embedded=True` — folium propaga ese kwarg a todos los hijos y `Marker.render()` no lo acepta).
  - **Sin servidor ni cambios en TD**: los HTML son autocontenidos y se abren al instante en `file://`, independientes de internet/CDN.
  - `mapas_municipio.py` y `mapa_ruta.py` guardan con `guardar_autocontenido` (flag `--assets-cache`; fallback CDN si la descarga de assets falla al generar).
  - **Migración**: regenerados los 297 mapas (296 municipio + 1 ruta), 0 errores. Verificado: **0** archivos con refs CDN, fuentes inline, total 545 MB (mediana 1.6 MB).
- **Chat de Telegram renderizado en HTML para TouchDesigner (Web Render)** (`scripts/td/puente_td.py` + `td/fluir_callbacks.dat`): además del OSC (`/mensaje` → `fluir_telegram`, intacto), en cada ráfaga con municipios el puente genera `td/chat_fluir.html` — HTML **100% autocontenido** (datos embebidos, fotos como data URIs, cero red; mismo principio que los mapas) que renderiza el chat y lo **revela sincronizado con la hora del loop**. En `/fin` el callbacks apunta el Web Render TOP `web_render_chat` a `td/chat_fluir.html?t0=<epoch_ms>&loop_secs=<N>`: la página arma su propio reloj (`t = (Date.now()-t0)/1000 % loop_secs` → hora 0..24) y muestra los mensajes conforme el loop alcanza su `hora` local (UTC−3). Los mensajes arrancan `display:none` y se revelan con fadeIn cuando el loop alcanza su hora → el feed crece y el contenedor **baja automáticamente** (auto-scroll de chat en vivo); sin `t0` (vista previa) simula el tiempo (~45 s) para ver el auto-scroll también en browser. **Nuevo diseño estilo Telegram Web**: burbujas con fondo gris claro, avatar con iniciales del remitente, fotos dentro de la burbuja (grid), separadores tipo Telegram, auto-scroll suave. **Scroll corregido**: se quitó `scroll-behavior:smooth` (el re-set de `scrollTop` cada 250ms deslizaba el thread entero en segundos); ahora es snap instantáneo que baja solo cuando se revela un mensaje nuevo (`huboNuevo`) → el feed avanza al ritmo del loop sin glides rápidos. **Rediseño v2 (tema oscuro editorial)**: fondo negro + Georgia serif coherente con `textos_fluir.html`; burbujas con **zig-zag por turno** (alternan de lado cuando cambia el escribiente) y nombre/acento lateral en color determinista por persona (paleta de 8 tonos estilo Telegram); respiro visual en cada cambio de emisor; el chat **se reinicia** cuando el loop da la vuelta. Flags `--generar-chat-html`/`--no-generar-chat-html`. `gui_fluir.py` no cambia (delega en `_procesar_rafaga`).
- **Textos renderizados en HTML para TouchDesigner (Web Render)** (`scripts/td/puente_td.py` + `td/fluir_callbacks.dat`): mismo patrón que el chat — `puente_td.py --generar-textos-html` (default) genera `td/textos_fluir.html` (HTML **100% autocontenido**, `titulo` + `texto_completo` por texto, ordenados por ruta vía `keypoint`) y en `/fin` el callbacks apunta el Web Render TOP `web_render_textos` a `td/textos_fluir.html?t0=<epoch_ms>&loop_secs=<N>`. La página muestra **UN texto a la vez**, rotando en orden de ruta a medida que avanza el loop (slots de igual duración); sin `?t0` (vista previa) rota cada 6 s. Canal OSC `/texto` → `fluir_textos` intacto como respaldo/estado. Flag `--generar-textos-html`/`--no-generar-textos-html`.

### Documentación
- **AGENTS.md**: filas de `mapas_municipio.py`, `mapa_ruta.py` y `tiles_offline.py` (autocontenido + `--assets-cache`).
- **README.md**: filas y árbol de los scripts de mapas.
- **`docs/retorno_fluir_td.md`**: nota del bloque `/mapa` (HTML autocontenido, cero red).
- **`.gitignore`**: `assets_cache/`.

---

## [Entrega 43] — 2026-08-25

Revisión completa del proyecto contra su documentación y actualización de la misma.

### Corregido
- **Ruta rota del spec del loop** (`flujos.py`): la opción TUI "Regenerar spec del loop" escribía a `pruebas/spec.json`, pero la carpeta `pruebas/` fue eliminada en la limpieza del workspace (91b0060). Ahora escribe a `deploy/spec.json`. Sincronizadas README.md y `docs/deploy.md`.

### Documentación
- **AGENTS.md**: 
  - Catálogo: agregados scripts que faltaban (`consolidar_medios.py`, `fix_gps_sign.py`, `mover_descartadas.py`); corregido `transcribe_media` (el paso `--step transcribe` usa `transcribe.py`); documentados flags reales de `puente_td.py`, `mapa_ruta.py`, `mapas_municipio.py`, `limpiar_descripciones.py`, `repetir_contenido.py`, `query.py`.
  - Mapa de datos: claves reales de `video_metadata` (`xml_*`, `xmp_*`, `video_spherical_projection`, `sony_device_*`); lista de claves de `media_metadata`/`media_keypoints` ampliada (`ubicacion_video*`, `texto_*`, `ubicacion_video`).
  - Regla `--mode`: aclaradas excepciones (`inferir_hora_textos.py` y `mapas_municipio.py` solo skip|update; `consolidar_medios.py`/`mover_media.py` usan mover|copiar).
  - Timeout de Ollama corregido (120s → 180s; legacy 300s); asserts de `test_motor_loop` (47 → 42).
  - Tabla de documentos: agregado `docs/plan_keywords.md` y ampliada la lista de diseño.
- **README.md**: agregados subcomandos faltantes (`astronomia`, `mapa-municipios`/`mapas`) y aliases (`undo`, `backfill`, `csv`); corregida navegación de Mantenimiento Hoja 2 (faltaba "n. Siguiente"); tabla de documentos con `plan_keywords.md`.
- **docs/deploy.md**: removidas referencias a `pruebas/` (eliminada); agregado endpoint `textos.php` (7 endpoints, no 6).
- **docs/visualizaciones.md**: visor 360 corregido de "fullscreen" a "embebido en el bloque".
- **docs/videos_360_web.md**: tabla de "estado actual" marcada como snapshot histórico pre-implementación.
- **ROADMAP.md**: ítems de timeout ThreadPoolExecutor y ollama_client marcados como Resueltos (ya implementados).

---

## [Entrega 42] — 2026-08-25

### Añadido
- **Bloque "Videos 360°" en el lienzo web** (`deploy/`): **reproduce el 360 en el
  propio bloque** (visor embebido, no fullscreen) con **Three.js local**
  (`deploy/js/three.min.js`, UMD 0.147, vendored). Esfera `SphereGeometry` +
  `VideoTexture` (`BackSide`), cámara en el centro; drag para mirar, rueda para
  zoom (fov 30–110), auto-rotación en reposo, barra ◀ ▶ para cambiar de video
  entre los filtrados por los chips. Al re-renderear el bloque se detiene y
  libera la escena. `medios_filtrados.php` solo devuelve los 360 cuyo archivo
  existe en `deploy/media/` (sin links rotos).
- **HTTP Range en `servir_medio.php`** (`206`/`416`, `Accept-Ranges`): streaming y seek para `<video>` (imprescindible para el visor 360).
- **Filtros conectados a todos los medios**: `medios_filtrados.php` acepta `horas` (franja `[min,max]` en hora local Argentina UTC−3, convirtiendo el `hora` UTC del snapshot) y `subtipo` (csv). En `app.js` el fetch incluye `video`, los toggles recargan los medios **en vivo** (`cargarMediosFiltrados` en color/hora/provincia/municipio/tag) y el bloque Videos se re-renderiza.
- **Fix hosting de rutas**: `servir_medio.php` resuelve como fallback `media/<carpeta>/<archivo>` (la DB snapshot-local guarda rutas absolutas de Windows que no existen en hosting → 404 de imágenes y audios; el fallback los sirve desde `deploy/media/`).
- **Mapa Leaflet en el lienzo**: `deploy/vendor/leaflet/` (vendored) + `recorrido.php` devuelve `latitud, longitud, keywords` (bloque Mapa).

### Cambiado
- **Exportador** (`scripts/exportar_visualizacion.py`): **skip-if-exists** (export incremental, no re-transcodifica archivos presentes), default `--transcode-360-largo` **1920→1440** (360 en 1440×720), tope de bitrate por píxeles (`-maxrate`/`-bufsize`) y keyframes para seek (`-g 60 -sc_threshold 0`). Censo: **44 videos 360** (3840×1920, `subtype='360'` + `xmp_spherical=True`).

### Documentación
- **`docs/deploy.md`**: endpoints (`horas`, `subtipo`, Range), sección "Videos 360°", nota de filtro por horas, gaps actualizados (regulares siguen como lista; contenido 360 requiere `--transcode`), fix del flag `--transcode` mal documentado.
- **`docs/videos_360_web.md`**: de "pendiente" a implementado (opción Three.js local, bloque, visor, transcode).
- **`docs/visualizaciones.md`**: sección "Filtros conectados a todos los medios" + bloque "Videos 360°".
- **`ROADMAP.md`**: 360° y detección de videos 360 → implementados.

### Pendiente
- Transcode completo de los 44 360 a `deploy/media/` (`python scripts/exportar_visualizacion.py --transcode`, ~varias horas); hoy solo un subset de demo es reproducible.
- Videos regulares: reproducción inline pendiente (el Range ya está soportado).
- Fase "compleja" de filtros: cruce de medianoche en horas y qué hacer cuando no hay medios.

---

## [Entrega 41] — 2026-08-25

### Añadido
- **Tiles de la vista inicial incrustados en los mapas por municipio** (`scripts/tiles_offline.py` + `scripts/mapas_municipio.py`): al abrir varios mapas a la vez en TouchDesigner (Web Render TOP sobre `file://`), cada uno descargaba sus tiles de CartoDB de internet en runtime → cuello de botella de red. Ahora los tiles de la **vista inicial** de cada municipio viajan incrustados como **data URIs base64** en el propio HTML: el mapa se muestra al instante sin red, y el zoom/pan posterior sigue cargando de internet.
  - `tiles_offline.py`: `tiles_en_bounds` (Web Mercator), `zoom_fit_bounds` (replica el zoom de `fitBounds`, expande el rango si cae fuera del default 11-13), `descargar_tiles_png` (cache en `tiles_cache/`, compartido entre municipios; standalone para precargar), `data_uris`, `js_capa_base_embebida` (JS de **capa única** con polling de la variable del mapa — Folium renderiza `var map = L.map` siempre al final del script — que resuelve data URI→embedded / CartoDB online).
  - `mapas_municipio.py`: el mapa se crea con `tiles=None` y la capa única se inyecta al guardar → **no se descarga dos veces** la misma zona (sin la capa base de Folium duplicando la descarga). Flags `--no-embebido` (deshabilita la incrustación; los mapas quedan 100% online) y `--zooms` (default 11,12,13).
  - **Sin servidor ni procesos extra**: los data URIs son parte del HTML y funcionan en `file://` (Chromium de TD), sin CORS. No cambia el deploy ni `puente_td.py`.
  - **Migración**: regenerados los 296 mapas (74 municipios × 4 variantes, 0 errores). Resultado: 296/296 con capa única + data URIs, **0** con capa base duplicada; total 171 MB, mediana 363 KB por mapa; 3699 tiles únicos en cache (19 MB).

### Documentación
- **AGENTS.md**: `tiles_offline.py` en estructura y catálogo; fila de `mapas_municipio.py` con la incrustación.
- **README.md**: fila de `scripts/tiles_offline.py` + fila y árbol de `mapas_municipio.py` actualizados.
- **`docs/retorno_fluir_td.md`**: nota en el bloque `/mapa` (HTML con vista inicial incrustada, `--no-embebido` para regenerar sin ella).

---

## [Entrega 40] — 2026-08-24

### Cambiado
- **Gaps del track: no fabricar posiciones falsas** (`scripts/ubicar_videos_gpx.py`): las muestras dentro de un hueco del track mayor que `--umbral-gap` (default **1800 s = 30 min**, antes 600 s que solo flaggeaba) ahora **NO se emiten** — la interpolación lineal a través del hueco producía una posición falsa (recta entre dos puntos del track separados por el gap).
  - El video `INSTA 5 ..._152.mp4` (id 1376, Colonia Caroya, 25-ago) tenía un gap de **9110 s (2.5 h)** en su inicio: el offset 0 emitido era una posición falsa. Ahora sus keypoints empiezan en el primer punto con track real (offset 1230 s) y `media.lat/lon` pasó de la falsa `(-31.405066,-64.212150)` a la real `(-31.406933,-64.205973)`.
  - **Limpieza de cobertura**: si un video queda sin ninguna posición válida (todo en gaps), en `--mode update/replace` se limpia su `media.latitude/longitude` previo de track (evita puntos ficticios). Resultado tras el update: 7 videos con GPS limpiado, 33 con ubicación, 361 keypoints.
- **Aviso de gap en los mapas** (`scripts/mapa_ruta.py` + `scripts/mapas_municipio.py`): los medios con `ubicacion_video_gaps` cuyo máximo gap ≥ `--umbral-gap-aviso` (default 1800 s) se marcan en **naranja** y su popup muestra "⚠️ Posición incierta: el track GPX tiene un hueco de ~X h cerca del inicio del video". Nuevo flag `--umbral-gap-aviso` en ambos scripts.

### Documentación
- **AGENTS.md**: fila de `ubicar_videos_gpx.py` (regla de gaps, limpieza de GPS) y de los scripts de mapas (`--umbral-gap-aviso`).
- **`docs/discrepancia_horarios_camaras.md`**: nota del caso del video `_152` (gap del track de 2.5 h en su inicio, posición interpolada falsa).

---

## [Entrega 39] — 2026-08-24

### Cambiado
- **Nombres de archivo de mapas por municipio sin acentos** (`scripts/mapas_municipio.py` + `scripts/td/puente_td.py`): los tildes/diéresis y la `ñ` complicaban la visualización de los HTML en TouchDesigner. El nombre ahora usa un **slug ASCII**: `mapa_municipio_<municipio>_<variante>.html` donde el municipio se normaliza a NFD (elimina tildes, diéresis y virgulilla de la ñ: `'Río Hondo'`→`Rio_Hondo`, `'Jesús María'`→`Jesus_Maria`, `'Melincué'`→`Melincue`), espacios→`_` y se descartan símbolos no alfanuméricos, conservando las mayúsculas. Los nombres sin acentos no cambian (`'Bell Ville'`→`Bell_Ville`).
  - `mapas_municipio.py`: nuevo `_slug_municipio()` (la lógica vivía en `_nombre_archivo`), sin dependencias.
  - `puente_td.py`: `_slug_municipio()` (línea 354) replicó la misma normalización para que la ruta que TD recibe por `/flujos/fluir/mapa` coincida **exactamente** con el archivo generado (requisito ya documentado en Entrega 37).
  - **Migración**: regenerados los 296 mapas (74 municipios × 4 variantes) y eliminados los 104 HTML viejos con acentos de `mapas/`. Verificado: 0 nombres con caracteres no-ASCII, 0 divergencias entre los slugs del generador y del puente.

### Documentación
- **AGENTS.md**: fila de `mapas_municipio.py` con la nueva convención de slug (sin acentos, ejemplo `'Río Hondo'`→`Rio_Hondo`).
- **README.md**: fila de `scripts/mapas_municipio.py` con la convención ASCII.
- **ROADMAP.md**: entrada del historial actualizada al slug sin acentos.
- **`docs/retorno_fluir_td.md`**: §contrato (bloque `/mapa`) y decisión nº 14 con el slug ASCII.
- **`flujos.py`**: ayuda del CLI y menú TUI aclaran "sin acentos".

---

## [Entrega 38] — 2026-08-17

### Añadido
- **Modo de generación en `mapas_municipio.py`**: nuevo flag `--mode skip|update` (default `update`). `update` regenera todos los mapas (comportamiento previo); `skip` solo genera los archivos que faltan en disco (los existentes se saltan, contados como `saltados`). El `--dry-run` en modo `skip` marca los archivos que ya existen con `[skip]`. En la TUI (Visualizaciones→Mapas→Mapas por municipio) se pregunta `?Generar solo los que faltan? (S/n)` con **default Sí** (modo `skip`), y las preguntas de variantes ahora tienen **default Sí** (`S/n`).

### Cambiado
- **Ruta de los mapas desde el track GPX** (`scripts/mapa_ruta.py` + `scripts/mapas_municipio.py`): la línea de ruta ya NO se dibuja con los GPS embebidos de los medios (que mayormente son `inferido_tiempo`/`track_gps`, derivados del track), sino con el **track GPX real** registrado en `tracks` (`Al_FaB_Tucuman.gpx`, 3920 puntos).
  - `mapa_ruta.py`: la PolyLine principal usa los 3920 puntos del track; los medios quedan como marcadores con su GPS propio. `--road-colors` colorea los segmentos del track por pendiente (calculada de la altitud del track). **Heatmap eliminado** (decisión del usuario).
  - `mapas_municipio.py`: las variantes `ruta`/`gradiente` usan el **tramo del track** cuyo tiempo cae dentro del rango `[min, max]` de timestamps de los medios del municipio; `contexto` usa el track completo en gris. Los medios quedan como marcadores.
- **Nuevo helper compartido `scripts/track_gpx.py`**: `cargar_tracks` (relee los .gpx de `tracks`), `puntos_track_con_tiempo`, `interpolar_posicion`, `tramo_temporal`, `distancia_haversine` (metros), `medir_discrepancias`/`reportar_discrepancias`. Envuelve la lógica que ya existía en `ubicar_videos_gpx.py`/`keypoints_contexto.py`.
- **Reporte de discrepancias media vs track**: ambos scripts ganan el flag `--tolerancia-metros` (default 1000). Compara el GPS embebido de los medios (`geolocation_source IN metadata/manual`) contra la posición interpolada del track en su timestamp; si la distancia supera la tolerancia, lo reporta en el log (solo reporte, no escribe DB). Con la DB actual: 10 discrepancias >1000 m (fotos de Colón, Bell Ville y Río Hondo — 2.2 a 2.6 km).
- **TUI**: `opcion_mapa` actualizada (sin heatmap, con `--tolerancia-metros`).

### Documentación
- **AGENTS.md**: `track_gpx.py` en estructura y catálogo; `mapa_ruta.py` agregado al catálogo (antes solo en estructura); descripciones de mapas actualizadas al track.
- **README.md**: `track_gpx.py` en tabla y estructura; descripciones de mapas actualizadas.
- **ROADMAP.md**: entrada en el historial.

---

## [Entrega 37] — 2026-08-18

### Añadido
- **Rutas de mapas por municipio en el retorno "Fluir"** (`scripts/td/puente_td.py` + `td/fluir_callbacks.dat` + `td/crear_tablas_fluir.dat`): nueva tabla `fluir_mapas` en TD (`[municipio, ruta]`). Cuando el visitante elige municipio(s), el puente emite `/flujos/fluir/mapa <municipio> <ruta>` × municipios, con la ruta **absoluta** al mapa HTML generado por `scripts/mapas_municipio.py`. El nombre de archivo coincide **exactamente** con `_nombre_archivo` del generador para la variante configurada (`VARIANTE_MAPA_MUNICIPIO`, default `ruta`): `mapas/mapa_municipio_<municipio>_<variante>.html` (espacios→`_`, acentos conservados); la ruta completa = raíz del proyecto + ese archivo. **El HTML no viaja por OSC** (evita el límite de tamaño del mensaje); TD guarda la ruta en `fluir_mapas` y decide cómo renderizarla (p. ej. Web Render TOP). Flag CLI `--no-enviar-mapas`. El callbacks la limpia al inicio de lote y **no cuenta en los `recibidos/esperados` del `/fin`** (valida medios del loop). Fase 2 pendiente: capas extra al mapa (marcadores por tags/colores), Web Render Source=DAT editable, sync con el loop.

### Cambiado
- **TUI Visualizaciones reestructurada**: nueva opción 1 "Mapas" que agrupa en un submenú "Mapa de ruta" y "Mapas por municipio"; el menú queda: 1 Mapas, 2 Exportar visualización web (deploy), 3 TouchDesigner (puente OSC). Árbol del README sincronizado (faltaba listar "Mapas por municipio") y columnas TUI del catálogo en AGENTS.md actualizadas.

### Documentación
- **`docs/retorno_fluir_td.md`**: contrato §1 y diagrama §0 con el mensaje `/mapa`; espejo §3.2 byte-idéntico al `.dat` actualizado; §4/§8 con `fluir_mapas`; decisión nº 14 (fase 1: solo ruta); §3.1 router de 9 addresses.
- **AGENTS.md**: bullet del retorno "Fluir" con `fluir_mapas` (9 tablas) + fila de `puente_td.py`.
- **README.md**: fila de `scripts/td/puente_td.py`.

---

## [Entrega 36] — 2026-08-17

### Añadido
- **Mapas por municipio** (`scripts/mapas_municipio.py`): genera un mapa HTML por cada municipio recorrido (74 municipios), con 4 variantes:
  - `ruta`: puntos del municipio + línea que los conecta
  - `puntos`: solo los marcadores del municipio
  - `contexto`: puntos destacados sobre la ruta completa (gris)
  - `gradiente`: segmentos coloreados por pendiente + leyenda
  - Nombre de archivo: `mapa_municipio_<municipio>_<variante>.html` (espacios→`_`, conserva acentos: `mapa_municipio_Melincué_ruta.html`). Salida en `mapas/`.
  - Reutiliza helpers de `mapa_ruta.py` (Folium, `_crear_popup`, `color_segun_gradiente`, leyenda de gradiente) para consistencia visual.
  - CLI: `flujos.py mapa-municipios` (alias `mapas`), args `--variantes`, `--municipio` (substring), `--output`, `--db`, `--dry-run`.
  - TUI: Visualizaciones→2 (menú reordenado: 1 Mapa de ruta, 2 Mapas por municipio, 3 Exportar visualización, 4 TouchDesigner).
  - Diseñado extensible para futuros tipos de mapa (feature "generar diferentes mapas").
  - Verificado: 74 municipios × 4 variantes = 296 archivos, 0 errores (~16 s).

### Documentación
- **AGENTS.md**: `mapas_municipio.py` en estructura y catálogo; numeración de Visualizaciones corregida (exportar→3, puente→4, osc_probe→4).
- **ROADMAP.md**: entrada en el historial (2026-08-17).
- **README.md**: `mapas_municipio.py` en tabla de scripts y estructura.

---

## [Entrega 35] — 2026-08-23

### Añadido
- **GUI "Fluir" en Python** (`gui_fluir.py`, raíz del proyecto): reemplaza la UI de chips de TouchDesigner y el listener del "Fluir". Muestra la lista completa de seleccionables cargada desde la BD (horas 24 · municipios 74 · colores 10 · tags 200), permite marcar con checkboxes multi-columna y al hacer "Consultar y enviar por 9002" delega en `puente_td._procesar_rafaga` → `loop_db.generar_loop` y emite el contrato completo por OSC 9002. TD queda reducido a escuchar el 9002 (9000/9001 ya no hacen falta para este gesto). Features de UI: scroll interno por pestaña (Canvas + scrollbar + rueda del mouse), filtro de búsqueda en vivo (municipios/tags, insensible a acentos/mayúsculas), contadores por pestaña + total global (`trace_add`), Todo/Nada por grupo, frecuencias discretas por item (`tag (23)`), textos truncados con `…`, envío en hilo (UI no se congela, botón deshabilitado) y resumen post-envío leyendo `td/spec_fluir.json`. La selección sobrevive al filtrado (las `BooleanVar` viven fuera de los widgets). Doc: `docs/gui_fluir.md`.

### Corregido
- **Tags del selector**: ahora son las **200 con más apariciones** ordenadas luego alfabéticamente (`sorted(contador.items(), key=-freq)[:200]` → sort por `_clave_alfabetica`). Antes eran las 200 primeras alfabéticas del universo total (~1143 únicas) y la lista "se quedaba en la C".
- **Ruta de import en `gui_fluir.py`**: calculaba la raíz subiendo 3 niveles (patrón de `scripts/td/`) desde un script que vive en la raíz → apuntaba afuera del proyecto, `import loop_db` fallaba en silencio dentro del hilo y no salía nada por 9002. Ahora 1 nivel + `sys.path` explícito (`scripts/td`, `scripts/ai_media`).
- **Doble disparo eliminado**: la versión previa heredó debounce + Timer + polling del listener; dos caminos podían procesar la misma ráfaga. Con la GUI como origen el disparo es directo (sin ráfaga ni debounce).

### Documentación
- **`docs/gui_fluir.md`** (nuevo): arquitectura, carga de datos (queries + fix de tags), features de UI, delegación del contrato 9002, uso, verificación completa y decisiones/lecciones.
- **AGENTS.md**: `gui_fluir.py` agregado a estructura, catálogo de scripts y tabla de documentos.

---

## [Entrega 34] — 2026-08-18

### Añadido
- **Chat de Telegram en el retorno "Fluir"** (`scripts/td/puente_td.py` + `td/fluir_callbacks.dat` + `td/crear_tablas_fluir.dat`): nueva tabla `fluir_telegram` en TD con el chat de los municipios elegidos por el visitante. El puente (modo `fluir`) consulta los mensajes con el **mismo criterio que la web** (`deploy/api/mensajes_telegram.php`): rango de fechas de los medios del municipio (`MIN/MAX timestamp_utc`), `es_sistema=0`, hora local UTC−3 (igual que `app.js`), texto truncado a 250 chars y `fotos` como JSON de media_ids. Emite el bloque `/tabla telegram <n>` + `/mensaje <id> <from_name> <texto> <hora> <fecha> <tipo> <fotos> <municipio>` ×N tras los chiches y antes del `/fin`; el resumen gana el 8º arg `telegram` (compatible hacia atrás: el callbacks lo lee con default 0). Flag CLI `--no-enviar-telegram`. El callbacks escribe `fluir_telegram` con header `[id, from_name, texto, hora, fecha, tipo, fotos, municipio]` y la limpia al inicio de lote. **No cuenta en los `recibidos/esperados` del `/fin`** (valida medios del loop, no el chat). Solo se envía si hay municipios elegidos (criterio web).

### Documentación
- **`docs/retorno_fluir_td.md`**: contrato §1 y diagrama §0 con el mensaje `/mensaje` y el 8º arg del resumen; espejo §3.2 byte-idéntico al `.dat` actualizado; §4/§8 con la tabla `fluir_telegram`; decisión nº 13; §3.1 router de 8 addresses.
- **AGENTS.md**: bullet del retorno "Fluir" con `fluir_telegram` (8 tablas).

---

## [Entrega 33] — 2026-08-18

Registra trabajo ya commiteado (2026-08-17) que quedó sin entrada en el changelog.

### Añadido
- **Diagnóstico de relojes Insta360** (`scripts/diagnosticar_camaras_360.py`): para cada .mp4 360° extrae `QuickTime:CreateDate` (UTC), bitrate, fps y timestamp del filename; clasifica cámara A (LA, +7h) / B (UTC+1, −1h) / B reconfigurada / desconocida y deduce la hora real argentina (CreateDate −3h). **Solo lectura, no escribe en DB.** NO TUI (decisión usuario 2026-08-17). Procedimiento completo en `docs/discrepancia_horarios_camaras.md`.
- **Ubicación de videos 360° por interpolación GPX** (`scripts/ubicar_videos_gpx.py`): los videos Insta360 remuxados perdieron su GPS embebido; el script muestrea su intervalo temporal cada `--intervalo` s, interpola (lat, lon, ele) contra el track GPX y colapsa momentos detenidos (umbral 5 km/h + distancia mínima 100 m). Escribe `media.latitude/longitude/altitude` (posición inicial, source `track_interpolado`), `media_keypoints` key=`ubicacion_video` por tramo y sentinels `ubicacion_video_estado` (`ok`|`sin_datos`|`fuera_rango`|`sin_track`) + `ubicacion_video_gaps` (JSON). Flags: `--solo-360`, `--mode skip|update|replace`, `--intervalo`, `--umbral-movimiento`, `--distancia-minima`, `--umbral-gap`, `--sobrescribir-gps`, `--dry-run`. NO TUI.
- **Ingesta de videos 360° con horas reales UTC**: la ingesta ya aplica la corrección de reloj documentada (embebido = UTC − 3h real).

### Cambiado
- **Modelo de keywords del SENTIDO: `gemma3:latest`** (`MODELO_TEXTO_DEFAULT` en `keywords_transcripciones.py`): ganó el A/B (93 llamadas, Ago 2026) contra `qwen2.5:3b` con el prompt endurecido P2 — reglas de formato, palabras prohibidas, fidelidad, artefactos de voz y keywords compuestas. Aplica a `--origen transcripcion` (`ia_keywords_transcripcion`) y `--origen texto` (`ia_keywords_texto`).
- **Nubes de elecciones ordenadas** (`scripts/td/elecciones.py`): municipios/tags/colores/clima en orden alfabético (case-insensitive, sin acentos), días en orden natural lunes→domingo, horas 0..23; `MAX_TAGS=200` (el parser OSC de TD corta mensajes con demasiados args) — la selección se hace por frecuencia (top cuartil acotado) y luego se ordena alfabéticamente.
- **`flujos.py`**: el texto del TUI que describe "Keywords desde transcripciones" menciona el modelo correcto (gemma3:latest).

### Corregido
- **`--transcode` en `exportar_visualizacion.py`**: el flag real es `--transcode` (action=store_true); la documentación previa mencionaba `--no-transcode` (inexistente). El TUI agrega `--transcode` solo si el usuario confirma transcodificar.

### Documentación
- **AGENTS.md/README.md sincronizadas**: catálogo de scripts (`diagnosticar_camaras_360.py`, `ubicar_videos_gpx.py`), mapa de datos (UBICAR VIDEOS GPX), claves `media_metadata` (`ubicacion_video_estado`/`ubicacion_video_gaps`), keypoint `ubicacion_video`, modelos por tarea (`gemma3:latest`), tabla de documentos (`docs/discrepancia_horarios_camaras.md`), subcomando `mapa`, estructura del proyecto.

---

## [Entrega 32] — 2026-08-17

Registra trabajo ya commiteado (2026-08-14/16) que quedó sin entrada en el changelog.

### Añadido
- **Pipeline de traducción EN→ES NO-AI** (`scripts/ai_media/glosario.py`): glosario persistente en JSON (`glosario_keywords.json` en la raíz del proyecto, 100% independiente de la DB) + motores clásicos — **Google vía `deep_translator` por defecto** y **Argos offline** opcional. Reemplaza la capa de traducción por IA (Ollama) como default del pipeline. Adaptación rioplatense de primera clase: viene del glosario (léxico manual del dominio) y de `reemplazos_descripcion` (reglas post-traducción tipo "coche"→"auto"), NO del motor. Prioridad de origen: manual > db_seed > auto.
- **`traducir_metadata.py` con `--motor`**: `google` (default, vía `deep_translator`), `argos` (offline), `glosario` (solo léxico) y `ollama` (conserva el pipeline legacy `translategemma`). `improve_db.py` (pasos keywords/descriptions/combinado) traduce con el pipeline NO-AI por defecto.
- **`generar_glosario.py`**: genera/amplía el glosario desde fuentes manuales + DB (pares alineados `ia_keywords_en`/`ia_keywords`, origen `db_seed`) y opcional `--extender --motor google|argos` (palabras del corpus ausentes → origen `auto`). `--dry-run` para previsualizar.
- **`generar_sinonimos_localidades.py`**: propone sinónimos de localidades cruzando los tags observados en `--clave` (default `ia_keywords`) contra georef/contexto.
- **`inferir_hora_textos.py`**: infiere timestamp de textos `type='text'` sin fecha interpolando SU PUNTO (lat/lon) contra el track GPX (posición → tiempo, ponderación por distancia inversa); `--umbral` (default 2000 m) descarta textos lejos de la ruta (`fuera_umbral`). Marca `media.geolocation_source = 'track_interpolado'`. Modos skip (solo NULL) / update. Diseño pendiente documentado en ROADMAP: textos que narran trayectorias más allá de su punto (ej: "De Saladillo a Bell Ville").

### Cambiado
- **Requisito nuevo**: `deep-translator` (`pip install deep-translator`) para el motor Google del pipeline NO-AI; Argos (`argos-translate`) es opcional/offline.
- **Docs sincronizadas**: AGENTS.md (modelos por tarea, mapa de datos TRANSLATE/INFERIR HORA TEXTOS, catálogo de scripts, lección "traducción = NO-AI"), README.md (modelos Ollama, tabla de scripts, `media_metadata`, enriquecimiento, documentos de diseño, estructura, dependencias, subcomando `ingest-textos`).

### Corregido
- **Revert de timestamps inferidos en textos históricos**: la inferencia aplicada el 2026-08-16 con `inferir_hora_textos.py` se revirtió el 2026-08-17 — los textos de `viajeros.md` son crónicas históricas (1729–2024), no parte del viaje 2025, y no deben llevar timestamp. Se limpiaron `timestamp_original`/`timestamp_utc`/`geolocation_source` (12 registros). `inferir_hora_textos.py` queda disponible para textos que sí pertenezcan al viaje. La nube de horas de elecciones ya los excluye (filtro `timestamp_utc IS NOT NULL`) y el loop usa `HORA_DEFECTO_TEXTO` para textos sin hora.

---

## [Entrega 31] — 2026-08-16

### Añadido
- **Videos 360° separados en el flujo "Fluir" (OSC 9002)**: loop_db.py marca cada video del spec con es_360 (True si media.subtype='360', escrito por improve_db --step video_metadata); puente_td.py separa video (normales) de video360 y los envía en bloques image → video → video360 → audio → text; el resumen ahora lleva 7 args (... <audio> <text> <video360>). Lado TD: tabla nueva fluir_videos_360 (misma estructura) en fluir_callbacks.dat y crear_tablas_fluir.dat; fluir_estado gana la fila video360; cotejo con es_360. Con la DB actual hay 0 videos 360° (la tabla queda vacía hasta que se ingiera material 360 y corra `--step video_metadata`).
- **Textos con contenido real en el "Fluir" (OSC 9002)**: para cada medio type='text', puente_td.py envía /flujos/fluir/texto <media_id> <titulo> <texto> justo después de su /medio, con el texto completo como unidad de medio (titulo_seccion + texto_completo; truncado de seguridad a 8000 chars); TD lo guarda en fluir_textos (nuevas columnas titulo/texto, HEADER_TEXTO) vía _recibir_texto (tabla[fila, col] por media_id, con fallback y guard de columnas ante pérdida de /tabla text). Sin ubicación/tags en las tablas (los resuelve el servidor de la DB).
- **Corrección de API Table DAT y backfill anti-pérdida en el "Fluir" (OSC 9002)**: setCell no existe en td.tableDAT — las celdas se escriben con tabla[fila, col] = valor (afectaba a _recibir_texto, _escribir_estado y _completar_textos_desde_spec); fix de fila[6].val → fila[6]. Nuevo backfill: al /fin, TD completa titulo/texto de fluir_textos desde td/spec_fluir.json cuando el mensaje /flujos/fluir/texto se pierde por UDP en ráfagas grandes.

### Cambiado
- **Prompt de duración del loop en el Fluir clarificado** (`flujos.py`): la intro del submenú FLUIR y el prompt `_preguntar_loop_secs` ahora explican que cada ráfaga arma un loop de 300 s por defecto y que el número define la ventana temporal [0..N] donde se reparten los medios (más segundos = más aire; menos = más ritmo). Sin cambios de comportamiento ni del default.
- **Título del proyecto: "Flujos y Diacronías"** (antes "Flujos"): actualizado el banner del TUI (`mostrar_bienvenida` y `AYUDA` — arte ASCII `ansi_shadow` de dos líneas "FLUJOS Y DIACRONÍAS"), el docstring de `flujos.py` y los títulos de README.md/VISION.md. Identificadores (`flujos.py`, `flujos.db`, comando `flujos`) sin cambios por convención.

---

## [Entrega 31] — 2026-08-24

### Corregido
- **Colores casi neutros ya no se etiquetan como colores** (`scripts/color_utils.py`): el naming mapeaba grises/blancos/negros (saturación < 0.15) a categorías coloreadas por dos vías: (a) match directo a colores CSS pálidos (lavanda, cardo, azure → violeta/azul) y (b) el sesgo anti-gris de `closest_css_color` promovía grises puros a "colores reales" a ≤1.5× de distancia sin mirar la saturación del píxel (un gris `#565656` → "verde"). Nueva **puerta de neutralidad** en `get_color_names()`: si sat < `UMBRAL_SATURACION_NEUTRO` (0.15) → clasifica por luminancia como `negro`/`blanco`/`gris` (con el CSS gris más cercano), sin pasar por el matching CSS. Blindaje defensivo en el sesgo anti-gris de `closest_css_color()`. Síntoma reportado: una foto blanco y negro aparecía bajo el filtro "violeta" de la web (`color_2 = #e8e8e9 → lavanda`). Datos regenerados: `improve_db --step colors --mode replace` (1048 imágenes, 0 errores) + sync de `deploy/db/visualizacion.db` (66 medios con algo violeta vs 67 antes; la foto B/N ya no matchea). Colores con tinte real (atardeceres azul-violeta, sat ≥ 0.15) conservan su clasificación.

## [Entrega 30] — 2026-08-14

### Añadido
- **Limpieza de descripciones con eco del prompt** (`scripts/limpiar_descripciones.py`): el modelo de visión (minicpm) a veces abría las descripciones regurgitando parte del prompt como meta-introducción ("To describe the image, we observe...", "Here's a long description of the image:", "Para describir la imagen, ..."). El script recorta esos prefijos de forma **determinista** (sin IA ni red) sobre `ia_description_en` y `ia_description`, con backup automático en `db/backups/`, `--dry-run`, `--solo-en`/`--solo-es`, assert de no-pérdida (sufix-strip estricto) e idempotencia. Invariante de negativos: ningún registro que ya empezaba con apertura legítima ("The image shows...", "La imagen muestra...") fue modificado. TUI Mantenimiento → Hoja 3 "Limpieza de datos" → 1; CLI `flujos.py limpiar-descripciones` / `descripciones`. Limpieza aplicada: **284 registros** (144 EN + 140 ES), residuos mid-text 0.
- **Prevención en `scripts/ai_media/image_analysis.py`**: nuevo `limpiar_meta_intro()` + `PREFIJOS_META_EN` (54 prefijos, familias A–I) que recorta las meta-intros al generarse las descripciones EN (`describir_imagen`, `_parsear_combinado` — cubre también `analyze_video` — y `_descripcion_utilizable`). Los 3 call sites quedan protegidos para corridas futuras.

### Cambiado
- **Prompts de descripción reescritos en `image_analysis.py` (ataque a la causa raíz del eco)**: `PROMPT_DESCRIBIR` pasa de `"Give me a long description of this image."` a `"Describe what you see in this image in detail. Start directly with the scene, without any preamble."` (elimina el "Give me"/"description" que invitaba al eco), y `PROMPT_COMBINADO` pide la descripción "written directly without preamble or meta-commentary". Validado con **A/B sobre 9 imágenes reales** (minicpm, temp 0.3, mismo proxy del pipeline): eco OLD 1/9 (11%, consistente con el ~13% de producción), **eco NEW 0/9**; sin regresiones de calidad (2 descripciones incluso más específicas); JSON del combinado robusto en los 3 casos; latencia ~20-30% menor por llamada. `limpiar_meta_intro` se mantiene como red de seguridad.
- **Fix 1 línea en `_parsear_combinado`**: el fallback no-JSON devolvía la descripción cruda sin pasar por `limpiar_meta_intro` (el único camino que se escapaba del cleaner); ahora queda limpio como el path de éxito.

### Corregido
- **Falso positivo en verificación de negativos**: el chequeo comparaba conteos antes/después de aperturas legítimas, pero los conteos suben legítimamente porque limpiar *revela* la apertura debajo de la meta-intro. Ahora verifica el invariante real: `violaciones_negativos` (ningún registro con apertura legítima modificado) = 0.

---

## [Entrega 29] — 2026-08-14

### Añadido
- **Nuevo helper compartido `scripts/td/util_enter.py`**: `detener_con_enter()` lanza un hilo daemon que bloquea en `input()` y setea un `threading.Event` al presionar Enter (con fallback EOF/Ctrl+C); lo usan `puente_td.py` (modo `fluir`) y `osc_probe.py` (modo indefinido) para salir limpio sin depender de Ctrl+C.
- **Fluir "Modo instalación"**: la escucha continua ya no tiene límite de tiempo — queda activa hasta que el usuario presiona Enter para detenerla (TUI Visualizaciones → 3 → 2 → 2).

### Cambiado
- **TUI TouchDesigner (Visualizaciones → 3)**: se eliminaron las opciones legacy — enviar colores, nube de tags, imágenes de un color y loop completo — porque apuntaban a ops que ya no existen en el .toe (`tabla_colores`, `nube_datos`, `movie1`). El menú queda: 1) Enviar elecciones, 2) Modo 'Fluir' (submenú: una ráfaga / modo instalación), 3) Probar OSC (eco).
- **`scripts/td/puente_td.py`**: eliminados los modos muertos (`enviar`, `colores`, `enviar_imgs`, `nube`) y sus helpers (consultas de colores, contador de keywords, `KEYWORDS_A_IGNORAR`); el CLI queda solo con `elecciones` (default) y `fluir`; se quitaron el argumento posicional `color`, `--cant` y `--max-tags`.
- **Salida limpia con Enter**: `fluir` y `osc_probe` (modo indefinido) se detienen presionando Enter (Ctrl+C queda como fallback); los mensajes de escucha lo indican.
- **Docs sincronizadas**: README (árbol TUI TouchDesigner) y AGENTS (catálogo de scripts TD y helper nuevo).

### Corregido
- **Ctrl+C en consola ya no mata el TUI**: `_correr()` en `flujos.py` lanza los scripts TD como proceso propio (`CREATE_NEW_PROCESS_GROUP` en Windows) y captura el `KeyboardInterrupt` para terminar el hijo sin cerrar `flujos.py`.

---

## [Entrega 28] — 2026-08-13

### Cambiado
- **Stickers de Telegram ya no se ingieren como media**: quedan solo en las tablas del chat (`telegram_messages`/`telegram_media`, documentan la conversación) y nunca entran a la tabla `media` (pipeline de enriquecimiento). `import_telegram.py` ajusta el mapeo (`detectar_type_media` → `other`), salta la ingesta de stickers en el loop principal y excluye stickers de la recuperación de medios pendientes. Nuevo script `scripts/limpiar_stickers.py` para limpiar los 10 stickers ya ingeridos (dry-run + backup automático en `db/backups/`).

---

## [Entrega 27] — 2026-08-13

### Añadido
- **TUI Visualizaciones → opción 3 "TouchDesigner (puente OSC)"**: expone los modos de `scripts/td/puente_td.py` y `scripts/td/osc_probe.py` en el TUI para operar TouchDesigner sin consola — enviar colores a TD, nube de tags (keywords, `--max-tags`), elecciones (horas, municipios, colores, tags, días, clima; `--grupo`), imágenes de un color (`--cant`), loop completo (`enviar`), modo "Fluir" (submenú con escuchar una ráfaga `--una-vez` / escucha continua, ambos con `--loop-secs`) y probar OSC (eco) con puerto y ventana configurables. Todos los modos del puente pasan `--db` con la ruta resuelta y la salida se fuerza a UTF-8 (`PYTHONIOENCODING`).

### Cambiado
- **Scripts TD movidos a `scripts/td/`**: `puente_td.py`, `elecciones.py` y `osc_probe.py` pasan a `scripts/td/` (antes en `scripts/`); se corrigió la resolución de rutas (bootstrap standalone, `_importar_loop_db` y spec default `td/spec_fluir.json`). `loop_db.py`/`loop_engine.py` NO se mueven (agnósticos del renderizador). Documentación actualizada.

---

## [Entrega 26] — 2026-08-11

### Añadido
- **Fase 1 — Auditoría de contenedores** (`scripts/detectar_contenedores.py`): audita video/audio con ffprobe (streams faltantes) y anota `media_metadata` con `contenedor_estado` (`ok` | `sin_video` | `sin_audio` | `sin_contenido` | `error_ffprobe` | `archivo_faltante`) y `contenedor_streams` (JSON). TUI Mantenimiento → paginado con 2 hojas; opción 9 en Hoja 1. CLI `flujos.py detectar-contenedores` / `contenedores`.
- **Fase 1 — Contenido repetido por audio** (`scripts/repetir_contenido.py`): cross-correlación coseno del vector RMS por ventana (2 s, hop 0.5 s) entre pares de medios; reporta similitud y lag. **Solo reporta**, no escribe en DB (`--json` opcional). En `--contra` el pasaje más corto va como base (evita falsos negativos). TUI Mantenimiento Hoja 2 "Auditoría de medios" → 1; CLI `flujos.py repetir-contenido` / `repetidos`.
- **Fase 1 — Crossref audio-frames** (`scripts/audio_frame_crossref.py`): clasifica ventanas de 10 s con CED-mini (audio tagging local) y mapea los sonidos a los frames del video en ese rango. Solo reporta; extracción opcional de frames con `--frames-dir`. TUI Mantenimiento Hoja 2 → 2; CLI `flujos.py audio-frame` / `crossref`.
- **Fase 3 — Keypoints semánticos de video** (`scripts/ai_media/keypoints_video.py`): desde `video_analysis` escribe en `media_keypoints` key=`escena` (keywords de la escena) y key=`keyword` (keyword individual), source='ollama', con `timestamp_offset_secs` = inicio de la escena. Sentinel `media_metadata.keypoints_video_estado` (`ok` | `sin_datos`). NO toca los keypoints de transcripción (`key='transcription'`).
- **Fase 4 — Keypoints de contexto (devenir geográfico)** (`scripts/keypoints_contexto.py`): F1 interpola la posición del medio contra los tracks GPX (multi-track con prioridad contiene → solapa → cercano → sin_rango; muestreo cada `--intervalo` 30 s), F2 transiciones baratas (elevación ±50 m, día/crepúsculo/noche, movimiento ≤5 km/h), F3 enriquece con Georef + clima en frecuencia gruesa (300 s) con cache en memoria y JSON, F4 escribe `media_keypoints` key=`contexto_*` (`contexto_elevacion`, `contexto_astronomia`, `contexto_ubicacion`, `contexto_clima`, `contexto_movimiento`) con source `track_interpolado` | `estimado` | `gps_propio`. Sentinel `media_metadata.keypoints_contexto_estado` (`ok` | `sin_datos`). TUI Mejorar DB Hoja 3 → 2; CLI `flujos.py keypoints-contexto` / `keypoints`.
- **TUI Mejorar DB → Hoja 3 "Analisis de video"**: opción 1) Analizar video (escenas + IA), 2) Keypoints de contexto. La Hoja 2 pasa a tener navegación bidireccional (p Anterior / n Siguiente).
- **TUI Visualizaciones → opción 2 "Exportar visualización web (deploy)"**: submenú con deploy a `deploy/` (default) o a otra carpeta — en ambos casos **pregunta si transcodificar** videos grandes/360° a MP4 web (S/n) —, re-exportar snapshot local (`--snapshot-local`, `deploy/db/visualizacion.db` sin copiar medios), regenerar spec del loop (`loop_db.py --salida deploy/spec.json`) y previsualizar deploy (`--dry-run`).
- **Exportador genérico de deploy** (`scripts/exportar_visualizacion.py`, movido desde la web prototipo): el script ya no pertenece a la web prototipo sino al pipeline — sirve a **cualquier implementación web** (deploy es solo un destino posible). Deploy por defecto a `deploy/` (raíz del proyecto) con copia de medios a `<dir>/media/...` y transcode opcional (`--transcode`, `--transcode-box`, `--transcode-360-largo`, `--dry-run`); `--snapshot-local` conserva el comportamiento dev local (rutas absolutas, sin copiar). Documentado en `docs/deploy.md`.

### Cambiado
- **Fase 0 — `content_hash_video` eliminado de la ingesta** (`scripts/ingest.py`): el hash de contenido para videos (frame a 0.5 s, débil) desaparece — `content_hash` = `file_hash` para videos; se quitó la opción `--compute-video-hash` y la notificación de duplicados por content_hash en video (imágenes mantienen phash). ROADMAP Etapa 2 actualizado a Hecho.
- **Fase 0 — `backfill-end-time` corregido** (`flujos.py`): usa `datetime.fromisoformat` robusto, es idempotente (skip por `end_time IS NULL`) y pregunta si reparar `end_time == timestamp_utc` con duración.
- **Fase 0 — `video_analysis.py` eliminado**: el script legacy (keyframes + descripción) se borró; su export salió de `scripts/ai_media/__init__.py`. El análisis de video lo cubre el rediseño de `analyze_video.py` (Fase 3).
- **Fase 0 — key de keypoints corregida a `transcription`**: el paso `improve_db --step keypoints` usa consistente `key='transcription'` en skip/update/replace (antes convivían `transcription` y `transcript_segment`).
- **Fase 0 — modelos → `minicpm-v4.6` en docstrings** y defaults de los scripts de visión (era `qwen2.5vl:3b` en la doc).
- **Fase 3 — `analyze_video.py` rediseñado**: el análisis pasa a **muestreo por escenas** — scene detection (ffmpeg) → agrupar en escenas → ~10 imágenes por escena → selección por nitidez (sin IA) → **1 llamada de visión PROMPT_COMBINADO por escena** (keywords + descripción, máx 20 tags). Flags `--por-escena` y `--mejores-por-escena`; **se eliminó `--interval`**. TUI Mejorar DB Hoja 3 → 1; CLI `flujos.py analizar-video` / `analizar`.
- **Fase 2 — Embeddings retirados del TUI** (consolidado): `generate_embeddings.py`/`clustering.py` quedan disponibles solo por CLI; rediseño profundo pendiente (ROADMAP).
- **TUI Mantenimiento paginado**: `opcion_mantenimiento` migra a `_menu_paginado` con 2 hojas — Hoja 1 "Mantenimiento general" (1-9, gana la 9 "Auditar contenedores"), Hoja 2 "Auditoría de medios" (1 "Buscar contenido repetido (audio)", 2 "Correlacionar audio con frames").
- **AYUDA CLI actualizada** (`flujos.py`): documenta los comandos nuevos (`detectar-contenedores`, `repetir-contenido`, `audio-frame`, `analizar-video`, `keypoints-contexto`, `ingest-textos`) y el routing correspondiente en `main()`.
- **`numpy` documentado como dependencia** en AGENTS.md (cross-correlación de audio y clustering de embeddings).

### Corregido
- **Ronda de code review (2026-08-11)**: `opcion_geocode()` restaurada como función propia en `flujos.py` (había quedado colgando del cuerpo de `opcion_keypoints_contexto` por un refactor previo) y re-enlazada en Hoja 2 → 6; **5 submenús migrados a `_menu(...)`** (Analizar video, Keypoints de contexto, Auditar contenedores, Repetir contenido, Crossref audio-frame) con `pausa()` en las callables; `improve_db --step keypoints` **acotado a `key='transcription'`** (no borra `contexto_*`/`escena`/`keyword` en update/replace); `_seleccionar_gruesas` tolerante a intervalos no divisores de la frecuencia (elige la muestra más cercana al múltiplo); `keypoints_video.py` con import normal de `timezone` (sin `__import__`); `detectar_contenedores.py` anota `archivo_faltante` y usa `log.warning` en fallos de ffprobe; `analyze_video.py` renumera las escenas de forma contigua.

### Reconciliación de entregas anteriores
- La Entrega 25 describía "Mejorar DB con 2 hojas (se eliminó la Hoja 3 de cierre)" — sigue siendo cierto (la Hoja 3 de "Pasos de cierre"/embeddings no volvió), pero hoy hay una **Hoja 3 nueva "Analisis de video"** (Fase 3). Estado final: 3 hojas (IA y color / Etiquetado + inferencia y ubicación / Analisis de video), embeddings fuera del TUI.

---

## [Entrega 25] — 2026-08-11

### Cambiado
- **Embeddings retirados del TUI** (decisión ROADMAP 2026-08-09, ejecutada): `opcion_improve_db` ahora tiene 2 hojas — se eliminó la Hoja 3 ("Pasos de cierre") que exponía "1) Embeddings". La función `opcion_embeddings()` se quitó (dead code) y `generate_embeddings.py` queda disponible solo por línea de comandos. Está pendiente un rediseño profundo de la capa semántica antes de re-exponerla.

---

## [Entrega 24] — 2026-08-11

### Cambiado
- **TUI refactorizado a helpers genéricos** (`flujos.py`): los menu-loops manuales se reemplazaron por `_menu` (menú simple), `_menu_paginado` (hojas de hasta 9 opciones con navegación `n`/`next` y `p`/`prev`) y `_ejecutar_paso_mejora` (patrón `_preguntar_modo` + `_ejecutar_improve_db`). Texto visible y comportamiento idénticos (regla de oro).
- **`_menu` ampliado**: kwargs opcionales `pre_titulo` (cabecera custom que reemplaza `limpiar_pantalla`+título+intro; ej: banner + resumen DB de `tui()`), `etiqueta_salir` (default "Volver"), `on_salir` (acción al elegir "0", ej: "Chau"), `cerrar_al_ejecutar` (rompe el loop tras ejecutar una opción válida — replica el `break` de submenús). Defaults retrocompatibles con los callers previos.
- **Nuevo helper `_menu_paginado`**: hojas con hasta 9 opciones; navegación `n`/`next` y `p`/`prev` ("Opcion invalida." + `pausa()` en la primera/última hoja); `0` rompe. En hojas con Anterior y Siguiente, **p se lista primero** y luego n.
- **`opcion_improve_db`** → `_menu_paginado("MEJORAR BASE DE DATOS", 3 hojas)`: Hoja 1 "Pasos de IA y color", Hoja 2 "Etiquetado + inferencia y ubicacion", Hoja 3 "Pasos de cierre"; firma ahora acepta `db_path`.
- **`opcion_refinar_keywords`** → `_menu` externo (3 familias: Imagenes/Transcripciones/Textos) + `_menu` interno por familia con `cerrar_al_ejecutar=True` (Refinar todos / Previsualizar).
- **`opcion_keywords_transcripciones`** → `_menu` externo (2 orígenes) + `_menu` interno por origen con `cerrar_al_ejecutar=True` (4 modos).
- **`tui()`** → `_menu` con `pre_titulo=_cabecera` (bienvenida + resumen DB), `etiqueta_salir="Salir"`, `on_salir=_chau`.

---

## [Entrega 23] — 2026-08-09

### Añadido
- **Rediseño del motor de loop `loop_db.py` — "mensaje" por rangos y prioridades, tablas POR TIPO** (consigna del usuario 2026-08-09): el "mensaje" del Fluir es un concepto (la ráfaga completa aunque sean N paquetes OSC) y un "medio" es foto/video/audio/texto. `generar_loop()` ahora:
  - **Horas**: la línea de tiempo respeta el orden de llegada y filtra con **rango [min,max] inclusive** (elegir 6 y 13 → medios con hora local 06:00–13:59).
  - **Municipios**: filtro duro si vienen en el mensaje (municipio IN).
  - **Colores**: **prioridad** (score por slot color_1/2/3 coincidente), solo imágenes — los videos/audios/textos NO se descartan.
  - **Tags**: **prioridad** — score por etiqueta contenida en `ia_keywords` — NO descartan.
  - Orden de salida: **hora asc, score desc como desempate (id asc)**.
  - **`keypoint`** = ubicación temporal del medio **dentro del loop** (el `t_loop` que ya produce `armar_spec`) — replicado como campo `keypoint` en cada medio.
  - **Salida nueva `spec["por_tipo"]`** = `{image:[...], video:[...], audio:[...], text:[...]}` (mantiene `spec["medios"]` retro-compat) + `spec["resumen"]` = {total, image, video, audio, text, rango_horas, notas}.
  - Chiches: explícito que solo salen de **clima y astronomía** (era ya el caso).
  - CLI: agrega `--por-tipo` (imprime el resumen por tipo); `--dry-run` muestra rango/cantidades/score medio sin escribir.
- **`puente_td.py` modo `fluir` — contrato OSC 9002 por tipo**: reescrita `_procesar_rafaga` para emitir `/flujos/fluir/resumen <total> <loop_secs> <image> <video> <audio> <text>` → por tipo en orden `image, video, audio, text` (solo si tiene medios): `/flujos/fluir/tabla <tipo> <cant>` + `/flujos/fluir/medio <media_id> <ruta> <keypoint> <hora> <tipo>` → `/flujos/fluir/chiche <hora> <texto>` → `/flujos/fluir/fin <total>`. Flag `--enviar-medios` (default True; `--no-enviar-medios` solo resumen+fin). Verificado de punta a punta: ráfaga falsa (horas 6+13, tag paisaje, color azul, municipio Inriville) → 6 imágenes de Inriville en rango → OSC 9002 completo (resumen 6/6/0/0/0, tabla image 6, medio×6 con keypoint/hora, chiche×2, fin).
- **`td/fluir_callbacks.dat` reescrito — receptor por tipo**: `esc_in2_callbacks` (9002) ahora distribuye en **tablas por tipo**: `fluir_fotos` (image), `fluir_videos`, `fluir_sonidos` (audio), `fluir_textos` (text), todas `[media_id, ruta, keypoint, hora, tipo]`; `fluir_estado` lleva además `recibidos`/`esperados`; `fluir_medios`/`fluir_posiciones`/`_consumir_spec` eliminados (el `OSC` trae todo; el `spec` solo se lee para **cotejar** pérdida UDP). Helper único `_tabla_para_tipo(tipo)`. Mismo patrón File+Sync en `td/fluir_callbacks.dat`.
- **`docs/retorno_fluir_td.md` actualizado** al contrato por tipo y tablas `fluir_*` nuevas (checklist, esqueleto callbacks, 4 tablas de medios, decisiones cerradas: keypoint = t_loop, audio → `fluir_sonidos`).
- **El estado del loop refleja el filtro del usuario**: `spec["resumen"]["filtros"]` nuevo (horas, municipios, colores, tags, dias, clima) en `loop_db.py`; el puente envía por 9002 **`/flujos/fluir/filtro <clave> <valor>`** (hora_inicio, hora_fin, horas_elegidas siempre; municipios/colores/tags/dias/clima si vienen) y el callbacks `_recibir_filtro` los escribe como filas `[clave, valor]` en `fluir_estado` — así TD muestra qué eligió el visitante, no solo los totales. `/fluir_callbacks.dat` reescrito en UTF-8 limpio (corrige encoding mixto previo). Verificado de punta a punta: `/filtro` × 6 (hora_inicio=6, hora_fin=13, horas_elegidas="6, 13", municipios=Inriville, colores=azul, tags=paisaje) + spec con `filtros` estructurados.

### Cambiado
- **`docs/lecciones_elecciones_td.md` y `ROADMAP.md`**: rediseño del "Fluir" documentado — modelo filtrós duros (hora rango + municipios) y prioridades (color/tags), orden hora+score, keypoint, tablas por tipo. ROADMAP Etapa 5 "Fluir": lado Python ✅, falta receptor TD.

---

## [Entrega 22] — 2026-08-09

### Añadido
- **Modo `fluir` en `puente_td.py` — cierre del ciclo "Fluir" (TD → Python → respuesta)**: el puente ahora escucha en 9001 la ráfaga del botón "Fluir" de TD (formato verificada `/flujos/seleccion/<grupo> <valor>`, descarga de la tabla acumulada — sale toda junta en el único click), la **acumula por grupo** (tags, colores, municipios, horas, días, clima), detecta el fin de ráfaga por **debounce** (default 0.7s, `--debounce`), traduce los grupos a los filtros de `loop_db.generar_loop` (mapeo en constante `GRUPOS_OSC_A_FILTRO`, horas `'13:00'` → 13) y genera el spec del loop. Retorno por **canal separado 9002** (constante `OSC_PUERTO_TD_RESULTADO`), NO toca `osc_in1`/9000: escribe `td/spec_fluir.json` (flag `--spec-salida`) y envía `/flujos/fluir/resultado <cantidad> <loop_secs>` → `/flujos/fluir/medio <media_id> <ruta>` (uno por medio del spec) → `/flujos/fluir/fin <cantidad>`. Flags: `--debounce`, `--loop-secs`, `--spec-salida`, `--una-vez`. Probado de punta a punta sin TD (ráfaga falsa por 9001 → spec `td/spec_fluir.json` con 1 medio/23 segmentos/1 chiche + `osc_probe.py 9002` recibiendo los 3 mensajes).
- **`docs/retorno_fluir_td.md` — guía de armado manual del receptor 9002 en TD**: arquitectura del canal de retorno aislado (`osc_in2` OSC In DAT 9002 + `osc_in2_callbacks` → `td/fluir_callbacks.dat` con File+Sync), tablas `fluir_estado`/`fluir_medios` (OSC) y `fluir_posiciones`/`fluir_chiches` (JSON), esqueleto completo del callbacks, estrategia híbrida de consumo (OSC inmediato + JSON en `fin`), opciones de reproducción coherentes con `docs/motor_loop.md` (reloj de loop, cursor de medio activo, chiches), regla dura de no pisar `osc_in1`/`elec_*` ni recrear nombres legacy (`movie1`, `tabla_colores`, ...), checklist de armado y decisiones abiertas (duración efectiva de imágenes, verificación de paquetes OSC perdidos, `loop_secs` desde tabla).
- **`docs/lecciones_elecciones_td.md` — "Decisión clave" de canal de retorno**: documentado que el "Fluir" es un evento único (descarga de la tabla acumulada; no hay toggles individuales en el wire) y que la respuesta NO debe tocar `osc_in1`/9000 — el retorno va por **9002 separado + `osc_in2`** (tabla de los 3 canales OSC definitiva). ROADMAP Etapa 5 "Fluir" actualizado a "en implementación".

### Cambiado
- **`scripts/td/puente_td.py` — docstring/epilog**: nuevo modo `fluir` documentado con ejemplo de prueba sin TD (3 terminales: `fluir` + enviador + `osc_probe.py` 9002).

---

## [Entrega 21] — 2026-08-08

### Cambiado
- **Documentación sincronizada con el mapa REAL de TouchDesigner**: export OP Find (`td/opfind1.tsv`, 459 ops) verificado. `AGENTS.md` (estructura de operadores) y `docs/lecciones_elecciones_td.md` ahora documentan la arquitectura actual del toe: `elec_<id>` Table DATs → `elec_<id>_container<N>` con `replicator1` → `boton_<id>_N` (Button COMP con hijos fijos `par1`/`text`/`parexec1`/`panelexec1`), `osc_out1` confirmado. Aclarado que `movie1`/`tabla_colores`/`nube_datos`/`nube_container`/`color_actual`/`seleccion_actual`/`info_imagen` **ya no existen** en el toe (handlers de colores/slideshow apuntan a ops por recrear; pipeline visual en construcción).
- **Handler muerto de nube eliminado de `osc_callbacks.dat`**: `/flujos/nube/datos` escribía en `nube_datos` (Table DAT que ya no existe — la nube de Text TOPs fue reemplazada por las elecciones con replicators). El docstring ahora solo lista los handlers vivos (`/flujos/elecciones/<id>`) y los pendientes del pipeline visual.

### Registrado (decisiones de diseño TD, 2026-08-08)
- **Manual por ahora**: se decide seguir armando los grupos de elecciones manualmente en TD (Replicator + `elec_<id>_container<N>` + `boton_<id>_N`); opción A (que `elecciones_ui.dat` genere lo mismo) queda diferida en ROADMAP Etapa 5 como refactor futuro.
- **"Fluir" como término del disparo de selección**: igual que en la web (`#btn-fluir`), en TD el visitante **acumula** elecciones y al presionar **"Fluir"** se envía **todas las selecciones juntas**. La ráfaga sobre 9001 (verificada con `osc_probe.py`) ES el "Fluir": 2 tags + 2 colores + 2 municipios + 2 horas en formato `/flujos/seleccion/<grupo> <valor>`. El diseño futuro es que el "Fluir" acumule en TD y envíe **un solo mensaje grande** (formato a definir) que Python procesa como selección completa → `loop_db.py` → spec JSON. Documentado en `docs/lecciones_elecciones_td.md` (sección 2) y ROADMAP Etapa 5.
- **Formato OSC real verificado con `osc_probe.py` (9001)**: en 60s llegaron 8 mensajes del toe; el formato real es `/flujos/seleccion/<grupo> <valor>` (grupo en el address, un solo valor, sin flag 1|0) — **corregida la doc** que decía `/flujos/seleccion <grupo> <valor> 1|0`. Confirma el comportamiento toggle-OSC actual (un mensaje por toque).

---

## [Entrega 20] — 2026-08-08

### Cambiado
- **TUI Ingesta reordenado** (`flujos.py` `opcion_ingesta`, README): el orden ahora es 1) Ingerir multimedia, 2) Ingerir track GPS (GPX), 3) Ingerir textos (.md), 4) Ingerir chat de Telegram, 5) Deshacer ingesta. Antes Textos y Telegram estaban invertidos y "Deshacer" al final.
- **Renombrado "Limpieza de tandas" → "Limpieza de tandas de fotografías"** (`flujos.py`, README): clarifica que la limpieza aplica a tandas de fotos (no a otros medios).
- **TUI Mejorar DB: audio de corrido y etiquetado al final de la sección** (`flujos.py` `opcion_improve_db`): reorden sobre diagnóstico funcional — Audio tagging es **tagging** (no descripción), Keywords desde transcripciones es **keywords de todo tip** (luego pasará a textuales), Refinar keywords es de **toda clase de keywords**. Quedó así:
  - Hoja 1 termina: 7) **Audio tagging** (primero de los de audio), 8) **Transcripción**, 9) **Keypoints** (audio de corrido que cruza a la Hoja 2).
  - Hoja 2 arranca: 1) **Keywords desde textos y transcripciones** (último de los de audio; hoy también cubre keywords de textos .md vía `--origen texto` → `ia_keywords_texto`), 2) **Refinar keywords** (fin de la sección de etiquetado/multimedia; candidata a moverse antes de Embeddings — se decide más adelante); luego 3-9 inferencia y ubicación (timestamps, GPS, gradientes, localización, clima, día de semana, astronomía).
  - Título de Hoja 2: "Etiquetado + inferencia y ubicación".
- **Regla de navegación p antes que n** (`flujos.py` Hoja 2, AGENTS.md): en hojas con Anterior y Siguiente, la opción **p << Anterior se lista primero** y luego **n Siguiente**. Aplicado a Hoja 2 y documentado en AGENTS.md "Reglas de desarrollo (menú TUI)".

### Añadido
- **Keywords del SENTIDO para textos .md** (`scripts/ai_media/keywords_transcripciones.py`): el script ahora soporta `--origen {transcripcion,texto}` (default `transcripcion`, retrocompatible). Para `texto`, lee `texto_completo` de medios `type='text'` y escribe `ia_keywords_texto` (clave NUEVA en `media_metadata`), misma lógica/prompt (encabezado "Leé este texto...") y protecciones. TUI (Hoja 2→1) renombrada a "**Keywords desde textos y transcripciones**": submenú de origen (1 transcripciones, 2 textos) y flujo de modos pasando `--origen`. TUI **Refinar keywords** (Hoja 2→2): submenú de familia (`ia_keywords` / `ia_keywords_transcripcion` / `ia_keywords_texto`) pasando `--clave` a `refinar_keywords.py` (ya lo soportaba). AGENTS.md sincronizado (clave `ia_keywords_texto`, mapa de datos con "KEYWORDS TEXTOS", catálogo).

---

### Cambiado
- **Traductor EN→ES cambiado a `translategemma`** (`traducir_metadata.py`, `improve_db.py` `_traducir_metadata`): auditoría cruzada de keywords EN (crudo de visión) vs ES reveló que la basura (`户外`, `ripio/grava`, checklist de `banquina/manubrio/...`) la generaba el **traductor qwen2.5:3b** (colapso en checklist/chino), NO la visión (minicpm da EN limpio). Batería de 11 casos reales de la DB: qwen2.5:3b **score −2.3** (3 chino, 5 checklist, 5 slash) vs **translategemma +1.6** (0/0/0, fiel, 10/11 conteo exacto). Modelos grandes sin ventaja y más pesados (requisito: hardware limitado) → se eligió translategemma (3.3GB, 4.3B, especializado). Nuevo `MODELO_TRADUCCION_DEFAULT = "translategemma"`.
- **Re-traducción masiva aplicada**: `traducir_metadata.py --paso keywords --mode update` → **702 ok / 0 errores**. Verificado en DB: chino 15+ → 0, slash 55 → 1, checklist 90 → 1.
- **GLOSARIO eliminado** de `traducir_metadata.py`: test A/B demostró que era **decorativo** (0 términos usados por translategemma en 11 casos con/sin) y su regla "usá EXACTAMENTE" empujaba al modelo viejo al checklist/slash. Se quitaron la constante `GLOSARIO` y la regla 5 de `PROMPT_TRADUCIR_AMBOS`/`PROMPT_TRADUCIR_KEYWORDS`.
- **Capa semántica eliminada de `refinar_keywords.py`** (ahora 2 capas: léxica + diccionario): `--usar-embeddings` con `paraphrase-multilingual:latest` introducía **falsos sinónimos** que degradaban el dominio (`ciclismo→deporte`, `nublado→soleado`, `parche→parque`, `cesta→ruta`). Se quitó por completo (no queda ni como opción): `refinar_con_embeddings()`, `similitud_coseno()`, `MODELO_EMBEDDINGS`, args `--usar-embeddings`/`--umbral`, e import `math`.
- **`ciclismo`/`ciclista(s)` dejaron de colapsar** a `deporte`/`personas` en `SINONIMOS` de `refinar_keywords.py`: `ciclismo` pasó a término canónico propio (variantes: ciclista, ciclistas, cycling, cyclist, cyclists, pedaleando).
- **TUI `opcion_refinar_keywords`** (`flujos.py`): se eliminaron las opciones de embeddings (antes 2 "capa semántica" y 4 "dry-run con embeddings"); ahora opción 1 "Refinar todos (update)" y 2 "Previsualizar (dry-run)".

---

### Cambiado
- **Transcripción con VAD + filtro de alucinaciones** (`transcribe.py`, `improve_db.py`): auditoría de las 217 transcripciones detectó **alucinaciones masivas de Whisper** — solo 144/217 (66%) en español; 73 en idiomas aleatorios (noruego-nynorsk 28, inglés 36, italiano 4, javanés 2, coreano 1, portugués 1, turco 1), 58 de los 73 con confianza de idioma < 0.5. Causa raíz: faster-whisper corría **sin VAD** sobre clips de **ruido ambiental (cámaras GoPro sin habla)**, con `language=None` y `condition_on_previous_text=True` → inventaba basura repetitiva ("I'm going to finish it" ×10, "Bu ne? Bu ne?"), incluso con `language_probability` alta (0.79 → "4-5-6-7-8"). Factores desencadenados: idioma aleatorio en silencio, sin detección de voz, lazo de repetición auto-alimentado y sin filtro de texto.
- **Removido el paso `transcribe_zg`** de `improve_db.py`: era redundante — con el `run_transcribe` nuevo (VAD + autoidioma + filtro de confianza + no guarda basura en `sin_voz`) ya no queda "zona gris" que arreglar: mismo motor y parámetros, en una regeneración completa (`--mode replace`) `transcribe` cubre todo. Se eliminaron `_query_zona_gris`, `check_transcribe_zg`, `run_transcribe_zg`, la entrada del REGISTRY, de `DEP_ORDER` y del docstring. La doc (AGENTS.md, README.md, mapa de datos, catálogo, nota histórica) quedó sincronizada.
- **Fix import `clasificar_estado`** (`improve_db.py` `run_transcribe`): el paso usaba `clasificar_estado()` pero el import solo traía `transcribir_audio` → NameError al guardar (`name 'clasificar_estado' is not defined`), por lo que las transcripciones se procesaban (VAD OK) pero **no se escribían en la DB**. Se agregó `clasificar_estado` a ambos imports (normal + fallback con `sys.path`).
- **`skip` auto-recuperable en `run_transcribe`**: la query de modo `skip` ahora considera pendiente **solo** a los archivos **sin `whisper_estado`** (ni tocados, ni corte a mitad de batch / checkpoint), retomando cualquier corrida interrumpida (Ctrl+C/cuelgue) en la siguiente pasada con `--mode skip`. El marcador de "terminado" es `whisper_estado` y **no** `whisper_segments`, porque con el ajuste de abajo un archivo `sin_voz` queda con estado pero sin segmentos (usar `whisper_segments` re-transcribiría los `sin_voz` en cada corrida).

### Añadido
- **`transcribe.py` gana soporte VAD + confianza**: parámetros `vad_filter`/`vad_parameters`, `condition_on_previous_text`, umbrales (`no_speech_threshold`, `compression_ratio_threshold`, `log_prob_threshold`), y `incluir_metricas` → cada segmento lleva `promedio_logprob`, `no_hay_habla_prob`, `ratio_compresion`. Nuevos helpers `filtrar_segmentos_confiables()` (logprob ≥ -0.8, no_habla < 0.6, compresión < 2.4, duración ≥ 1.5 s) y `clasificar_estado()` (`ok` | `sin_voz`). Retrocompatible con segmentos sin métricas.
- **`run_transcribe` en `improve_db.py`** mejorado: ahora usa modelo `small` + VAD + autodetección + filtro de confianza, escribe `whisper_estado`, y usa checkpoint incremental (`Checkpoint` cada 20) en vez de un único commit final.
- **`run_transcribe` no guarda basura en `sin_voz`**: ahora `run_transcribe` solo persiste `whisper_segments`/`whisper_info` cuando clasifica `ok`; en ruido/silencio (`sin_voz`) deja únicamente la marca `whisper_estado=sin_voz`.
- **Docs**: `AGENTS.md` sincronizado (mapa de datos, catálogo, nota histórica).

---

## [Entrega 20] — 2026-08-05

### Cambiado
- **Eliminado el género fotográfico de las keywords** (era el comodín "otras"): la auditoría mostró que el 77% de los medios (544/702) tenía "otras" como primera keyword porque la visión ya no pide género (prompts con keywords libres desde Ago 2026) pero `refinar_keywords.py` seguía forzándolo en post-proceso. Se eliminó la lógica completa de género:
  - `image_analysis.py`: eliminados `GENEROS_FOTOGRAFICOS`, `_GENEROS_STR` y `_validar_genero()` (y su llamada en el fallback de `analizar_imagen_completo`). Verificado: `PROMPT_KEYWORDS`/`PROMPT_COMBINADO` ya no pedían género; `PROMPT_CLASIFICAR` conserva categorías (utilidad CLI `clasificar_imagen`, aparte del pipeline).
  - `refinar_keywords.py`: eliminados `GENEROS_FOTOGRAFICOS`, `VARIANTES_GENERO`, `es_genero()` y `_tiene_mezcla_generos()`; `refinar_lista_keywords()` ya no busca género ni inserta "otras" al inicio — ahora solo normaliza, singulariza, aplica sinónimos, deduplica y recorta a máx 7.
- **Limpieza de la DB**: se confirmó que "otras" fue insertada COMO EXTRA (no reemplazó ningún keyword válido: 0 casos con ES vacío teniendo EN). Se borró el token "otras" de los 544 registros `ia_keywords` **sin re-traducir** (no se perdió información). Backup previo en `db/backups/`.
- **Docs**: README.md (image_analysis "17 géneros" → "keywords libres"; `ia_keywords` sin género), ROADMAP.md ("17 géneros" → "libres"; refinar sin embeddings), notas de código en image_analysis/refinar/traducir.
- **`refinar_keywords.py`** gana `--clave` para procesar `ia_keywords_transcripcion` (keywords de audios/videos salidas de `keywords_transcripciones.py`), y en `SINONIMOS` se dieron grupos propios a `camino` y `gente` (dejaron de colapsar a `ruta`/`personas`) y a `autopista` (unifica `autovía`/`highway`/`freeway`/`motorway`, ya no colapsa a `ruta` — en el dominio es "ruta más ancha, más tráfico", significado distinto). Pasada real sobre `ia_keywords_transcripcion` (140 registros, 109 actualizados) + restauración del único caso que la pasada previa había unificado (`autopista→ruta` en media 780). El diccionario quedó idempotente: re-correr no re-convierte `autopista`.
- **Nube de tags web corregida** (`deploy/api/tags.php`): ya NO tokeniza las descripciones (`ia_description`) en bruto — eso inyectaba ruido de redacción de la IA (`sugiere` ×1191, `indica` ×675, `entorno`, `general`) y recortaba frases compuestas ("entorno rural" → "entorno") al contar palabras sueltas. Ahora cuenta las **keywords completas** (`ia_keywords`), respetando "entorno rural"/"general mendoza" y filtrando con `KEYWORDS_A_IGNORAR` (mismo criterio que `puente_td.py`/`elecciones.py`). Requiere columna `keywords` en `visualizacion.db` → agregada a `scripts/exportar_visualizacion.py` (lee `ia_keywords`). Snapshot re-exportado (1522 medios, 702 con keywords); verificado con PHP: nube = `bicicleta` (290), `ciclismo` (259), `ruta` (128), sin el ruido.

---

## [Entrega 17] — 2026-08-03

### Cambiado
- **Limpieza de tandas — selección con moondream** (`batch_selector.py`): se creó `MODELO_SELECCION_DEFAULT="moondream:latest"`, usado en `seleccionar_mejor_imagen()`, `seleccionar_mejores_n()` y el CLI `--modelo`. `limpiar_tandas.py` lo importa. La selección es solo curación (no escribe en la DB), así que no necesita el modelo pesado ni el español del FLUJO IA (minicpm) — es ~15x más rápida. El FLUJO IA (image_analysis, improve_db, tag_images) **no se tocó**. Los prompts de selección (`PROMPT_EVALUAR_CALIDAD`, `_seleccionar_por_tema`) se pasaron a **inglés escuetos** (moondream responde mal en español).
- **Limpieza de proxies huérfanos** (`limpiar_tandas.py`): `limpiar_todos_los_proxies` era un import muerto. Ahora `_mover_a_excluir()` llama `limpiar_proxies(ruta)` por cada descartada (evita proxies huérfanos en `.proxies/`), y hay un flag `--limpiar-proxies` para borrar toda la carpeta `.proxies/` de la raíz al final.
- **Timezone — día y hora local (Argentina UTC-3)**:
  - `dia_semana.py`: `parsear_timestamp()` normaliza `Z` y convierte a Argentina antes de `weekday()` (antes el día se calculaba en UTC: 23:30 local lunes → "martes").
  - `loop_db.py` `_extraer_hora()`: convierte a Argentina antes de sacar la hora (el loop quedaba 3h tarde). Guardián `if dt.tzinfo is not None`.
  - `improve_db.py`: fuerza aware UTC (`_as_aware_utc()`) en la interpolación de `run_timestamps`, y normaliza `Z` en `run_keypoints`/`run_gps`.
  - `fetch_weather.py`: normaliza `Z` en las 3 apariciones de `fromisoformat` (181, 255, 403).
- **`--mostrar` → `--no-mostrar`** en `improve_db.py`: la vista en vivo de keywords/descripciones (texto EN generado) se muestra por default; `--no-mostrar` la silencia. Aplica a pasos keywords, descriptions y combinado.
- **Docs**: `AGENTS.md` sincronizado (modelo de selección, limpieza de proxies, timezone, flag invertido). `README.md` actualizado con subcomandos `import-telegram`/`mover`, scripts nuevos y documentos de diseño faltantes.

---

## [Entrega 16] — 2026-08-01

### Añadido
- **Motor de loop** (el "cerebro" de la instalación, agnóstico del renderizador):
  - `scripts/ai_media/loop_engine.py` — **núcleo puro** (sin DB ni render): matemática de arcos horarios (N horas → N−1 segmentos de duración igual), cruce de medianoche (`24 + (H[i+1]−H[i])`), posición de un medio en el loop (`t_loop = t_start + frac·duracion_seg`), descarte de medios fuera del arco, y armado de la spec JSON. Funciones: `calcular_segmentos()`, `hora_en_fraccion()`, `posicionar_hora()/posicionar_medio()`, `armar_spec()`.
  - `scripts/ai_media/loop_db.py` — integración con la DB (solo lectura): filtra `media`+`media_metadata` por municipios/colores/tags/días/clima (AND), ordena por recorrido real (`cumul_distance_m`) o elección, normaliza `timestamp_utc` mixto (`Z`/`+00:00`), genera y **consolida los chiches** por (texto, hora en punto) para no spamear el render, y vuelca la spec a JSON. CLI: `python scripts/ai_media/loop_db.py --horas 7 16 13 18 --salida spec.json`.
  - `scripts/ai_media/test_motor_loop.py` — 47 tests del núcleo (segmentos, cruce nocturno, fracción, posición, descarte, todas las horas). Fix de consola Windows: reconfigura `stdout` a UTF-8 (la flecha `→` rompe cp1252).
- **Documento** `docs/motor_loop.md` — especificación completa del motor (entrada, matemática de segmentos, posicionamiento, chiches, salida JSON, arquitectura y pendientes). Actualiza la referencia de `diseno_instalacion.md` (antes "próximo paso: motor de loop").

### Validado
- Corrida real contra `db/flujos.db` con `--horas 7 16 13 18`: 1328 medios posicionados, 63 chiches **consolidados** (antes 1574), 3 segmentos (7→16, 16→13 nocturno, 13→18).

### Cambiado
- Limpieza de imports duplicados y comentario obsoleto en `audio_tagging.py`; robustez en `batch_selector.py` (nitidez redimensiona a 800px) y `clustering.py` (parseo de keywords + guard de coseno 0).
- `ROADMAP.md`/`VISION.md`/`AGENTS.md`/`README.md` actualizados para reflejar el estado real (pipeline IA EN→ES, audio tagging, motor de loop, GPX, Telegram).

---

## [Entrega 15] — 2026-08-01

### Añadido
- **Keywords desde transcripciones** (`scripts/ai_media/keywords_transcripciones.py`): extrae keywords del **SENTIDO** de las transcripciones (`whisper_segments`) con Ollama texto (`qwen2.5:3b`). Prompt semántico: captura conceptos implícitos (no solo palabras literales), filtra muletillas y ruido, parseo de JSON/texto/numerado. Guarda `ia_keywords_transcripcion` (ES, coma-separado). Umbrales: `MIN_TEXTO_LEN=40`, `MAX_KEYWORDS=8`. Probado: media 227 (entrevista agua hidráulica) → `agua, laguna, bomba, inundación, salinidad, canal, riego, proyección`.
- **Audio tagging** (`scripts/ai_media/audio_tagging.py`): reconocimiento de sonidos ambientales en audio/video con **sherpa-onnx CED-mini** (527 clases AudioSet, int8, 100% local en CPU, sin Ollama). ffmpeg extrae WAV 16 kHz mono en memoria (sin archivos temporales), se divide en ventanas de 10 s, se agregan probs por etiqueta y se queda con top-k. Guarda `ia_keywords_sonido` (ES con glosario EN→ES) e `ia_sonido_raw` (JSON `[{name, prob}]`). Modelo en `models/audio/sherpa-onnx-ced-mini-audio-tagging-2024-04-19/`.
- **Descarga automática del modelo** (`audio_tagging.py`): si no existe `model.int8.onnx` en la ruta por defecto, el script lo **descarga solo** desde GitHub Releases (asset oficial de sherpa-onnx, ~45 MB) y lo extrae a la carpeta canónica, tolerando la estructura anidada del tar. Se puede deshabilitar con `--no-descargar` (útil en entornos sin internet). Verificado end-to-end: descarga → extracción → carga del modelo → dry-run OK.
- **TUI Mejorar DB → Hoja 3 "Audio/video IA"**: nueva hoja paginada con Audio tagging. La Hoja 2 ganó la opción 9 (Keywords desde transcripciones) y `n) Siguiente >>` para ir a la Hoja 3. Coherente con la regla de paginación (las opciones de IA/audio van a la hoja siguiente cuando la temática está llena).

### Cambiado
- **Requisitos nuevos** (`AGENTS.md`): `onnxruntime 1.27.0` + `sherpa-onnx 1.13.4` (`pip install onnxruntime sherpa-onnx`), con instrucciones de descarga/extracción del modelo CED-mini.

### Nota técnica (API sherpa-onnx 1.13)
- **NO** usar `compute(tuple, rate)` (API vieja, da `TypeError`): en 1.13 el flujo es `stream = tagging.create_stream()` → `stream.accept_waveform(rate, samples_float)` → `tagging.compute(stream)` → eventos con `.name`, `.prob`, `.index`.
- **NO** existe `stream.input_finished()` en `OfflineStream` (solo `accept_waveform`, `result`, `get_option`, etc.).
- Resultado: 270 audios/videos procesados en ~78 s (0.29 s/media); 24 sin pista de audio (ffmpeg falla → se loguea como "sin audio").

---

## [Entrega 14] — 2026-08-01

### Añadido
- **Pipeline IA EN→ES** (`improve_db.py`, `image_analysis.py`, `traducir_metadata.py`): los modelos de visión multilingües (minicpm-v4.6) responden mejor en inglés. El pipeline de keywords/descripciones es ahora **2 fases**:
  1. **Fase A (visión)**: minicpm genera EN → se guarda en `ia_keywords_en` / `ia_description_en`.
  2. **Fase B (traducción)**: qwen2.5:3b traduce a ES sobre la DB (sin re-procesar imágenes) → `ia_keywords` / `ia_description` (**ES definitivo, lo que consume la interfaz**).
  El EN queda persistido para re-traducir sin re-correr visión (`--mode update`). Al regenerar el EN SIEMPRE se invalida el ES viejo (incluido skip).
- **Paso `combinado`** en `improve_db.py`: keywords + descripción en UNA llamada de visión (JSON) + 1 de traducción (JSON). Recomendado para la pasada masiva (~10s visión + ~9s traducción por imagen).
- **`traducir_metadata.py`**: script independiente reutilizable para traducir EN→ES sobre la DB (glosario de cicloturismo, prompts anti-portugués, modo JSON combinado). CLI con `--paso`, `--mode`, `--dry-run`, `--limit`, `--modelo`.
- **`_reparar_json`** en `image_analysis.py`: reparación robusta de JSON truncado que devuelven los modelos (recorte de basura, cierre de brackets, array de keywords cerrado con `}` en vez de `]`).
- **Auto-inicio de Ollama** (`ollama_client.py`): `asegurar_ollama()`, `ollama_responde()`, `iniciar_ollama()`. Todos los scripts que requieren Ollama verifican primero si el servidor responde y, si no, lo arrancan con `ollama serve` en background (`CREATE_NO_WINDOW` en Windows, sin bloquear la terminal). Cubre `OllamaVision`, `OllamaEmbedding` (en constructor) y los scripts que usan ollama directo: `traducir_metadata.py`, `improve_db.py`, `refinar_keywords.py`, `image_analysis.py --list-models`, `analyze_video.py`, `tag_images.py`, `generate_embeddings.py`. `flujos.py _verificar_ollama()` usa la función central y avisa "✅ Ollama iniciado automáticamente".
- **Términos EN en SINONIMOS** (`refinar_keywords.py`): red de seguridad para keywords que queden en inglés tras la traducción (`tree`→árbol, `repair`→reparación, `bike`→bicicleta, etc.). Stopwords EN agregadas.

### Cambiado
- **`MODELO_VISION_DEFAULT` → `minicpm-v4.6:latest`** en `image_analysis.py`: ganador de la comparativa de modelos. Grilla fija ~340 tokens (la resolución NO infla el contexto), keywords conceptuales + descripciones largas, ~13-19s por imagen a 800px.
- **Prompts de visión en inglés y mínimos**: `PROMPT_KEYWORDS` = "Give me exactly 5 keywords for this image, comma-separated.", `PROMPT_DESCRIBIR` = "Give me a long description of this image.", `PROMPT_COMBINADO` = JSON mínimo. Validado: los prompts complejos en español degradaban la calidad de minicpm (keywords genéricas, descripciones vacías).
- **Género fotográfico pendiente**: `_validar_genero()` desactivado en el flujo de keywords (minicpm no fuerza la lista controlada). Las keywords son libres; `refinar_keywords.py` fuerza "otras" si no hay género.
- **`flujos.py` TUI Mejorar DB**: reestructurado a 2 hojas paginadas (IA y color / Inferencia y enriquecimiento) con navegación `n) Siguiente >>` / `p) << Anterior`.

### Corregido
- **Bug en fase B**: `_crear_cliente_texto()` ahora llama `asegurar_ollama()` y lanza `RuntimeError` si no hay servidor (antes fallaba con error oscuro del cliente).
- **`_reparar_json`** aplicado en `_parsear_combinado` (antes solo se intentaba `json.loads` directo).
- **Gradientes de ruta reubicados en el TUI**: se movió la opción "Calcular gradientes de ruta" de `Mantenimiento DB` (donde estaba como opción 2) a `Mejorar DB → Hoja 2` (nueva opción 8, junto a inferencia/enriquecimiento). Coherente con la filosofía de agrupación temática (gradientes son enriquecimiento, como geocode/clima/astronomía). `Mantenimiento DB` quedó renumerado (ahora 8 opciones, sin gradientes).
- **6 bugs de robustez en `image_analysis.py` / `tag_images.py` / `puente_td.py`** (revisión de código):
  - `_validar_genero` ya no pierde la primera keyword descriptiva: `keywords[0] = "otras"` (sobrescribía) → `keywords.insert(0, "otras")`.
  - `_parsear_keywords` ahora maneja JSON objeto `{"keywords": [...]}` (qwen2.5vl responde así a veces), no solo listas planas.
  - `_reparar_json` limpia trailing commas (`["playa", "mar",]` → `["playa", "mar"]`) antes de intentar parsear.
  - `_es_genero` y la 2da pasada de match reconocen flexión de género (`nocturno` ↔ `nocturna`, `urbana` ↔ `urbano`).
  - Nuevo helper `_descripcion_utilizable`: filtra JSON crudo, texto < 5 chars y restos del prompt regurgitado en los fallbacks de `analizar_imagen_completo`/`_batch` (antes el fallback guardaba la respuesta cruda como descripción).
  - `tag_images.py` escribe `ia_keywords` en **texto plano** (`, ".join`) en vez de `json.dumps`, unificando el formato con `improve_db.py`/`traducir_metadata.py`. `puente_td.py` gana `_partes_keywords()` que soporta ambos formatos (texto y JSON array) para no romper con datos históricos.
  - `tag_images.py` renombra `file_hash` → `fingerprint` en los sidecars `.tags.json` (el MD5 rápido no es el SHA-256 de la DB; el nuevo helper `_fingerprint_valido` soporta ambos nombres para compatibilidad con sidecars viejos).

---

## [Entrega 13] — 2026-08-01

### Cambiado
- **Proxy a 800px** (`scripts/ai_media/proxy.py`): `MAX_LADO_PX` pasó de 1600 a 800. Medido con `qwen2.5vl:3b`: ~4x menos tokens de visión (1085 vs 2500 por imagen), ~2.5x más rápido por imagen, y menos presión sobre el umbral de degradación acumulativa (swap). La calidad de tags/descripciones se mantiene para este modelo.
- **`num_ctx=4096` fijado en `ollama_client.py`** (`NUM_CTX_DEFAULT`): sin `num_ctx`, Ollama reserva el contexto máximo del modelo (128000) → 8.2 GB RAM, saturando la memoria y disparando el swapping en máquinas sin GPU. 4096 cubre los ~2718 tokens de una imagen 1600px + prompt, con margen para datos extra en el prompt (estilo de descripción, keywords obligatorias), usando ~2.9 GB.
- Documentación y docstrings actualizados (`AGENTS.md`, `README.md`, `__init__.py`, `image_analysis.py`, `proxy.py`) para reflejar el nuevo tamaño de proxy.

### Pendiente (próxima sesión)
- **Investigar el umbral de degradación acumulativa**: el problema parece ser la acumulación de píxeles analizados (imágenes chicas → más imágenes antes del problema; grandes → menos). Estrategia propuesta: procesar en tandas de ~20 imágenes y sacar el modelo de la memoria entre tandas (esperando que se vacíe el swap). No se descarta throttling térmico del CPU como causa raíz.
- **Probar reinicio completo de `ollama.exe`** (nunca se hizo; todas las pruebas fueron sobre la misma sesión del proceso) para ver si restaura la velocidad inicial de ~4-5s/imagen.

---

## [Entrega 12] — 2026-07-31

### Añadido
- **Refinamiento de keywords IA** (`scripts/ai_media/refinar_keywords.py`): 3 capas para limpiar y unificar `media_metadata.ia_keywords`:
  1. **Léxica**: normaliza (quita artículos `la/el/...`, singulariza plurales), filtra basura (`sa_\d+`, `dsc\d+`, restos del prompt).
  2. **Diccionario de sinónimos**: unifica variantes del dominio (`bici`→`bicicleta`, `auto`→`automóvil`, variantes de género `street`→`callejera`).
  3. **Semántica (opcional `--usar-embeddings`)**: agrupa sinónimos con `paraphrase-multilingual:latest` (coseno ≥ 0.87, configurable con `--umbral`). Se subió de 0.82 a 0.87 porque palabras truncadas generaban falsos positivos (`monta~obra` 0.844); los sinónimos reales están ≥ 0.88.
- **Opción en TUI**: `Mejorar DB > Parte 1 > 9) Refinar keywords` con submenú (léxico, +embeddings, dry-runs).
- **CLI**: `python scripts/ai_media/refinar_keywords.py [--usar-embeddings] [--umbral N] [--mode skip|update|replace] [--dry-run]`.

### Cambiado
- **Modelo de visión por defecto**: `MODELO_VISION_DEFAULT` pasó de `moondream:latest` a `qwen2.5vl:3b` (moondream regurgita el prompt en keywords). También en `ollama_client.py` (`OllamaVision`, timeout 120→180s).
- **Prompts de keywords simplificados**: `PROMPT_KEYWORDS`/`PROMPT_COMBINADO` piden "exactamente 5 keywords, género primero"; `_validar_genero()` busca el género en cualquier posición y lo promueve.
- **Navegación del menú Mejorar DB**: Parte 1 usa `n) Siguiente >>` y Parte 2 `p) << Anterior` (antes teclas 9/9); `0` sigue siendo Volver.

### Corregido
- **Modelo de sinónimos descartado**: `nextfire/paraphrase-multilingual-minilm` confundía no-sinónimos (`bici~perro` 0.771). Borrado de Ollama; se eligió `paraphrase-multilingual:latest` (`bici~perro` 0.146).

---

## [Entrega 11] — 2026-07-28

### Añadido
- **`--destino` / `-d`** en `import_telegram.py`: copia automáticamente los archivos multimedia a una carpeta canónica (`{destino}/telegram/`) durante la importación, en vez de dejarlos atados al export temporal de Telegram. Resuelve colisiones de nombre con sufijo `_1`, `_2`.
- **Recuperación de media pendiente** en re-import: al re-ejecutar con `--mode skip`, los mensajes existentes se saltan pero se ejecuta una etapa de recuperación que busca `telegram_media` con `media_id=NULL` (archivos no disponibles en corridas previas) e intenta ingerirlos. Se puede ejecutar N veces.
- **Integración TUI**: pregunta por `--destino` en Ingesta → 4. Importar chat de Telegram.
- **SIDECAR_EXTS** como constante compartida en `mover_media.py`.

### Corregido
- **Sidecars en mover_media.py**: `ejecutar_movimiento()` y `ejecutar_copia()` buscaban sidecars en el directorio de destino en vez del directorio de origen (no movían/copiaban los sidecars). Ambos corregidos.
- **Límite en `_resolver_colision`**: loop infinito potencial con `while True` reemplazado por `for n in range(1, MAX_INTENTOS+1)` con fallback timestamp.
- **`reparar_json`**: reemplazada heurística frágil (`endswith("]")`/`endswith("}")`) por conteo de brackets.
- **`import shutil`/`datetime` inline**: movidos al tope del archivo (antipatrón eliminado).
- **`detectar_message_type`**: condición siempre True simplificada a `return "text"`.

## [Entrega 10] — 2026-07-28

### Añadido
- **Importación de Telegram** (`scripts/import_telegram.py`): nuevo script que importa exports de Telegram a la base de datos. Lee `result.json`, repara JSON truncado automáticamente, registra chats en `telegram_chats`, mensajes en `telegram_messages`, y multimedia en `telegram_media`.
- **Migración v4** (`db/migrate.py`): tres nuevas tablas (`telegram_chats`, `telegram_messages`, `telegram_media`) + columna `telegram_message_id` en `media`.
- **Integración flujos.py**: TUI (Ingesta → 4. Importar chat de Telegram), CLI (`python flujos.py import-telegram` / `tg`).
- **Vinculación bidireccional**: `telegram_media.media_id` → `media.id` y `media.telegram_message_id` → `telegram_messages.id`. Los multimedia de Telegram se ingieren en `media` table opcionalmente (`--no-ingest` para solo metadata).
- **Manejo de service messages**: se marcan con `es_sistema=1` para filtrado posterior.

### Cambiado
- `db/schema.sql`: agregadas tablas `telegram_chats`, `telegram_messages`, `telegram_media` y columna `telegram_message_id` en `media`.
- `AGENTS.md`: documentación completa de las nuevas tablas, script, CLI y mapa de datos.
- `flujos.py`: AYUDA actualizada con `import-telegram` y `mover`.

---

## [Entrega 9] — 2026-07-23

### Añadido
- **Utilidades de DB centralizadas** (`db/util.py`): `abrir()` (conexión con WAL + foreign_keys), `resolver_db()` (resolución de ruta a `db/flujos.db`), `conectar()` (context manager), `ModoHelper` (lógica skip/update/replace centralizada).
- **Migraciones con callables** (`db/migrate.py`): `_MIGRACIONES` ahora acepta strings SQL y callables. Migración v3 (`_migrar_media_embeddings`) es un callable que maneja tanto DB nueva como DB con tabla existente.
- **Sys.path fix para standalone**: los 8 scripts refactorizados agregan la raíz del proyecto a `sys.path` cuando se ejecutan como script principal.

### Cambiado
- **Refactorización masiva de conexiones DB**: 8 scripts ahora importan `abrir` y `resolver_db` desde `db/util.py` en vez de tener funciones duplicadas:
  `fetch_weather.py`, `gradiente.py`, `geocode.py`, `relocate.py`, `ingest_gpx.py`, `exportar_csv.py`, `puente_td.py`, `query.py`.
- También se refactorizó `dia_semana.py` (sys.path fix agregado).
- `ingest_gpx.py`: conserva `verificar_schema()` tras `abrir()` para migración automática.
- `geocode.py`: `_conectar()` reemplazada por `abrir()` + `migrar_db()`.

### Corregido
- **Import `db.util` en scripts standalone**: scripts ejecutados como `python scripts/foo.py` fallaban con `ModuleNotFoundError: No module named 'db'` porque `sys.path[0]` apunta a `scripts/`. Agregado bloque `if __name__ == "__main__" and __package__ is None: sys.path.insert(0, ...)` en los 8 scripts + `dia_semana.py`.

---

## [Entrega 8] — 2026-07-23

### Añadido
- **Exportación DB a CSV** (`scripts/exportar_csv.py`): exporta cada tabla de la DB a un archivo CSV separado dentro de `db/exports/<timestamp>/`. Soporta `--table`, `--output`, `--dry-run`, `--list-tables`. Incluye `_resumen.txt` con conteo por tabla.
- **Opción en TUI**: `Mantenimiento DB > 7) Exportar DB a CSV` con submenú para elegir tablas (todas, media, metadata, o selección manual).
- **CLI**: `python flujos.py export-csv [--table media,config] [--output dir]`.
- **Migración v3** en `db/migrate.py`: schema canónico para `media_embeddings` (UNIQUE(media_id, modelo) en vez de media_id PK, ON DELETE CASCADE).
- **`generate_embeddings.py`** ahora llama a `verificar_schema()` para aplicar migraciones pendientes al conectar DB.
- **`db/exports/`** y **`db/backups/`** agregados a `.gitignore`.
- **CHANGELOG.md**: este archivo.

### Cambiado
- `exportar_csv.py`: `media_embeddings` ahora exporta TODAS las filas (sin la columna BLOB), en vez de solo 10 de muestra.

### Corregido
- `exportar_csv.py`: emoji `✅` reemplazado por `->` para compatibilidad con CP1252 en Windows.

---

## [Entrega 7] — 2026-07-22

### Añadido
- **Puente TouchDesigner** (`scripts/td/puente_td.py`): cerebro Python que consulta la DB y envía datos a TD vía OSC. Modos: `enviar` (loop colores→selección→imágenes), `colores`, `enviar_imgs`, `nube` (genera nube de tags desde keywords).
- **Scripts TD externalizados** en `td/`: `osc_callbacks.dat` (callbacks OSC In DAT) y `nube_generar.dat` (generación de nube de etiquetas en TD). Se vinculan desde DATs internos con `File` + `Sync to File = ON`.
- **`.opencode/` y `opencode.json`** ignorados por git (config local del agente).

### Documentación
- `AGENTS.md`: sección completa del puente TD (scripts, OSC, estructura de operadores TD esperados).

---

## [Entrega 6] — 2026-07-21

### Añadido
- **Extracción de metadatos de cámara y 360° con ExifTool en videos** (antes solo se corría en imágenes):
  - `process_file()` ahora corre ExifTool también en videos → captura `xml_devicemanufacturer`, `xml_devicemodelname`, `xmp_spherical`, `xmp_projectiontype`.
  - `detect_360()` extendida para cubrir XMP `ProjectionType` desde ExifTool.
  - `infer_author()` para videos usa marca/modelo detectados vía ExifTool.
- **Backfill** en `improve_db.py`: nuevo paso `video_metadata` que corre ExifTool sobre videos ya ingestados, guarda metadatos en `media_metadata`, actualiza `subtype = '360'`, y backfillea `author` si está vacío.

### Cambiado
- `infer_author()` para videos: ahora prioriza `xml_devicemanufacturer`/`xml_devicemodelname`.

### Corregido
- `ingest_gpx.py`: `migrar_db()` reemplazado por `verificar_schema()` centralizado de `db/migrate.py`.
- `scripts/ai_media/__init__.py`: imports faltantes agregados.
- `flujos.py`: `opcion_gradient()` ya no duplica `leer_db()`.
- `flujos.py`: submenú de mejora DB ahora recibe `db_path` desde `tui()`.
- `flujos.py`: batch IDs ahora usan `int(time.time() * 1000) % 1000000` en vez de `random.randint`.
- `improve_db.py`: `run_keypoints` modo `update` ya no borra TODOS los keypoints (solo los de medios con whisper_segments).

---

## [Entrega 5] — 2026-07-20

### Añadido
- **Ingesta de track GPS** (`scripts/ingest_gpx.py`): parsea GPX, extrae waypoints, backfill de altitud en `media.altitude` vía búsqueda binaria temporal.
- **Track real ingestado**: `tracks/Al_FaB_Tucuman.gpx` (28 waypoints, 3920 track points, altitud backfilleada en 226 medios).
- **Opción en TUI**: `Ingesta > 2) Ingerir track GPS (GPX)` con selección de modo de backfill y opciones (omitir waypoints/altitud, dry-run).
- **Schema versioning centralizado** (`db/migrate.py`): migraciones v1→v2 (tracks + waypoints). `verificar_schema()` es idempotente.
- **Tests de migraciones** (`db/test_migrate.py`): 8 tests (versión 0, idempotencia, orden, DB real).
- **Undo GPX**: `opcion_undo_ingest()` ahora lista batches (prefijo `b<id>`) y tracks (prefijo `t<id>`). Al deshacer un track se borra (CASCADE a waypoints) y revierte altitud de medios con `geolocation_source='track_gps'`.
- **Auto-backup**: `_preguntar_modo(db_path)` crea backup automático en `db/backups/` cuando se elige modo `replace`.

### Cambiado
- `flujos.py`: `_preguntar_modo(db_path)` ahora acepta `db_path` y llama a `_auto_backup()` en modo replace.
- `ingest_gpx.py`: `conectar()` llama automáticamente a `verificar_schema()`; `migrar_db()` eliminada (código muerto).
- `db/schema.sql`: tabla `media_embeddings` documentada.

---

## [Entrega 4] — 2026-07-18

### Añadido
- **Datos climáticos extendidos**: velocidad del viento (km/h), dirección del viento (grados + texto cardinal N/NE/E/etc), presión atmosférica (hPa). 226/226 registros actualizados.
- **Modo update en weather y día_semana**: ahora no limpia antes de reprocesar (consistente con el resto del pipeline).

### Corregido
- `gradiente.py`: `min(a, 1.0)` en Haversine para evitar NaN por error de punto flotante. Agregado `AND timestamp_utc IS NOT NULL` para evitar que NULLs se ordenen al inicio.
- `fetch_weather.py`: función `viento_direccion_a_texto()` para convertir grados a 16 rumbos.
- Modo update en `fetch_weather.py` y `dia_semana.py`: ya no borra datos existentes antes de reprocesar.

---

## [Entrega 3] — 2026-07-15

### Añadido
- **Filtro `--types`** en ingesta: permite seleccionar qué tipos de medio ingerir (`--types image,video`). No-sidecar XML correctamente excluido cuando se usan tipos específicos.
- **Flag `--allow-no-timestamp`**: ingerir archivos aunque no tengan timestamp.
- **Parseo de timestamp desde nombre de archivo**: formato `YYYY-MM-DD-HH-MM-SS_` (lectura de derecha a izquierda, completa con 00).
- **Menú interactivo mejorado**: opción Cancelar en `_preguntar_modo()`, navegación entre partes 1 y 2 en mejora DB, menú principal reordenado.

### Cambiado
- `ingest.py`: color extraction removido de la ingesta (delegado a `improve_db.py --step colors`).
- TUI: "Mas..." renombrado a "Siguiente >>" con navegación bidireccional entre partes.

---

## [Entrega 2] — 2026-07-10

### Añadido
- **Geocodificación inversa** (`scripts/geocode.py`): API Georef Argentina (batch), modo skip/update/replace.
- **Clima histórico** (`scripts/fetch_weather.py`): Open-Meteo ERA5-Land, agrupación por fecha+celda 0.5°, matching horario.
- **Día de la semana** (`scripts/dia_semana.py`): lunes–domingo desde timestamp_utc.
- **Gradientes de ruta** (`scripts/gradiente.py`): distancia Haversine, cambio elevación, pendiente %, acumulados.
- **Mapa interactivo** (`scripts/mapa_ruta.py`): Folium con puntos GPS, heatmap, colores por pendiente.
- **Color utils mejorado**: extracción por grilla, concentración cuadrática, centralidad + saturación relativa, distancia Redmean, anti-gray bias, variantes grey.
- **Modo skip/update/replace** en todas las operaciones DB.

### Corregido
- **GPS sign bug**: ExifTool sin `-n` devuelve `"South"`/`"West"` (texto completo), no `"S"`/`"W"`. `parse_gps_dms()` ahora usa `_es_sur_oeste()` aceptando ambos formatos. Verificado: 226 registros con GPS tienen signo negativo correcto.
- `color_utils.py`: `olivedrab`/`olive`/`darkolivegreen` movidos de "amarillo" a "verde". `fuchsia` agregado a "violeta".

---

## [Entrega 1] — 2026-07-05

### Añadido
- **Pipeline completo de ingesta** (`scripts/ingest.py`): escanea carpetas, extrae metadatos (ExifTool, ffprobe), calcula hashes (fingerprint rápido o SHA-256), inserta en DB con batch_id.
- **Post-procesamiento** (`scripts/improve_db.py`): 7 pasos (colors, keywords, descriptions, transcribe, keypoints, timestamps, gps) con skip/update/replace y resolución automática de dependencias.
- **Entry point unificado** (`flujos.py`): TUI interactivo + CLI routing con 15+ comandos.
- **Base de datos SQLite**: schema completo con ~55 columnas en `media`, `media_metadata` (key-value), `media_keypoints`, `config`, índices.
- **Columna `end_time`**: precalcula `timestamp_utc + duration_secs` para consultas por rango temporal.
- **Backup/Restore DB**: backup manual, restore desde backup, reset (backup + schema limpio).

---

## [Fundación] — 2026-06-28

### Añadido
- Estructura inicial del proyecto.
- Schema SQLite base (`db/schema.sql`).
- `AGENTS.md` como documentación exhaustiva para agentes de código.
- `VISION.md`: concepto de la instalación y la dérive.
- `README.md` y `ROADMAP.md`.
- Scripts de IA: `ollama_client.py`, `transcribe.py`, `image_analysis.py`, `proxy.py`, `tag_images.py`, `batch_selector.py`, `clustering.py`, `generate_embeddings.py`, `video_analysis.py`, `analyze_video.py`.
- Documentos de diseño: `docs/arquitectura_motor.md`, `docs/flujo_de_medios.md`, `docs/linea_de_tiempo.md`, `docs/geocodificacion_reversa.md`, `docs/limpieza_tandas_resultados.md`, `docs/semantica_color.md`, `docs/ideas_externas.md`.
