# deploy — Visualización web

> **Estado: web consolidada en `deploy/`.**
> La serie de prototipos `webN` terminó en la 3ª prueba iterativa; su
> contenido se movió a `deploy/`, que es ahora **la** ubicación definitiva de la
> visualización web (sin sufijo de versión). `deploy/` es a la vez la **fuente**
> del sitio (HTML/PHP/JS versionados en git) y el **destino** del exportador
> (medios copiados y snapshot `visualizacion.db` generados — ignorados por git).
> El spec portable del motor de loop se regenera desde el TUI a `deploy/spec.json`
> (la carpeta `pruebas/` con el prototipo `prueba_loop.html`/`loop.php` fue
> eliminada en la limpieza del workspace — los vigentes viven en `deploy/`).

## Propósito

Desde el pipeline Python (SQLite) → la **instalación** (TouchDesigner), esta capa
web es un **renderizador alternativo / prototipo** del motor de loop y del
lienzo explorable. Consume un **snapshot exportado** de la DB principal, NO la
DB real del pipeline — es un snapshot desacoplado para la web.

- Lienzo interactivo (`index.html` + `app.js`): bloques en coordenadas mundo,
  paletas por hora, nube de tags, slideshow de medios, mensajes Telegram.
- 7 endpoints PHP que sirven el snapshot (`visualizacion.db`) a la SPA.
- Spec portable del motor de loop (`deploy/spec.json`): valida `spec.json` desde
  el TUI (Visualizaciones → Exportar → Regenerar spec), sin TouchDesigner.

---

## Estructura

```
deploy/
├── index.html                 # Página "Lienzo" (SPA)
├── .htaccess                  # Seguridad Apache (protege .db/.json/.md/.py)
├── includes/
│   └── db.php                 # Conexión PDO a deploy/db/visualizacion.db
├── api/                       # 7 endpoints JSON
│   ├── medios_filtrados.php   # Medios con filtros (municipio/color/provincia/tag/tipo)
│   ├── servir_medio.php       # Archivo binario del medio (con thumbnails GD)
│   ├── tags.php               # Nube de tags desde keywords (ia_keywords), no descripciones
│   ├── recorrido.php          # Puntos + colores del recorrido
│   ├── puntos.php             # Embeddings del lienzo (⚠️ hoy vacío, ver "Gaps")
│   ├── mensajes_telegram.php  # Mensajes de Telegram de un municipio
│   └── textos.php             # Transcripciones para el bloque "Textos" (audios priorizados)
├── css/estilos.css
├── js/app.js                   # Lógica del lienzo (bloques, paletas, FLOW)
├── db/visualizacion.db         # SNAPSHOT exportado (generado, no versionar)
├── media/                      # Medios copiados por el deploy (generado)
└── spec.json                   # Spec portable del motor de loop (generada, no versionar)
```
> El exportador es **genérico** (sirve a cualquier implementación web, no solo
> esta) y vive en `scripts/exportar_visualizacion.py` (fuera de `deploy/`). El
> deploy por defecto escribe en `deploy/` en la raíz del proyecto; el modo
> `--snapshot-local` escribe `deploy/db/visualizacion.db` (dev local sin copiar
> medios).

---

## Cómo regenerar (2 pasos, tras tocar la DB principal)

El snapshot se genera desde `db/flujos.db` (FUENTE de verdad). Pasos:

1. **Re-exportar el snapshot SQLite**
   ```bash
   python scripts/exportar_visualizacion.py
   ```
   Lee `db/flujos.db` y **recrea** `visualizacion.db` por completo (medios,
   categorias, telegram_messages).
   - **Deploy genérico (default)**: escribe en `deploy/` (raíz del proyecto)
     copiando los medios a `deploy/media/...`. `--transcode` transcodifica videos
     grandes/360° a MP4/H.264 web (opt-in; sin él solo copia tal cual).
     `--transcode-360-largo 1440` (default) deja los 360 en 1440×720 con tope de
     bitrate y keyframes para seek. El export es **incremental** (skip-if-exists:
     no re-transcodifica archivos ya presentes). `--deploy-dir <carpeta>` cambia
     el destino; `--dry-run` previsualiza sin escribir.
   - **Snapshot local (dev)**: `--snapshot-local` escribe
     `deploy/db/visualizacion.db` con rutas absolutas de Windows, **sin** copiar
     medios ni transcodificar. ⚠️ Al compartir archivo con el modo deploy, el
     último export gana — para subir a hosting usar siempre el modo deploy.
     `servir_medio.php` resuelve como fallback `media/<carpeta>/<archivo>`, así
     que el hosting funciona aunque la DB guarde rutas absolutas locales (el
     fallback cubre imágenes, videos y audios).

2. **Regenerar el spec del motor de loop** (en PowerShell forzar UTF-8 por
   caracteres de caja `─`):
   ```powershell
   $env:PYTHONIOENCODING='utf-8'
   python scripts/ai_media/loop_db.py --horas 7 16 13 18 --salida deploy/spec.json
   ```
   `loop_db.py` lee `db/flujos.db` (solo lectura), calcula la hora de día de cada
   medio, genera los chiches y produce `spec.json` (portable: web de prueba y
   TouchDesigner). Los `--horas` definen los arcos; los del prototipo actual son
   `7 16 13 18`. La spec queda en `deploy/spec.json` (la carpeta `pruebas/` con
   `prueba_loop.html`/`loop.php` fue eliminada en la limpieza del workspace);
   TD genera la suya propia en `td/spec_fluir.json`.

> Al regenerar con los datos limpios (translategemma aplicado en la DB principal)
> se propagan las descripciones ES correctas y los 1522 medios completos a la web
> — verificable: 0 medias con `"!!!!"`, 0 con tailandés.

---

## Endpoints API (todos contra el snapshot, NO contra el pipeline `db/flujos.db`)

| Endpoint | Recibe (GET) | Devuelve |
|---|---|---|
| `medios_filtrados.php` | `limite` (1–20), `tipo` (csv), `municipio`, `color`, `provincia`, `tag`, `horas`, `subtipo` (todos aceptan csv; `horas` filtra por franja `[min,max]` en hora local; `subtipo` p. ej. `360`) | resultados agrupados por tipo (incluye `titulo` para textos) |
| `servir_medio.php` | `id` (obligatorio), `thumb` (opcional) | archivo binario con **HTTP Range** (`206`/`416`, `Accept-Ranges`) — necesario para `<video>` |
| `tags.php` | `limite` (10–100, default 40) | `{total, tags:[{tag, frecuencia, peso}]}` |

> **Nota — nube de tags**: `tags.php` arma la nube contando las **keywords**
> (`ia_keywords` → columna `keywords` del snapshot), **no** las descripciones.
> Originalmente tokenizaba `ia_description` en bruto, lo que inyectaba ruido de
> redacción de la IA ("sugiere", "indica", "entorno", "general") y recortaba las
> frases compuestas ("entorno rural", "general mendoza"). Al contar keywords
> completas + filtro `KEYWORDS_A_IGNORAR` (mismo criterio que
> `scripts/td/puente_td.py` / `scripts/td/elecciones.py`) las frases se respetan y el
> ruido desaparece. Requiere que el snapshot tenga la columna `keywords`
> (agregada al exportador).
>
> Desde Ago 2026 el exportador además **une** keywords por tipo de medio
> (`unir_keywords()`): audio = `ia_keywords_sonido` + `ia_keywords_transcripcion`;
> video = `ia_keywords` + transcripción + sonido; texto = `ia_keywords_texto` +
> `ia_keywords`; resto = `ia_keywords`. Así la nube incluye sonidos ambientales y
> transcripciones, no solo visión. También persiste la columna `titulo`
> (`titulo_seccion` solo para type='text').
| `recorrido.php` | — | `{total, puntos, colores}` (colores = UNION de color_1..3) |
| `puntos.php` | — | `{total, puntos}` (embeddings) — **hoy 0 puntos** |
| `mensajes_telegram.php` | `municipio` (obligatorio), `limite` (def 200), `fotos` (bool) | `{total, rango, mensajes + fotos JSON}` |
| `textos.php` | `limite` (1–20, def 8), `no` (csv de ids a excluir) | transcripciones del bloque "Textos" (prioriza audios transcritos) |

> **Filtro por horas**: `horas` (csv 0..23) filtra con franja continua `[min,max]`
> en hora LOCAL del viaje (Argentina, UTC−3). El snapshot guarda `hora` en UTC
> (columna `hora` = `HH:MM` del `timestamp_utc`) y la API convierte
> `((h−3+24) mod 24)`. Sin `horas` → sin filtro horario. Los **textos** no tienen
> `timestamp_utc` (crónicas históricas) → `hora` NULL → se excluyen cuando hay
> filtro de horas. Limitación conocida: sin cruce de medianoche (22 y 2 → franja
> 2–22 amplia). El bloque **Videos** muestra los videos regulares (sin 360) como
> lista de descripciones; los **360°** tienen su propio bloque con visor.

> `loop.php` y `prueba_loop.html` (prototipo que consumía `spec.json`) se
> eliminaron en la limpieza del workspace (2026-08-23): la SPA del deploy NO
> consume la spec del motor.

---

## Videos 360° (bloque + visor)

- **Bloque "Videos 360°"**: **reproduce el 360 en el propio bloque** (visor
  embebido, no fullscreen): Three.js local (`js/three.min.js`, vendored, UMD
  0.147) con esfera `SphereGeometry` + `VideoTexture` + `BackSide`, cámara
  adentro; **drag** para mirar, **rueda** para zoom (fov 30–110), auto-rotación
  en reposo. Barra con ◀ ▶ para cambiar de video (los filtrados por los chips,
  vía `medios_filtrados.php?tipo=video&subtipo=360`). Al re-renderear el bloque
  se detiene y libera la escena.
- **Disponibilidad**: la API solo devuelve los 360 cuyo archivo existe en
  `deploy/media/<carpeta>/` (evita links rotos; los demás quedan fuera).
- **Reproducción**: `servir_medio.php` soporta HTTP Range (206/416) → streaming y seek.
- **Contenido**: los 44 videos 360 (3840×1920, `subtype='360'` +
  `xmp_spherical=True`) se identifican por `subtipo='360'`. Requieren estar
  transcodificados en `deploy/media/<carpeta>/` (correr el exportador con
  `--transcode`).

---

## Gaps y pendientes conocidos

- **`puntos.php` devuelve 0**: `visualizacion.db.medios.embedding_x/embedding_y/cluster`
  quedan como placeholder (el exportador los prevé pero **nunca los puebla**). La
  generación de embeddings está en `generate_embeddings.py` y el modelo `nomic-embed-text`;
  no se volcaron a 2 dims en el snapshot.
- **`medio_categoria` vacío**: tabla puente creada pero sin uso.
- **El snapshot de la web** se re-exportó el 15/8/2026 con el exportador actual
  (1.341 medios, 1.316 con keywords, 1.506 tags, columna `titulo`). Para
  actualizarlo de nuevo: ver "Cómo regenerar". Fuera de sincronía, la web
  mostraría datos viejos (p. ej. descripciones pre-translategemma).
- **`descripcion`** de todos los medios en el snapshot depende de `ia_description`
  (clave `ia_description` de `media_metadata` en la principal).
- **Sin resultados en filtros**: si un filtro (p. ej. horas nocturnas) no encuentra
  medios, los bloques muestran "—". Pendiente decidir qué hacer en ese caso.
- **Videos regulares no se reproducen**: el bloque "Videos" sigue siendo lista de
  descripciones (solo el bloque **Videos 360°** reproduce). Pendiente: `<video>`
  inline para regulares (el Range ya está soportado).
- **Contenido 360°**: el visor necesita los archivos transcodificados en
  `deploy/media/` (hoy hay un subset de demo). Para el set completo correr el
  exportador con `--transcode` (ver "Cómo regenerar").

---

## Servir

Cualquier Apache con PHP (PDO SQLite) apuntando a `deploy/`. El `.htaccess`:
- `Options -Indexes`
- Deniega `.db|sqlite|sqlite3|json` (protege `visualizacion.db`)
- Deniega `.htaccess|htpasswd|md|py|sh`
El snapshot es lo único que se sirve (vía PHP), los fuentes `.py/.md` quedan
fuera de alcance por HTTP. La spec portable del motor (`deploy/spec.json`) es
una herramienta de prueba regenerable desde el TUI, no parte del deploy.

---

## Notas de diseño

- La estructura de bloques, paletas horarias, colocación aleatoria y botón "Fluir"
  están documentadas en `docs/visualizaciones.md` (sesión 28 Jul 2026).
- El spec del motor (segmentos, arcos, chiches) en `docs/motor_loop.md`.
- Esta web es un **renderizador alternativo** a TouchDesigner
  (ver `docs/motor_loop.md`); no reemplaza el pipeline.
