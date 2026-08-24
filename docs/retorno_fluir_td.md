# Retorno del "Fluir" en TouchDesigner — Guía de armado (canal 9002)

> Documento de armado **manual** del lado TouchDesigner para recibir el resultado
> del gesto "Fluir" que devuelve Python por el puerto **9002**.
> No edita archivos TD: el `.toe` se arma a mano siguiendo esta guía.
>
> **Fecha**: 2026-08-09 · **TD**: 2025.32820 (ver `docs/lecciones_elecciones_td.md`)
> · **Pipeline**: `scripts/td/puente_td.py` modo `fluir` (ya implementado) ·
> **Spec**: `docs/motor_loop.md` + `td/spec_fluir.json` (ejemplo real en disco) ·
> **Callbacks**: `td/fluir_callbacks.dat` (contrato NUEVO por tipos).

---

## 0. Resumen ejecutivo de la arquitectura propuesta

El retorno del "Fluir" llega por un **canal OSC nuevo y aislado** (9002) que
recrea el mismo patrón probado en `osc_in1` en la sesión de elecciones: un
**OSC In DAT** con su **callbacks interno adjuntado a un `.dat` externo**
(File + Sync to File).

**Rediseño clave**: el contrato OSC pasó de "una lista plana de medios"
(`/resultado`, `/medio`, `/fin`) a un **contrato por tipos** — Python agrupa
los medios por tipo (`image`, `video`, `audio`, `text`), anuncia cada tabla con
`/tabla`, y envía cada medio con su **keypoint** (= `t_loop`, posición dentro
del loop) y su **hora**. TD ya no depende del JSON para saber "cuándo" sale cada
medio: lo trae el wire.

```
Python: puente_td.py modo fluir
   │
   │  1. escribe td/spec_fluir.json   (spec completo: loop_secs, resumen, por_tipo, chiches)
   │  2. envía por OSC 9002, en orden:
   │     /flujos/fluir/resumen <total> <loop_secs> <image> <video> <audio> <text> <video360>
   │     Por tipo (image, video, video360, audio, text; solo si tiene medios):
   │       /flujos/fluir/tabla  <tipo> <cantidad>
   │       /flujos/fluir/medio  <media_id> <ruta> <keypoint> <hora> <tipo>  (×cantidad)
   │       /flujos/fluir/texto  <media_id> <titulo> <texto>   (solo type='text', justo después de su /medio)
   │           Contenido real del texto como unidad de medio (titulo_seccion + texto_completo);
   │           la ruta .md del /medio no sirve para visualizar.
   │     /flujos/fluir/chiche <hora> <texto>   (0..N)
   │     /flujos/fluir/mensaje <id> <from> <texto> <hora> <fecha> <tipo> <fotos> <municipio>  (0..N, solo con municipios)
   │     /flujos/fluir/mapa <municipio> <ruta>   (0..N, solo con municipios)
   │     /flujos/fluir/fin   <total>
   ▼
/project2/
   ├── osc_in2           (OSC In DAT, puerto 9002)   ← NUEVO, independiente
   │   └── osc_in2_callbacks  ◄── File: td/fluir_callbacks.dat (Sync to File ON)
   │           ├─ /resumen → fluir_estado     (total, loop_secs, image, video, video360, audio, text, telegram)
   │           ├─ /tabla+/medio (+/texto si type='text') → fluir_fotos / fluir_videos / fluir_videos_360 / fluir_sonidos / fluir_textos
   │           ├─ /chiche → fluir_chiches
   │           ├─ /mensaje → fluir_telegram   (chat de Telegram de los municipios elegidos)
   │           ├─ /mapa → fluir_mapas         (ruta del mapa HTML de cada municipio elegido)
   │           └─ /fin      → fluir_estado.fin=1 + cotejo y backfill de textos con td/spec_fluir.json
   ▼
   (consumo, opcional según etapa visual)
   ├── fluir_estado      (Table DAT clave-valor: total, loop_secs, por tipo, fin, recibidos/esperados)
   ├── fluir_fotos / fluir_videos / fluir_videos_360 / fluir_sonidos / fluir_textos  (Tablas por tipo; fluir_textos suma titulo/texto)
   ├── fluir_chiches     (Table DAT: hora, texto)
   ├── fluir_telegram    (Table DAT: id, from_name, texto, hora, fecha, tipo, fotos, municipio)
   ├── fluir_mapas       (Table DAT: municipio, ruta)
   ├── fluir_loop        (Timeline o Count CHOP en loop 0..loop_secs)
   └── fluir_movie       (Movie File In TOP) → reproducción del loop
```

**Regla de oro**: `osc_in2` **no se conecta ni comparte nada** con `osc_in1`
(puerto 9000, nubes `elec_*`) ni con `osc_out1` (9001, selección). Cada canal
mantiene su operador y sus tablas `elec_*` vs `fluir_*`. Eso ya estaba decidido
en `docs/lecciones_elecciones_td.md` §"Decisión clave" (canal 9002 separado) y
esta guía lo respeta al pie.

---

## 1. Contrato del canal 9002 (lo que llega exactamente)

Antes de armar ningún operador hay que fijar el wire — esto lo escribe
`scripts/td/puente_td.py` (`_procesar_rafaga`, ya implementado) y viene de
`spec["resumen"]` / `spec["por_tipo"]` / `spec["chiches"]`:

| Orden | Address | Args | Significado |
|---|---|---|---|
| 1 | `/flujos/fluir/resumen` | `i:int total`, `f:float loop_secs`, `i:image`, `i:video`, `i:audio`, `i:text`, `i:video360`, `i:telegram` | Resumen del lote: totales por tipo (de `spec["resumen"]`). `video` = videos **normales**; `video360` = videos 360° (separados); `telegram` = mensajes del chat (0 si no hay municipios elegidos) |
| 2 (por filtro activo; 0..N) | `/flujos/fluir/filtro` | `s:clave`, `s:valor` | Un filtro puesto por el usuario → fila `[clave, valor]` en `fluir_estado`. Claves: `hora_inicio`, `hora_fin`, `horas_elegidas` (siempre); `municipios`, `colores`, `tags`, `dias`, `clima` (solo si vienen) |
| 3 (por tipo, orden estable image → video → video360 → audio → text; **solo si tiene medios**; el bloque `video360` solo se envía si hay videos 360°) | `/flujos/fluir/tabla` | `s:tipo`, `i:cantidad` | Comienza una tabla para un tipo |
| 4 | `/flujos/fluir/medio` (×cantidad) | `i:media_id`, `s:ruta`, `f:keypoint`, `f:hora`, `s:tipo` | Un medio por mensaje; el tipo es el del bloque |
| 5 (solo type='text', justo después de su /medio) | `/flujos/fluir/texto` | `i:media_id`, `s:titulo`, `s:texto` | Contenido real del texto como unidad de medio: `titulo_seccion` + `texto_completo` (se escribe en las columnas `titulo`/`texto` de `fluir_textos`; la ruta `.md` del `/medio` no sirve para visualizar) |
| — (se repite tabla+medio (+texto si type='text') para cada tipo con medios) | | | |
| 6 | `/flujos/fluir/chiche` (0..N) | `f:hora`, `s:texto` | Un chiche climático/astronómico |
| 7 (solo si hay municipios elegidos; tras chiches, antes de /fin) | `/flujos/fluir/tabla telegram` + `/flujos/fluir/mensaje` (×N) | `/tabla`: `s:telegram`, `i:cantidad`; `/mensaje`: `i:id`, `s:from_name`, `s:texto`, `f:hora`, `s:fecha`, `s:tipo`, `s:fotos`, `s:municipio` | Bloque del chat de Telegram: un mensaje por fila en `fluir_telegram`. `hora` es la **local** (UTC−3) de llegada; `fotos` es JSON de media_ids de fotos; `tipo` = message_type. No cuenta en los `recibidos/esperados` del `/fin` (valida medios) |
| 8 (solo si hay municipios elegidos) | `/flujos/fluir/mapa` (× municipios) | `s:municipio`, `s:ruta` | Ruta **absoluta** al mapa HTML del municipio (generado por `scripts/mapas_municipio.py`, variante `ruta`) → fila `[municipio, ruta]` en `fluir_mapas`. TD la usa para renderizar el mapa (Web Render). No cuenta en los `recibidos/esperados` del `/fin` |
| 9 | `/flujos/fluir/fin` | `i:int total` | Marca de finalización del lote |

Puntos que conviene notar antes de programar:

- **La address real es `/flujos/fluir/...`** (constante `OSC_ADDR_FLUIR` en
  `puente_td.py`). En documentación previa figuraron variantes con typo
  (`/flojos/fluir`, `/fljujos/fluir`); **la real es `/flujos/fluir`** — se
  verifica empíricamente con `osc_probe.py 9002`. El callbacks compara el
  address absoluto contra `"/flujos/fluir/..."`.
- **`keypoint` = `t_loop`**: posición temporal del medio **dentro del loop**
  (segundos sobre `[0, loop_secs)`). QUEDA **DEFINIDO** con ese significado; ya
  no es una decisión abierta. `hora` es la hora decimal de los metadatos (0..24).
  Ambos valores viajan en el wire: el motor puede posicionar cada medio sin leer
  el JSON.
- **Tipos sin medios no se anuncian**: si `image` tiene 0 medios, el resumen
  reporta `image=0` y NO se envía `/tabla image` ni ningún `/medio` image.
  Aplica igual a `video360`: su bloque solo se envía si hay videos 360°. En el
  resumen, `video` cuenta **solo los videos normales** y `video360` los 360°;
  la suma de ambos es el total de videos del lote.
- **`/flujos/fluir/filtro` refleja la elección del usuario**: el estado del
  loop (tabla `fluir_estado`) no solo tiene totales sino también qué eligió el
  visitante (`hora_inicio`, `hora_fin`, `horas_elegidas` siempre; más
  `municipios`, `colores`, `tags`, `dias`, `clima` si vienen). El puente lo
  genera desde `spec["resumen"]["filtros"]` / `spec["resumen"]["rango_horas"]`.
- **El bloque `/mensaje` replica el criterio de la web** (`deploy/api/
  mensajes_telegram.php`): por municipio elegido toma el rango de fechas de sus
  medios (`MIN/MAX timestamp_utc`) y envía los mensajes del chat dentro de esa
  ventana, excluyendo los de sistema (`es_sistema=0`). Si el visitante no elige
  municipios, no se envía el bloque (el resumen reporta `telegram=0`). El texto
  se trunca a 250 chars y las fotos viajan como JSON de media_ids.
- **El bloque `/mapa` solo lleva la ruta** (no el HTML): el mapa de cada
  municipio lo genera `scripts/mapas_municipio.py` (variante `ruta`, archivo
  `mapas/mapa_municipio_<municipio>_ruta.html`, slug ASCII sin acentos:
  espacios→`_`, `'Río Hondo'`→`Rio_Hondo`). El puente no
  genera mapas; solo construye la ruta con la plantilla
  `PLANTILLA_MAPA_MUNICIPIO` (ajustable en `puente_td.py`) y la envía. TD la
  guarda en `fluir_mapas` y decide cómo renderizarla (p. ej. Web Render TOP).
- **El archivo `td/spec_fluir.json` se escribe ANTES del primer mensaje OSC** y
  ahora **no es la fuente única**: se lee para **cotejar** (debug de pérdida
  UDP) y para el **backfill anti-pérdida** de textos en `fin`.
- Los mensajes son **best-effort** (UDP). El `fin` trae la cantidad esperada
  para detectar paquetes perdidos comparando contra las tablas recibidas.

---

## 2. Paso 1 — Crear el receptor `osc_in2` (OSC In DAT, puerto 9002)

**Objetivo**: disponer de un punto de entrada dedicado para el retorno del
"Fluir", físicamente separado del canal de nubes (9000).

**Instrucción**:

1. En `/project2`, `Add Operator` → **OSC In DAT** (categoría `DAT`). TD lo
   nombra automáticamente **`osc_in2`** (porque `osc_in1` ya existe) y su DAT
   interno de callbacks queda **`osc_in2_callbacks`** — mantener esos nombres,
   NO renombrar.
2. En los parámetros del OSC In DAT (página de red / `OSC In`): **Port = 9002**.
   Dejar `Active` = ON.
3. Verificar con un probe externo en otra terminal:
   `python scripts/td/osc_probe.py 9002 5` → los mensajes `/flujos/fluir/...`
   deben aparecer en la tabla del `osc_in2`.
4. (Opcional, layout) Ubicar `osc_in2` a la derecha de `osc_out1`.

**Por qué**:

- Un OSC In DAT solo **muestra** los mensajes crudos en su propia tabla; el
  enrutado a las tablas vive en su callbacks interno (Lección 1 de
  `docs/lecciones_elecciones_td.md`).
- El nombre `osc_in2` es el que TD autogenera al crear el segundo OSC In DAT
  (con `osc_in1` ya existente) y su callbacks interno `osc_in2_callbacks`; es el
  nombre real del toe. Mantenerlo (sin renombrar) evita romper la referencia
  del callbacks y mantiene la convención `osc_i<N>` del proyecto.
- Puerto distinto (9002) + operador distinto (`osc_in2`) ⇒ **cero interferencia**
  con `osc_in1/9000`: aunque un mensaje erróneo llegara al socket viejo, no
  tocaría las nubes porque viven en otro operador y otro puerto.

---

## 3. Paso 2 — Crear `td/fluir_callbacks.dat` y adjuntarlo al callbacks de `osc_in2`

**Objetivo**: implementar el cerebro de recepción: distribuir los mensajes del
contrato **por tipo** en tablas separadas (`fluir_fotos`, `fluir_videos`,
`fluir_videos_360`, `fluir_sonidos`, `fluir_textos`), mantener `fluir_estado`,
`fluir_chiches`, `fluir_telegram` (chat de Telegram) y `fluir_mapas` (rutas de
mapas por municipio), y
en `fin` cotejar recibido vs esperado (+ el spec JSON).

**Instrucción**:

1. Crear el archivo `td/fluir_callbacks.dat` con el contenido de la §3.2.
2. Doble clic sobre **`osc_in2`** → se abre el DAT interno `osc_in2_callbacks`.
3. En ese DAT: **`File` = `fluir_callbacks.dat`**, **`Sync to File` = ON**.
4. Guardar el `.dat` y pulsar `Load` para forzar el sync; verificar en el
   Textport que no hay errores (o correr por
   `python -c "import ast; ast.parse(open('td/fluir_callbacks.dat', encoding='utf-8').read()); print('OK')"`).

**Por qué**: el patrón **File + Sync to File = ON** es el que ya usa el
proyecto (`osc1_callbacks` → `osc_callbacks.dat`, `elecciones_ui_callbacks` →
`elecciones_ui.dat`) y permite editar el código fuera de TD y que se refleje en
el `.toe`.

### 3.1 Convenciones a respetar dentro del `.dat`

- **Prefijo de log**: `[fluir_callbacks] ...`.
- **Idioma**: español (docstrings, variables `_snake_case`).
- **`_ROOT`**: al inicio `_ROOT = op("/project2")`.
- **Constantes**: `OSC_ADDR_FLUIR = "/flujos/fluir"` (la real del sender) y el
  mapeo `TABLAS_POR_TIPO` image/video/video360/audio/text →
  fotos/videos/videos_360/sonidos/textos.
- **Helper central**: `_tabla_para_tipo(tipo)` devuelve el nombre de tabla
  correcto (4 tablas de medios con la misma estructura; `fluir_textos` suma
  `titulo`/`texto` para el contenido real).
- **Router**: `_enrutar(address, args)` por **address absoluto** — los 9
  addresses son fijos (`/resumen`, `/filtro`, `/tabla`, `/medio`, `/texto`,
  `/chiche`, `/mensaje`, `/mapa`, `/fin`).
- **Guardas**: si falta una tabla destino, advertencia clara y `return`.

### 3.2 Esqueleto de `fluir_callbacks.dat` (listo para copiar/pegar)

El contenido real vive en `td/fluir_callbacks.dat`; este bloque es un espejo del
mismo (docstring + handlers). Ver §4 y §8 para crear las tablas que consume.

```python
"""
fluir_callbacks.dat - Callbacks del receptor de retorno del "Fluir" (OSC 9002).

El cerebro Python (`scripts/td/puente_td.py` modo 'fluir') envía por el puerto
9002 un contrato POR TIPO, en este orden exacto:

    /flujos/fluir/resumen <total> <loop_secs> <image> <video> <audio> <text> <video360>
        Resumen del lote: totales por tipo (vienen de spec['resumen']).
    /flujos/fluir/filtro <clave> <valor>   (0..N, uno por filtro puesto por
        el usuario): hora_inicio, hora_fin, horas_elegidas, municipios,
        colores, tags, dias, clima. Se guardan como filas [clave, valor] en
        fluir_estado (igual que los totales).
    Por tipo (orden estable image, video, video360, audio, text; SOLO si tiene medios;
    si un tipo tiene 0 medios NO se envían su /tabla ni /medio):
        /flujos/fluir/tabla  <tipo> <cantidad>
            Anuncia el comienzo de la tabla para ese tipo.
        /flujos/fluir/medio  <media_id> <ruta> <keypoint> <hora> <tipo>  (x cantidad)
            Un medio por mensaje; el <tipo> es el del momento (el del bloque).
        /flujos/fluir/texto <media_id> <titulo> <texto>   (solo para medios text, justo después de su /medio)
            Contenido real del texto como unidad de medio (titulo_seccion + texto_completo).
            Escribe titulo/texto en la fila del media_id en fluir_textos.
    /flujos/fluir/chiche <hora> <texto>   (0..N, chiches climáticos/astronómicos)
    /flujos/fluir/mensaje <id> <from_name> <texto> <hora> <fecha> <tipo> <fotos> <municipio>
        (0..N, solo si el visitante eligió municipio(s)) Un mensaje de Telegram del
        chat (es_sistema=0) dentro del rango de fechas de los municipios elegidos.
        Se guardan como filas en fluir_telegram.
    /flujos/fluir/mapa <municipio> <ruta>
        (0..N, solo si el visitante eligió municipio(s)) Ruta ABSOLUTA al mapa HTML
        del municipio (generado por scripts/mapas_municipio.py, variante 'ruta').
        Se guardan como filas [municipio, ruta] en fluir_mapas.
    /flujos/fluir/fin   <total>           (fin de lote)

keypoint = ubicación temporal del medio DENTRO del loop, en segundos sobre
[0, loop_secs): es el mismo valor que el spec llama `t_loop`. `hora` es la
hora decimal de los metadatos (0..24). El OSC ya trae todo esto: el archivo
td/spec_fluir.json se escribe ANTES del primer mensaje y se lee SOLO para
cotejar (debug de pérdida UDP), ya NO es fuente única de datos.

Tablas destino (Table DAT en /project2). Las CINCO tablas de medios tienen la
MISMA estructura [media_id, ruta, keypoint, hora, tipo] (fluir_textos suma
titulo/texto para el contenido real del medio, ver abajo):
  fluir_estado  [clave, valor]  total, loop_secs, image, video, video360,
                                audio, text, fin (0/1), recibidos, esperados,
                                + los filtros del usuario (hora_inicio,
                                hora_fin, horas_elegidas, municipios,
                                colores, tags, dias, clima si vienen)
  fluir_fotos   [media_id, ruta, keypoint, hora, tipo]  <- image
  fluir_videos  [media_id, ruta, keypoint, hora, tipo]  <- video
  fluir_videos_360 [media_id, ruta, keypoint, hora, tipo]  <- video360
  fluir_sonidos [media_id, ruta, keypoint, hora, tipo]  <- audio
  fluir_textos  [media_id, ruta, keypoint, hora, tipo, titulo, texto]  <- text
  fluir_chiches [hora, texto]
  fluir_telegram [id, from_name, texto, hora, fecha, tipo, fotos, municipio]  <- telegram (chat)
  fluir_mapas   [municipio, ruta]  <- mapa HTML del municipio (ruta absoluta)

Un único helper _tabla_para_tipo(tipo) resuelve el nombre de tabla correcto
para cada tipo: image -> fluir_fotos, video -> fluir_videos, video360 ->
fluir_videos_360, audio -> fluir_sonidos, text -> fluir_textos.

Este callbacks es INDEPENDIENTE del canal 9000/osc_in1 (nubes de elecciones):
solo escribe las tablas `fluir_*`. No toca `elec_*` ni `osc_out1`.
"""

_ROOT = op("/project2")

PREFIJO_LOG = "[fluir_callbacks]"

# Address base del contrato "Fluir" (coincide con OSC_ADDR_FLUIR de
# puente_td.py; verificado con osc_probe.py 9002).
OSC_ADDR_FLUIR = "/flujos/fluir"

# Nombre de tabla destino por tipo de medio (única fuente del mapeo).
TABLAS_POR_TIPO = {
    "image": "fluir_fotos",
    "video": "fluir_videos",
    "video360": "fluir_videos_360",
    "audio": "fluir_sonidos",
    "text": "fluir_textos",
}

HEADER_MEDIO = ["media_id", "ruta", "keypoint", "hora", "tipo"]

# Los medios type='text' reciben además el contenido real (titulo_seccion +
# texto_completo) por /flujos/fluir/texto, que se escribe en las columnas 5 y 6.
HEADER_TEXTO = ["media_id", "ruta", "keypoint", "hora", "tipo", "titulo", "texto"]

# Mensajes de Telegram del chat: acompañan al loop pero NO son medios del arco
# (sin keypoint; la hora es la LOCAL de llegada del mensaje, UTC-3). Los llena
# el mensaje /flujos/fluir/mensaje.
HEADER_TELEGRAM = ["id", "from_name", "texto", "hora", "fecha", "tipo", "fotos", "municipio"]

# Mapas por municipio: el puente envía la ruta ABSOLUTA al mapa HTML de cada
# municipio elegido (generado por scripts/mapas_municipio.py, variante 'ruta').
# Se guardan como filas [municipio, ruta] en fluir_mapas; no son medios del loop.
HEADER_MAPA = ["municipio", "ruta"]

# Tipo anunciado por el último /tabla (contexto para logs y debug).
_tipo_actual = None

# Total esperado reportado por /resumen (fallback si /fin no trae total).
_total_esperado = 0


# --------------------------------------------------------------------- Router
def onReceiveOSC(dat, rowIndex, message, bytes, timeStamp, address, args, peer):
    """Entry point del callbacks interno del OSC In DAT (firma fija de TD)."""
    try:
        _enrutar(address, list(args))
    except Exception as e:
        print(f"{PREFIJO_LOG} Error en {address}: {e}")


def _enrutar(address, args):
    """Despacha por address absoluto. Los siete mensajes del 'Fluir' son fijos."""
    if address == OSC_ADDR_FLUIR + "/resumen":
        _recibir_resumen(args)
    elif address == OSC_ADDR_FLUIR + "/filtro":
        _recibir_filtro(args)
    elif address == OSC_ADDR_FLUIR + "/tabla":
        _recibir_tabla(args)
    elif address == OSC_ADDR_FLUIR + "/medio":
        _recibir_medio(args)
    elif address == OSC_ADDR_FLUIR + "/texto":
        _recibir_texto(args)
    elif address == OSC_ADDR_FLUIR + "/chiche":
        _recibir_chiche(args)
    elif address == OSC_ADDR_FLUIR + "/mensaje":
        _recibir_mensaje(args)
    elif address == OSC_ADDR_FLUIR + "/mapa":
        _recibir_mapa(args)
    elif address == OSC_ADDR_FLUIR + "/fin":
        _recibir_fin(args)
    else:
        print(f"{PREFIJO_LOG} Dirección desconocida: {address} {args}")


def _tabla(nombre):
    """Devuelve una tabla del proyecto o imprime advertencia si no existe."""
    t = op("/project2").op(nombre)
    if t is None:
        print(f"{PREFIJO_LOG} Falta la tabla '{nombre}' (ver checklist)")
    return t


def _tabla_para_tipo(tipo):
    """Devuelve el nombre de la tabla que corresponde a un tipo de medio.

    image -> fluir_fotos, video -> fluir_videos, video360 -> fluir_videos_360,
    audio -> fluir_sonidos, text -> fluir_textos. Si el tipo es desconocido, imprime advertencia y
    devuelve None.
    """
    nombre = TABLAS_POR_TIPO.get(tipo)
    if nombre is None:
        print(f"{PREFIJO_LOG} Tipo desconocido: '{tipo}' (esperado: "
              f"{list(TABLAS_POR_TIPO.keys())})")
    return nombre


def _filas_datos(tabla):
    """Cantidad de filas de datos de una tabla (descuenta la fila 0 header)."""
    return max(0, tabla.numRows - 1)


def _limpiar(tabla, header):
    """Vacía una tabla y escribe su fila 0 (header) con los nombres dados."""
    tabla.clear()
    tabla.appendRow(list(header))


# ------------------------------------------------------------------- Handlers
def _recibir_resumen(args):
    """Guarda la metadata del lote en fluir_estado y reinicia las tablas.

    Escribe total, loop_secs, image, video, video360, audio, text y fin=0. También vacía
    las tablas por tipo y los chiches: cada lote nuevo arranca limpio. Los
    filtros del usuario llegan después con /filtro y se agregan abajo.
    """
    global _total_esperado, _tipo_actual
    tabla = _tabla("fluir_estado")
    if tabla is None:
        return

    total = int(args[0]) if len(args) > 0 and args[0] is not None else 0
    loop_secs = float(args[1]) if len(args) > 1 and args[1] is not None else 300.0
    n_image = int(args[2]) if len(args) > 2 and args[2] is not None else 0
    n_video = int(args[3]) if len(args) > 3 and args[3] is not None else 0
    n_audio = int(args[4]) if len(args) > 4 and args[4] is not None else 0
    n_text = int(args[5]) if len(args) > 5 and args[5] is not None else 0
    n_video360 = int(args[6]) if len(args) > 6 and args[6] is not None else 0
    n_telegram = int(args[7]) if len(args) > 7 and args[7] is not None else 0

    _total_esperado = total
    _tipo_actual = None

    _limpiar(tabla, ["clave", "valor"])
    tabla.appendRow(["total", total])
    tabla.appendRow(["loop_secs", loop_secs])
    tabla.appendRow(["image", n_image])
    tabla.appendRow(["video", n_video])
    tabla.appendRow(["video360", n_video360])
    tabla.appendRow(["audio", n_audio])
    tabla.appendRow(["text", n_text])
    tabla.appendRow(["telegram", n_telegram])
    tabla.appendRow(["fin", 0])

    # Lote nuevo: vaciar tablas por tipo y chiches con su header.
    for nombre in TABLAS_POR_TIPO.values():
        t = _tabla(nombre)
        if t is not None:
            _limpiar(t, HEADER_MEDIO)
    t_chiches = _tabla("fluir_chiches")
    if t_chiches is not None:
        _limpiar(t_chiches, ["hora", "texto"])
    t_tg = _tabla("fluir_telegram")
    if t_tg is not None:
        _limpiar(t_tg, HEADER_TELEGRAM)
    t_mapa = _tabla("fluir_mapas")
    if t_mapa is not None:
        _limpiar(t_mapa, HEADER_MAPA)

    print(f"{PREFIJO_LOG} Resumen: {total} medios (image={n_image}, "
          f"video={n_video}, video360={n_video360}, audio={n_audio}, text={n_text}), "
          f"telegram={n_telegram}, loop de {loop_secs}s")


def _recibir_filtro(args):
    """Guarda un filtro del usuario como fila [clave, valor] en fluir_estado.

    El puente manda uno por filtro activo: hora_inicio, hora_fin,
    horas_elegidas, municipios, colores, tags, dias, clima. Se escriben con el
    mismo estilo que los totales para que el estado muestre qué eligió el
    visitante.
    """
    if len(args) < 2:
        print(f"{PREFIJO_LOG} Mensaje 'filtro' incompleto: {args}")
        return

    clave = str(args[0] or "").strip()
    valor = str(args[1] or "")
    if not clave:
        print(f"{PREFIJO_LOG} 'filtro' sin clave, ignorado: {args}")
        return
    tabla = _tabla("fluir_estado")
    if tabla is None:
        return
    _escribir_estado(tabla, clave, valor)

    print(f"{PREFIJO_LOG} Filtro: {clave} = {valor}")


def _recibir_tabla(args):
    """Anuncia el comienzo de una tabla por tipo.

    Guarda el tipo actual (contexto) y reinicia la tabla del tipo por si
    quedaron filas de un lote anterior que no pasó por resumen. El conteo
    real se verifica en /fin.
    """
    global _tipo_actual
    tipo = str(args[0]) if len(args) > 0 and args[0] is not None else ""
    cantidad = int(args[1]) if len(args) > 1 and args[1] is not None else 0
    _tipo_actual = tipo

    if tipo == "telegram":
        nombre = "fluir_telegram"
        header = HEADER_TELEGRAM
    else:
        nombre = _tabla_para_tipo(tipo)
        header = HEADER_TEXTO if tipo == "text" else HEADER_MEDIO

    t = _tabla(nombre)
    if t is not None:
        _limpiar(t, header)

    print(f"{PREFIJO_LOG} Tabla {tipo}: {cantidad} medios esperados")


def _recibir_medio(args):
    """Acumula un medio en la tabla de su tipo (una fila por medio)."""
    if len(args) < 5:
        print(f"{PREFIJO_LOG} Mensaje 'medio' incompleto: {args}")
        return

    media_id = int(str(args[0])) if args[0] is not None else 0
    ruta = str(args[1] or "").replace("\\", "/")  # normaliza separadores para TD
    keypoint = float(args[2]) if args[2] is not None else 0.0
    hora = float(args[3]) if args[3] is not None else 0.0
    tipo = str(args[4]) if args[4] is not None else _tipo_actual

    nombre = _tabla_para_tipo(tipo)
    if nombre is None:
        return
    tabla = _tabla(nombre)
    if tabla is None:
        return

    # Seguridad: si la tabla quedó vacía (resumen perdido), escribir header.
    if tabla.numRows == 0:
        tabla.appendRow(list(HEADER_MEDIO))
    if tipo == "text":
        # Si /tabla text se perdió (UDP), la tabla quedó en 5 columnas
        # (la limpió /resumen con HEADER_MEDIO). Normalizar antes de escribir.
        if tabla.numCols < 7:
            _limpiar(tabla, HEADER_TEXTO)
        tabla.appendRow([media_id, ruta, keypoint, hora, tipo, "", ""])
    else:
        tabla.appendRow([media_id, ruta, keypoint, hora, tipo])


def _recibir_texto(args):
    """Completa la fila de fluir_textos con el contenido REAL del texto.

    El puente envía /flujos/fluir/texto justo después del /medio para cada
    medio type='text' (titulo_seccion + texto_completo). Busca la fila que
    ya creó el /medio (columna 0 == media_id) y escribe titulo/texto en las
    columnas 5 y 6 (HEADER_TEXTO). Si la fila no existe (paquete /medio
    perdido por UDP), crea la fila completa para no perder el contenido.
    """
    if len(args) < 3:
        print(f"{PREFIJO_LOG} Mensaje 'texto' incompleto: {args}")
        return

    media_id = int(str(args[0])) if args[0] is not None else 0
    titulo = str(args[1] or "")
    texto = str(args[2] or "")

    tabla = _tabla("fluir_textos")
    if tabla is None:
        return

    # Seguridad: si la tabla quedó vacía (resumen perdido), escribir header.
    if tabla.numRows == 0:
        tabla.appendRow(list(HEADER_TEXTO))

    # Si /tabla text se perdió (UDP), la tabla quedó en 5 columnas; normalizar.
    if tabla.numCols < 7:
        _limpiar(tabla, HEADER_TEXTO)

    # Buscar la fila del media_id (los datos arrancan en la fila 1).
    fila_encontrada = -1
    for r in range(1, tabla.numRows):
        fila = tabla.row(r)
        if fila and str(fila[0]) == str(media_id):
            fila_encontrada = r
            break

    if fila_encontrada >= 0:
        tabla[fila_encontrada, 5] = titulo
        tabla[fila_encontrada, 6] = texto
    else:
        # Paquete /medio perdido: crear la fila completa (7 celdas) para no
        # perder el contenido real del texto.
        print(f"{PREFIJO_LOG} /texto sin /medio previo para media_id={media_id}; creando fila")
        tabla.appendRow([media_id, "", 0.0, 0.0, "text", titulo, texto])

    print(f'{PREFIJO_LOG} Textos: {media_id} "{titulo}" ({len(texto)} chars)')


def _recibir_chiche(args):
    """Acumula un chiche ambiental (clima/astronomía) en fluir_chiches."""
    if len(args) < 2:
        print(f"{PREFIJO_LOG} Mensaje 'chiche' incompleto: {args}")
        return

    hora = float(args[0]) if args[0] is not None else 0.0
    texto = str(args[1] or "")
    tabla = _tabla("fluir_chiches")
    if tabla is None:
        return
    if tabla.numRows == 0:
        tabla.appendRow(["hora", "texto"])
    tabla.appendRow([hora, texto])


def _recibir_mensaje(args):
    """Acumula un mensaje de Telegram del chat en fluir_telegram.

    El puente envía /flujos/fluir/mensaje tras /tabla telegram (solo si el
    visitante eligió municipio(s)). Columnas HEADER_TELEGRAM:
    [id, from_name, texto, hora, fecha, tipo, fotos, municipio].
    """
    if len(args) < 8:
        print(f"{PREFIJO_LOG} Mensaje 'mensaje' incompleto: {args}")
        return

    id_tg = int(str(args[0])) if args[0] is not None else 0
    from_name = str(args[1] or "")
    texto = str(args[2] or "")
    hora = float(args[3]) if args[3] is not None else 0.0
    fecha = str(args[4] or "")
    tipo = str(args[5] or "")
    fotos = str(args[6] or "")
    municipio = str(args[7] or "")

    tabla = _tabla("fluir_telegram")
    if tabla is None:
        return

    # Seguridad: si la tabla quedó vacía (resumen perdido), escribir header.
    if tabla.numRows == 0:
        tabla.appendRow(list(HEADER_TELEGRAM))
    if tabla.numCols < 8:
        _limpiar(tabla, HEADER_TELEGRAM)

    tabla.appendRow([id_tg, from_name, texto, hora, fecha, tipo, fotos, municipio])

    print(f'{PREFIJO_LOG} Telegram: {id_tg} "{from_name}" ({municipio})')


def _recibir_mapa(args):
    """Guarda la ruta del mapa de un municipio en fluir_mapas.

    El puente envía /flujos/fluir/mapa justo después del bloque telegram (solo
    si el visitante eligió municipio(s)). Columnas HEADER_MAPA: [municipio, ruta].
    La ruta es ABSOLUTA al mapa HTML generado por scripts/mapas_municipio.py
    (variante 'ruta'); TD la usa para renderizar el mapa del municipio.
    """
    if len(args) < 2:
        print(f"{PREFIJO_LOG} Mensaje 'mapa' incompleto: {args}")
        return

    municipio = str(args[0] or "")
    ruta = str(args[1] or "")
    if not municipio:
        print(f"{PREFIJO_LOG} 'mapa' sin municipio, ignorado: {args}")
        return

    tabla = _tabla("fluir_mapas")
    if tabla is None:
        return

    # Seguridad: si la tabla quedó vacía (resumen perdido), escribir header.
    if tabla.numRows == 0:
        tabla.appendRow(list(HEADER_MAPA))
    if tabla.numCols < 2:
        _limpiar(tabla, HEADER_MAPA)

    tabla.appendRow([municipio, ruta])

    print(f'{PREFIJO_LOG} Mapa: {municipio} -> {ruta}')


def _recibir_fin(args):
    """Finaliza el lote: marca fin=1 y valida recibidos vs esperados.

    Compara el total esperado (args[0] o el de /resumen) contra la suma real
    de filas de las 5 tablas por tipo y guarda received/esperados en
    fluir_estado para debug de pérdida UDP. Luego coteja (opcional) contra
    td/spec_fluir.json - que ya NO es fuente única de datos. Al final
    completa los textos faltantes en fluir_textos desde el spec JSON.
    """
    global _tipo_actual
    tabla_estado = _tabla("fluir_estado")
    if tabla_estado is None:
        return

    esperado = int(args[0]) if args else _total_esperado

    recibido = 0
    for nombre in TABLAS_POR_TIPO.values():
        t = _tabla(nombre)
        if t is not None:
            recibido += _filas_datos(t)

    if esperado >= 0 and esperado != recibido:
        print(f"{PREFIJO_LOG} ¡Ojo! fin dice {esperado} medios, recibí "
              f"{recibido} (posible pérdida de paquetes OSC)")

    # Marcar fin = 1 para que el motor no arranque a mitad de carga.
    _escribir_estado(tabla_estado, "fin", 1)
    _escribir_estado(tabla_estado, "recibidos", recibido)
    _escribir_estado(tabla_estado, "esperados", esperado)
    _tipo_actual = None

    print(f"{PREFIJO_LOG} Fin de lote: {recibido} medios cargados "
          f"(esperados {esperado}).")

    # Cotejo opcional contra el spec JSON (ya NO puebla tablas).
    _cotejar_spec(recibido)
    _completar_textos_desde_spec()


def _escribir_estado(tabla, clave, valor):
    """Agrega o actualiza una fila [clave, valor] en fluir_estado."""
    for r in range(1, tabla.numRows):
        fila = tabla.row(r)
        if fila and str(fila[0]).lower() == clave:
            tabla[r, 1] = valor
            return
    tabla.appendRow([clave, valor])


# ----------------------------------------------------------------- Spec JSON
def _completar_textos_desde_spec():
    """Backfill defensivo de titulo/texto en fluir_textos desde el spec.

    Bajo ráfagas grandes el paquete /texto (separado del /medio) puede
    perderse por UDP: la fila existe pero titulo/texto quedan vacíos.
    Reutiliza la misma fuente que _cotejar_spec (spec_fluir.json): para
    cada item de por_tipo["text"] con media_id, si la fila en fluir_textos
    tiene el texto vacío copia item.titulo -> col 5 e item.desc -> col 6.
    Si la fila no existe, el /medio tampoco llegó y el cotejo de /fin ya
    reporta esa pérdida (no hacemos nada).
    """
    try:
        import json as _json
        ruta = "{}/spec_fluir.json".format(project.folder)
        with open(ruta, "r", encoding="utf-8") as f:
            spec = _json.load(f)
    except Exception as e:
        print(f"{PREFIJO_LOG} Error al leer el spec JSON para backfill: {e}")
        return

    tabla = _tabla("fluir_textos")
    if tabla is None:
        return

    if tabla.numCols < 7:
        _limpiar(tabla, HEADER_TEXTO)

    items_texto = (spec.get("por_tipo") or {}).get("text") or []
    completados = 0
    for item in items_texto:
        media_id = item.get("media_id")
        if media_id is None:
            continue
        fila_encontrada = -1
        for r in range(1, tabla.numRows):
            fila = tabla.row(r)
            if fila and str(fila[0]) == str(media_id):
                fila_encontrada = r
                break
        if fila_encontrada < 0:
            # El /medio tampoco llegó; lo reporta el cotejo de /fin.
            continue
        fila = tabla.row(fila_encontrada)
        if fila is None:
            continue
        texto_actual = fila[6] if len(fila) > 6 else ""
        if texto_actual is None or str(texto_actual).strip() == "":
            tabla[fila_encontrada, 5] = item.get("titulo") or ""
            tabla[fila_encontrada, 6] = item.get("desc") or ""
            completados += 1

    print(f"{PREFIJO_LOG} Textos completados desde spec: {completados}")


def _cotejar_spec(recibido):
    """Lee td/spec_fluir.json y coteja totales/por tipo contra lo recibido.

    Solo informa (debug de pérdida UDP); NO puebla tablas: el OSC ya trae
    todo (keypoint, hora, tipo). Devuelve el dict del spec o None.
    """
    import json as _json

    try:
        ruta = "{}/spec_fluir.json".format(project.folder)
        with open(ruta, "r", encoding="utf-8") as f:
            spec = _json.load(f)
    except Exception as e:
        print(f"{PREFIJO_LOG} Error al leer el spec JSON: {e}")
        return None

    resumen = spec.get("resumen") or {}
    total_spec = resumen.get("total", len(spec.get("medios", [])))
    if total_spec != recibido:
        print(f"{PREFIJO_LOG} Cotejo: spec dice {total_spec} medios, recibí "
              f"{recibido} (revisar pérdida UDP)")

    por_tipo = spec.get("por_tipo") or {}
    for tipo, nombre in TABLAS_POR_TIPO.items():
        if tipo == "video360":
            # El spec JSON mantiene TODOS los videos bajo por_tipo["video"],
            # con el campo es_360 por item (lo agrega el Python). Los esperados
            # de video360 salen de ahí, no de una clave por_tipo["video360"].
            esperados_tipo = sum(1 for m in (por_tipo.get("video") or []) if m.get("es_360"))
        else:
            esperados_tipo = len(por_tipo.get(tipo, []) or [])
        t = _tabla(nombre)
        recibidos_tipo = _filas_datos(t) if t is not None else 0
        if esperados_tipo != recibidos_tipo:
            print(f"{PREFIJO_LOG} Cotejo {tipo}: spec dice {esperados_tipo}, "
                  f"recibí {recibidos_tipo}")

    print(f"{PREFIJO_LOG} Spec leído: {total_spec} medios, "
          f"{len(spec.get('chiches', []))} chiches")
    return spec
```

> **Nota**: este bloque es un espejo del archivo `td/fluir_callbacks.dat` del
> repo (fuente de verdad). El `.dat` en disco es el que se adjunta con
> File+Sync (tarea A); la guía lo replica para que el armado manual tenga el
> código a mano sin abrir el repo.

---

## 4. Paso 3 — Crear las tablas de datos de consumo

**Objetivo**: disponer de estructuras planas que el motor visual lea sin
parsear código dentro de los operadores de renderizado.

**Instrucción** (Table DAT, en la raíz de `/project2`):

| Op | Tipo | Columnas / uso |
|---|---|---|
| `fluir_estado` | Table DAT | pares clave-valor → filas `total`, `loop_secs`, `image`, `video`, `video360`, `audio`, `text`, `fin` (0/1), `recibidos`, `esperados` + **filtros del usuario** (`hora_inicio`, `hora_fin`, `horas_elegidas`, `municipios`, `colores`, `tags`, `dias`, `clima` si vienen) |
| `fluir_fotos` | Table DAT | `media_id`, `ruta`, `keypoint`, `hora`, `tipo` — medios `image` desde OSC |
| `fluir_videos` | Table DAT | `media_id`, `ruta`, `keypoint`, `hora`, `tipo` — medios `video` (normales) desde OSC |
| `fluir_videos_360` | Table DAT | `media_id`, `ruta`, `keypoint`, `hora`, `tipo` — medios `video360` (videos 360°) desde OSC |
| `fluir_sonidos` | Table DAT | `media_id`, `ruta`, `keypoint`, `hora`, `tipo` — medios `audio` desde OSC |
| `fluir_textos` | Table DAT | `media_id`, `ruta`, `keypoint`, `hora`, `tipo`, `titulo`, `texto` — medios `text` desde OSC; **única tabla de medios con 7 columnas** (`titulo` + `texto` = contenido real del texto) |
| `fluir_chiches` | Table DAT | `hora`, `texto` — eventos ambientales desde OSC |
| `fluir_telegram` | Table DAT | `id`, `from_name`, `texto`, `hora`, `fecha`, `tipo`, `fotos`, `municipio` — chat de Telegram de los municipios elegidos (desde `/mensaje`; `hora` = local UTC−3, `fotos` = JSON de media_ids) |
| `fluir_mapas` | Table DAT | `municipio`, `ruta` — ruta **absoluta** al mapa HTML de cada municipio elegido (desde `/mapa`; el HTML lo genera `scripts/mapas_municipio.py`, variante `ruta`) |

**Por qué**: 4 de las 5 tablas por tipo comparten estructura (`[media_id,
ruta, keypoint, hora, tipo]`) y el callbacks las llena con un único helper
`_tabla_para_tipo()`; **`fluir_textos` es la excepción**: suma `titulo`/`texto`
porque lleva el contenido real del texto (la ruta `.md` no es visualizable
directamente). `fluir_telegram` y `fluir_mapas` son canales aparte (no son
medios del loop): el primero lleva el chat (su `hora` es la de llegada, no un
keypoint) y el segundo las rutas de los mapas por municipio. **Ninguno cuenta**
en los `recibidos/esperados` que valida el `/fin`. Separar por tipo deja que el
motor lea solo la clase que le toca reproducir (fotos → TOP + Text, videos →
Movie, sonidos → Audio/Text, textos → Text), sin recorrer una lista mixta.

---

## 5. Alternativa (rápida): consumir solo el OSC, sin esperar el JSON

**Objetivo**: primera iteración funcional: con el wire solo (sin parsear
`spec_fluir.json`) ya se puede posicionar todo.

**Instrucción**: usar `fluir_fotos` / `fluir_videos` / `fluir_videos_360` /
`fluir_sonidos` / `fluir_textos` llenas por `/medio` (cada fila trae
`keypoint` y `hora`; para `fluir_textos` el contenido real llega por
`/flujos/fluir/texto`) +
`fin` de `fluir_estado`.

- **Ventaja**: no necesita el archivo; funciona aunque el JSON tarde en salir;
  es lo más rápido de armar y ya respeta el "cuándo" de cada medio (`keypoint`).
- **Limitación**: para reproducir un `image` sin romper el instante hace falta
  decidir una duración efectiva (ver Decisiones abiertas nº 3).

**Por qué**: con el contrato por tipos, el wire es autosuficiente para
posicionar; el JSON queda como fuente de enriquecimiento (color, tags, desc)
para etapas visuales posteriores.

---

## 6. Opciones de reproducción coherentes con el motor de loop existente

Según `docs/motor_loop.md` (§3 y §6), el spec define un reloj de **loop de
`loop_secs`** con cada medio ubicado en un `keypoint` (= `t_loop`) dentro de
`[0, loop_secs)`. La reproducción usa:

### a) Reloj de loop

- **Op que se necesita**: `fluir_loop` — un **Timeline CHOP** (o `Clock CHOP`)
  en loop de `0..loop_secs`. Tomar `loop_secs` de `fluir_estado` (fila
  `loop_secs`), nunca asumir 300 fijo.
- **Por qué**: el motor Python ya calculó dónde cae cada medio; si TD ignora el
  `keypoint` y reproduce linealmente, la instalación pierde la idea de
  "recorrer Buenos Aires → Tucumán en el tiempo de los metadatos".

### b) Cursor de medio activo

- Opción: `fluir_engine` — un **Script DAT / Execute DAT** que cada frame
  calcula `t = fluir_loop[0] % loop_secs` y, con **las tablas por tipo**
  (`fluir_fotos`, `fluir_videos`, `fluir_videos_360`, `fluir_sonidos`, `fluir_textos`), decide qué
  medio está activo:
  - tipo `image`: mostrar si `t ∈ [keypoint, keypoint + duracion_efectiva]`
    (la spec da `duracion = 0` en imágenes → **decisión de diseño**: duración
    mínima de tarjeta, p. ej. `max(3s, porción del segmento)` — ver Decisiones
    abiertas nº 3).
  - tipo `video`: reproducir con su duración real (si entra en la porción del
    segmento) o recortar un fragmento (§3.3 del motor).
- Conexión concreta: `fluir_movie` (Movie File In TOP) recibe el archivo desde
  el Script (`movie.par.file = ruta; movie.par.play = 1`).

**Por qué**: `keypoint` ya está **definido** (= `t_loop`): no hace falta
parsear el spec para posicionar; la tabla del tipo correcto da la ruta y el
instante.

### c) Chiches (eventos ambientales)

- Con `fluir_chiches` (`hora` → `texto`), el `fluir_engine` detecta el cruce y
  dispara un pulso (p. ej. un `LumaBlur`/`Level` destello o un Text overlay).
  Coincide con la definición de "eventos ambientales" del motor (§5).

### d) No pisar nombres legacy

- Nada de esto ocupa los nombres `movie1`, `tabla_colores`, `color_actual`,
  `info_imagen`, `seleccion_actual` (ver §7). El reproductor del Fluir debe
  llamarse **`fluir_movie`**, NO `movie1`.

Esta sección es de alto nivel adrede: fija el contrato de datos (dónde vive
cada dato que el motor necesita); el armado fino del render queda para la etapa
de pipeline visual, pero los nombres y tablas quedan disponibles.

---

## 7. Integración sin romper (reglas duras)

1. **`osc_in2` es 100% independiente de `osc_in1`/9000**:
   - No comparte callbacks DAT (`osc_in2_callbacks` vs `osc_in1_callbacks` están
     separados), ni tablas (`fluir_*` vs `elec_*`), ni sockets.
   - No se mueve ni se renombra nada de `osc_in1`/`osc_out1`/`elec_*`/`boton_*`.
2. **No recrear el pipeline legacy bajo nombres existentes**: no crear
   `movie1`, `tabla_colores`, `color_actual`, `info_imagen`, `seleccion_actual`
   para este canal. El reproductor del Fluir se llama `fluir_movie`.
3. **El `panelexec1` de los botones de elección no se toca** (sigue mandando la
   ráfaga del "Fluir" por 9001). El retorno 9002 es una adición, no un cambio.

---

## 8. Checklist de armado (ops a crear a mano, con nombres concretos)

- [ ] **`osc_in2`** — OSC In DAT en `/project2`, página `Network` → `Port = 9002`.
- [ ] **`osc_in2_callbacks`** — DAT interno de `osc_in2`; **File** =
      `td/fluir_callbacks.dat`, **Sync to File = ON**.
- [ ] **`fluir_callbacks.dat`** — archivo en `td/`, con el contenido de §3.2 (nuevo por tipos).
- [ ] **`fluir_estado`** — Table DAT (clave-valor: total, loop_secs, image, video,
      video360, audio, text, fin, recibidos, esperados + filtros del usuario:
      hora_inicio, hora_fin, horas_elegidas, municipios, colores, tags, dias,
      clima si vienen).
- [ ] **`fluir_fotos`** — Table DAT (media_id, ruta, keypoint, hora, tipo).
- [ ] **`fluir_videos`** — Table DAT (media_id, ruta, keypoint, hora, tipo).
- [ ] **`fluir_videos_360`** — Table DAT (media_id, ruta, keypoint, hora, tipo);
      videos 360° separados (se crea con `crear_tablas_fluir.dat`, igual que el resto).
- [ ] **`fluir_sonidos`** — Table DAT (media_id, ruta, keypoint, hora, tipo).
- [ ] **`fluir_textos`** — Table DAT (media_id, ruta, keypoint, hora, tipo,
      **titulo, texto**) — única tabla de medios con 7 columnas (contenido
      real del texto); si ya existe con 5 columnas, re-correr
      `crear_tablas_fluir.dat` la normaliza a 7.
- [ ] **`fluir_chiches`** — Table DAT (hora, texto).
- [ ] **`fluir_telegram`** — Table DAT (id, from_name, texto, hora, fecha, tipo,
      fotos, municipio) — chat de Telegram de los municipios elegidos; se llena
      con `/mensaje` (se crea con `crear_tablas_fluir.dat`, igual que el resto).
- [ ] **`fluir_mapas`** — Table DAT (municipio, ruta) — ruta al mapa HTML de cada
      municipio elegido; se llena con `/mapa` (se crea con `crear_tablas_fluir.dat`,
      igual que el resto).
- [ ] (Opcional, etapa visual) **`fluir_loop`** Timeline CHOP; **`fluir_movie`**
      Movie File In TOP; **`fluir_engine`** Script DAT con el planificador (scheduler).
- [ ] Verificación de punta a punta (3 terminales):
      `python scripts/td/puente_td.py fluir --una-vez`, un terminal con
      `python scripts/td/osc_probe.py 9002 5`, y en TD chequear que
      `fluir_fotos/fluir_videos/fluir_videos_360/fluir_sonidos/fluir_textos`
      se llenan, `fluir_telegram` recibe el chat y `fluir_mapas` las rutas si se
      eligió un municipio, y `fluir_estado.fin = 1` al terminar (y `recibidos`
      == `esperados`).

---

## 9. Decisiones (algunas cerradas con el rediseño por tipos)

| # | Decisión | Estado | Motivo |
|---|---|---|---|
| 1 | Nombre del receptor del retorno | **Resuelta** — `osc_in2` + `osc_in2_callbacks` | Convención real `osc_i<N>`; no renombrar. |
| 2 | Typo del address (`/flojos`, `/fljujos`) | **Resuelta** | la real es `/flujos/fluir` (ver `osc_probe.py 9002`, `puente_td.py::OSC_ADDR_FLUIR`) |
| 3 | Duración efectiva de imágenes (`duracion=0`) | **Abierta** (recomendación: `max(3s, porción del segmento)`) | `keypoint` ya define el INICIO, pero no la permanencia en imagen |
| 4 | ¿`fin` dispara el render o solo coteja? | **Resuelta** | `fin` marca `fin=1`, guarda `recibidos/esperados` y coteja el JSON (ya no es fuente única) |
| 5 | Debug de pérdida UDP | **Resuelta** | `fluir_estado` guarda `recibidos/esperados` |
| 6 | `loop_secs` configurable | **Resuelta** | leer de `fluir_estado`, nunca fijo 300 |
| 7 | Tabla de audio: `fluir_sonidos` vs `fluir_audios` | **Resuelta** → `fluir_sonidos` | consistente con español del proyecto (un docstring de `puente_td.py` dice `audios`, pero la decisión es `sonidos`) |
| 8 | Significado de `keypoint` | **Resuelta** | `keypoint` = `t_loop`: posición en segundos dentro del loop (0..loop_secs) — se usa tal cual del wire |
| 9 | El estado refleja el filtro del usuario | **Resuelta** | `spec["resumen"]["filtros"]` + mensaje `/flujos/fluir/filtro <clave> <valor>` → filas en `fluir_estado` (hora_inicio, hora_fin, horas_elegidas, municipios, colores, tags, dias, clima). Así TD muestra qué eligió el visitante, no solo totales |
| 10 | Videos 360° separados del resto | **Resuelta** | tabla propia `fluir_videos_360` (misma estructura `[media_id, ruta, keypoint, hora, tipo]`); el marker es `media.subtype='360'`, escrito por `improve_db --step video_metadata`; el puente los separa en el bloque `video360` (orden image → video → video360 → audio → text) y el resumen lleva **8 args** con `video360` en 7ª posición y `telegram` al final (`video` = solo normales). El spec JSON mantiene TODOS los videos bajo `por_tipo["video"]` con el campo `es_360` por ítem (lo agrega `loop_db.py`); el cotejo TD espera los video360 desde ahí, no de una clave `por_tipo["video360"]` |
| 11 | Contenido real de los textos en TD | **Resuelta** | mensaje separado `/flujos/fluir/texto <media_id> <titulo> <texto>` justo después de `/medio`, solo para type='text'; lleva el contenido real como unidad de medio (`titulo_seccion` + `texto_completo`, truncado de seguridad a 8000 chars); sin ubicación/tags en las tablas — lo resuelve el servidor de DB; guard `numCols` en `_recibir_medio`/`_recibir_texto` ante pérdida de `/tabla text` (UDP) |
| 12 | Backfill anti-pérdida de textos + fix de API Table DAT | **Resuelta** | al `/fin`, `_completar_textos_desde_spec()` completa `titulo`/`texto` en `fluir_textos` desde `td/spec_fluir.json` cuando el mensaje `/flujos/fluir/texto` se pierde por UDP (ráfagas grandes); celdas de Table DAT se escriben con `tabla[fila, col] = valor` (`setCell` no existe en `td.tableDAT`) |
| 13 | Chat de Telegram en el Fluir | **Resuelta** | tabla propia `fluir_telegram` (no es medio del loop: sin keypoint; `hora` = local UTC−3 de llegada). Se envía dentro de `_procesar_rafaga` SOLO si hay municipios elegidos (bloque `/tabla telegram` + `/mensaje` ×N), replicando el criterio web (rango de fechas de los medios del municipio, `es_sistema=0`, texto truncado a 250 chars, `fotos` como JSON de media_ids). No cuenta en los `recibidos/esperados` del `/fin`; el resumen lo reporta como `telegram=N` |
| 14 | Mapas por municipio en el Fluir (fase 1: solo ruta) | **Resuelta** | tabla propia `fluir_mapas` (no es medio del loop). El puente envía `/flujos/fluir/mapa <municipio> <ruta>` × municipios elegidos, con la ruta **absoluta** al mapa HTML generado por `scripts/mapas_municipio.py` (el nombre de archivo es **exactamente** el de `_nombre_archivo` para la variante configurada — `VARIANTE_MAPA_MUNICIPIO`, default `ruta`: `mapas/mapa_municipio_<municipio>_ruta.html`, slug ASCII sin acentos: `'Río Hondo'`→`Rio_Hondo`; plantilla `PLANTILLA_MAPA_MUNICIPIO`, ajustable; la ruta completa = raíz del proyecto + ese archivo). El HTML **no viaja por OSC** (evita el límite de tamaño del mensaje); TD decide cómo renderizarlo (p. ej. Web Render TOP). No cuenta en `recibidos/esperados` del `/fin`. **Fase 2 pendiente**: capas extra al mapa (marcadores por tags/colores), Web Render Source=DAT editable, sync con el loop |

---

## 10. Referencias cruzadas

- `docs/lecciones_elecciones_td.md` — Lección 1 (File+Sync), Lección 3/4/5
  (clases globales, Panel Execute, Textport vs Script DAT).
- `docs/motor_loop.md` — §3 (segmentos/posicionamiento), §5 (chiches),
  §6 (salida JSON del spec).
- `docs/arquitectura_motor.md` — Enfoque B (híbrido: TD = músculo audiovisual).
- `scripts/td/puente_td.py` — `_procesar_rafaga`: orden por tipo + `OSC_ADDR_FLUIR`.
- `td/osc_callbacks.dat` — modelo de routing (`_ROOT`, `onReceiveOSC`,
  `_enrutar`, prefijo de logs).
- `td/opfind1.tsv` — mapa real de ops del toe: no existen (aún) `osc_in2` ni
  tablas `fluir_*`; se crean con esta guía.