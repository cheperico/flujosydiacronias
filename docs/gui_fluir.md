# GUI "Fluir" en Python (`gui_fluir.py`) — Selector de medios y envío por 9002

> Interfaz gráfica Python (tkinter) que **reemplaza la UI de chips de TouchDesigner**
> para el gesto "Fluir": muestra la lista completa de items seleccionables cargada
> desde la DB, permite marcar los que quiere el visitante y al enviar genera el loop
> con `loop_db` y emite por OSC **9002** el mismo contrato que `puente_td.py modo fluir`.
>
> **Fecha**: 2026-08-23 · **Script**: `gui_fluir.py` (raíz del proyecto) ·
> **Dependencia clave**: `scripts/td/puente_td.py` → `_procesar_rafaga` (delegación) ·
> **Contrato 9002**: ver `docs/retorno_fluir_td.md` · **Motor del loop**: `docs/motor_loop.md`

---

## 0. Resumen ejecutivo

El flujo original con TD ocupaba tres puertos OSC:

| Puerto | Sentido | Rol original |
|---|---|---|
| 9000 | TD ← Python | Nubes de elecciones (lista completa seleccionable) |
| 9001 | TD → Python | Ráfaga `/flujos/seleccion/<grupo> <valor>` del botón "Fluir" |
| 9002 | TD ← Python | Resultado del loop (tablas de medios por tipo) |

Con la GUI Python, los dos primeros roles se absorben en la interfaz:

```
┌─────────────────────────── gui_fluir.py ───────────────────────────┐
│ 1. Carga la lista completa desde db/flujos.db                     │
│    (horas 24 · municipios alfabético · colores alfabético ·       │
│     tags = las 200 MÁS USADAS, ordenadas alfabéticamente)         │
│ 2. El usuario marca checkboxes (con filtro de búsqueda opcional)  │
│ 3. "Consultar y enviar por 9002" →                                │
│      _obtener_seleccionados()                                     │
│        └─► puente_td._procesar_rafaga(db_path, selecciones, ...)  │
│              ├─ loop_db.generar_loop(...)                         │
│              ├─ escribe td/spec_fluir.json                        │
│              └─ emite el contrato completo por 9002               │
│ 4. Log con resumen post-envío (totales por tipo + chiches)        │
└────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼  solo puerto 9002
                    TouchDesigner (osc_in2 + tablas fluir_*)
```

**TD ya no necesita**: la nube de elecciones (9000) ni el listener del "Fluir"
(9001). Solo escucha el **9002** y puebla sus tablas como documenta
`docs/retorno_fluir_td.md`.

---

## 1. Arquitectura interna

### 1.1 Rutas e imports (bootstrap)

`gui_fluir.py` vive en la **raíz** del proyecto. El bootstrap agrega al
`sys.path` lo necesario y delega:

```python
RAIZ_PROYECTO     = dirname(abspath(__file__))          # raíz (1 nivel, NO 3)
SCRIPTS_TD_DIR    = RAIZ/scripts/td                      # para importar puente_td
SCRIPTS_AI_MEDIA_DIR = RAIZ/scripts/ai_media             # loop_db lo agrega puente_td

from db.util   import resolver_db
from puente_td import _procesar_rafaga                   # delegación del envío
```

> ⚠️ **Lección crítica** (bug histórico): la versión inicial subía 3 niveles
> (cálculo copiado de `puente_td.py`, que vive en `scripts/td/`). Eso apuntaba a
> `C:\Users\Federico\Documents\...` fuera del proyecto, `import loop_db` fallaba
> en silencio dentro del hilo y **nunca salía nada por 9002**. La regla: cada
> script calcula la raíz según SU ubicación real.

### 1.2 Componentes

| Componente | Responsabilidad |
|---|---|
| `obtener_datos_desde_db(db_path)` | Carga los 4 grupos desde la BD (ver §2) |
| `_clave_alfabetica(valor)` / `_ordenar_alfabetico(items)` | Orden alfabético case-insensitive sin acentos (mismo criterio que `elecciones.py`, vía `unicodedata` NFD) |
| `FluirClient` | Cliente OSC de salida (`SimpleUDPClient` → `127.0.0.1:9002`) + `procesar_y_enviar()` que delega en `puente_td._procesar_rafaga` |
| `GUIFluirApp` | Toda la UI: notebook de 4 pestañas, scroll, contadores, filtros, envío en hilo |
| `_truncar(texto, max_len=26)` | Recorta textos largos con `…` para no desalinear columnas |

### 1.3 Estructuras de estado de la GUI

```python
items_por_grupo[grupo]     # [(valor_puro, texto_mostrar)] — texto lleva "(freq)"
vars_por_grupo[grupo]      # {valor_puro: tk.BooleanVar}   — la var persiste entre filtros
widgets_por_grupo[grupo]   # {valor_puro: ttk.Checkbutton} — reconstruidos al filtrar
frames_interior[grupo]     # frame interior del canvas scrollable
labels_contador[grupo]     # label "n/total seleccionados"
entries_filtro[grupo]      # entry 🔍 (solo municipios y tags)
canvas_por_grupo[grupo]    # canvas con scrollbar
```

La selección viaja siempre como **valor puro** (sin frecuencia ni truncado): es
lo que `_procesar_rafaga` espera en `selecciones = {grupo: [valores]}`.

---

## 2. Carga de datos desde la BD

Todo se consulta en tiempo de arranque desde `db/flujos.db`. Los grupos
municipios/colores/tags traen **frecuencia** para mostrarla discretamente.

### 2.1 Horas — 24 fijas

```python
[f"{h:02d}:00" for h in range(24)]
```
Orden numérico 00:00→23:00 (que además es alfabético por el formato HH).

### 2.2 Municipios y colores — todos, alfabéticos

```sql
SELECT municipio, COUNT(*) FROM media
WHERE municipio IS NOT NULL AND municipio != '' GROUP BY municipio;
-- ídem color_1_name_basic (WHERE ... IS NOT NULL)
```
Ordenados con `_clave_alfabetica` (sin acentos: "Río Hondo" ordena como
"rio hondo"). Con la DB actual: **74 municipios**, **10 colores**.

### 2.3 Tags — las 200 más usadas, alfabéticas ⚠️

Lógica idéntica a `elecciones.py::_consulta_tags` (parseo plano o JSON array,
filtros `KEYWORDS_A_IGNORAR`/`KEYWORDS_SENSIBLES`, descarte ≤2 chars,
lowercase), pero con un corte distinto pedido explícitamente:

```python
top_frecuencia = sorted(contador.items(), key=lambda x: -x[1])[:200]  # top POR FRECUENCIA
tags = sorted(top_frecuencia, key=lambda vf: _clave_alfabetica(vf[0]))  # LUEGO alfabético
```

> 🐛 **Bug corregido (2026-08-23)**: antes se hacía
> `sorted(contador.keys())[:200]` — tomaba las **200 primeras alfabéticas**
> del universo total (~1143 tags únicas), así que la lista "se quedaba en la
> C". Ahora son las **200 con más apariciones** y *dentro* de ese conjunto el
> orden es alfabético.
>
> Nota: `elecciones.py` usa otro corte (cuartil superior capado a `MAX_TAGS`)
> porque ese mensaje viaja entero por OSC; acá el límite de 200 es una decisión
> de UX de la GUI y no afecta al wire (los mensajes 9002 dependen de los medios
> elegidos, no de la lista mostrada).

---

## 3. La interfaz

Ventana 900×680 (min 720×520), layout vertical compacto que maximiza el área
de listas:

```
┌────────────────────────────────────────────────────────────────────┐
│ Panel Flujos - Selección OSC → TD (9002)     [N seleccionados]     │  ← barra título + total global
├─ Parámetros ───────────────────────────────────────────────────────┤
│ Loop secs [300.0] ☑ Enviar medios (tabla/medio/chiche)            │  ← barra compacta
│ [Consultar y enviar por 9002] [Limpiar selección]                 │
├─ Notebook ─────────────────────────────────────────────────────────┤
│ ┌──────┬────────────┬─────────┬──────────────────────┐             │
│ │Horas │Municipios  │Colores  │Tags (200 más usadas) │             │
│ └──────┴────────────┴─────────┴──────────────────────┘             │
│  header: 3/74 seleccionados          [🔍____] [Todo][Nada]         │  ← por pestaña
│  ┌──────────────────────────────────────────────────┐ ▲           │
│  │ ☑ Agua Dulce y La Soledad (12)  ☐ Ana Zumrán (4) │ █           │  ← canvas scrollable
│  │ ☐ Atamisqui (7) …                                │ █           │     (rueda del mouse)
│  └──────────────────────────────────────────────────┘ ▼           │
├─ Log ──────────────────────────────────────────────────────────────┤
│ 🔍 Selección: {'horas': 2, 'tags': 1}                             │
│ ✅ Enviado por 9002: 737 medios · 551 img · 62 vid · …            │
└────────────────────────────────────────────────────────────────────┘
```

### 3.1 Pestañas y columnas

Cada pestaña se construye genéricamente con `_construir_pestana(grupo, titulo,
con_filtro)`. Columnas según grupo (`_columnas_para`):

| Grupo | Items | Columnas |
|---|---|---|
| horas | 24 | 12 (grilla fija 2×12) |
| municipios | ~74 | `min(10, max(3, √n))` ≈ 8–9 |
| colores | ~10 | `min(6, max(3, √n))` ≈ 4 |
| tags | 200 | `min(10, max(4, n//20))` = 10 |

Los checkbuttons usan `ttk` (estilo uniforme del tema) y el texto se trunca a
26 chars con `…`; la frecuencia se muestra como sufijo `(N)` salvo en horas.

### 3.2 Scroll interno

Patrón Canvas estándar de tkinter:

- `canvas.create_window((0,0), window=interior)` con anchor NW.
- `<Configure>` del interior → `scrollregion = bbox("all")`.
- `<Configure>` del canvas → ancho del window = ancho del canvas (evita corte horizontal).
- Scrollbar vertical + bind de `<MouseWheel>` en canvas e interior
  (`yview_scroll(-delta/120)`).
- Al filtrar se reconstruye el grid y el scrollregion se recalcula solo.

### 3.3 Filtro de búsqueda (municipios y tags)

Entry con placeholder `🔍`. En `<KeyRelease>` se llama `_repoblar_pestana`,
que **destruye y re-crea** solo los checkbuttons visibles (match por
`_clave_alfabetica`, o sea insensible a acentos/mayúsculas):

```python
visibles = [(v, t) for v, t in items if filtro in _clave_alfabetica(v)]
```

Recrear (en vez de `grid_remove`) evita huecos en el grid: las filas/columnas
se recalculan para los visibles. Las `BooleanVar` viven en `vars_por_grupo`
(fuera de los widgets), así que **la selección sobrevive al filtrado**.

### 3.4 Contadores en vivo

Cada `BooleanVar` registra `trace_add("write", ...)` → `_actualizar_contadores()`,
que refresca:

- el label por pestaña (`3/74 seleccionados`),
- el total global de la barra de título (`5 seleccionados`).

Botones **Todo/Nada** por pestaña marcan/desmarcan todo el grupo (los traces
actualizan los contadores automáticamente).

### 3.5 Envío en hilo + resumen post-envío

`_on_enviar` NO bloquea la UI:

1. Valida selección no vacía y `loop_secs` numérico en `(0, 86400]`
   (si es inválido: aviso en log + default 300.0 restaurado en el campo).
2. Deshabilita el botón y lanza `threading.Thread(_trabajo_envio, daemon=True)`.
3. El hilo llama `client.procesar_y_enviar(...)` (bloqueante, puede tardar:
   lee toda la BD y arma el loop) y reporta por `root.after(0, ...)`
   (todo toque a widgets vuelve al hilo de tkinter).
4. `_fin_envio(ruta_spec)` rehabilita el botón, lee `td/spec_fluir.json` y
   loguea el detalle:

```
✅ Spec escrito: ...\td\spec_fluir.json
✅ Enviado por 9002: 737 medios · 551 img · 62 vid · 17 video360 · 84 aud · 23 txt · 16 chiches
→ Revisa TouchDesigner (puerto 9002).
```

(`video360` sale de separar `por_tipo["video"]` por el marcador `es_360`,
igual que hace el puente.)

---

## 4. Delegación del envío (contrato 9002 intacto)

`FluirClient.procesar_y_enviar` es un wrapper fino:

```python
spec_salida = os.path.join(RAIZ_PROYECTO, "td", "spec_fluir.json")
ruta_spec = _procesar_rafaga(
    db_path=db_path,            # resolver_db()
    selecciones=selecciones,    # {grupo: [valores]} desde los checkboxes
    loop_secs=loop_secs,        # del campo (default 300.0)
    spec_salida=spec_salida,
    host="127.0.0.1",
    enviar_medios=...,          # checkbox "Enviar medios"
)
```

**Por qué delegar y no duplicar**: `_procesar_rafaga` ya implementa el
contrato completo y probado contra TD — traducción de grupos a filtros
(`GRUPOS_OSC_A_FILTRO`, horas `'09:00'`→9), generación con
`loop_db.generar_loop` (rango horario duro, municipios duro, colores/tags como
prioridad), separación video/video360, chiches, y la secuencia exacta por 9002
(`/resumen` → `/filtro`×N → por tipo `/tabla`+`/medio`(+/`/texto` para text)
→ `/chiche` → `/fin`). Cualquier evolución del contrato queda en UN solo lugar.

Detalle del wire y armado del receptor en TD:
`docs/retorno_fluir_td.md`.

### 4.1 Lo que la GUI eliminó del diseño previo

Cuando la GUI es el origen, **no hay ráfaga ni debounce**: el listener en 9001,
el acumulador por grupo, el `threading.Timer` y el doble disparo
(timer + polling) fueron eliminados. El click dispara directo. Esto también
elimina una condición de carrera latente de la versión anterior (dos caminos
podían procesar la misma ráfaga).

---

## 5. Uso

```powershell
python gui_fluir.py
```

1. Elegir pestaña(s) y marcar items (usá `🔍` para filtrar en municipios/tags,
   `Todo/Nada` para acciones masivas).
2. Ajustar `Loop secs` si hace falta (default 300.0).
3. `Consultar y enviar por 9002`.
4. Verificar en TD que `fluir_estado`/`fluir_*` se poblaron (el log de la GUI
   muestra totales por tipo; el cotejo fino lo hace el callbacks con el spec).

Requisitos: `python-osc` (ya del proyecto), tkinter (stdlib), DB ingesta
previa. No requiere Ollama ni red.

### Probar sin TD (listener falso)

```powershell
# T1: escuchar 9002 unos segundos
python scripts/td/osc_probe.py 9002 15
# T2: disparar el envío desde la GUI (botón) durante esa ventana
```

---

## 6. Verificación realizada (2026-08-23)

| Test | Resultado |
|---|---|
| Sintaxis (`py_compile`) | OK |
| Datos BD: 24 h · 74 municipios · 10 colores · **200 tags top-frecuencia** | OK |
| Tags ordenadas alfabéticamente dentro del top-200 (`_clave_alfabetica`) | OK (`True`) |
| Arranque GUI: 4 pestañas, 24+74+10+200 vars, 4 canvas, 2 filtros | OK |
| Contadores en vivo (marcar 3 → `3 seleccionados`; `Todo` tags → `200/200`) | OK |
| Filtro tags `sol` → 4 visibles, selección preservada al filtrar | OK |
| Envío real con listener en 9002 (horas 9+15): **659 mensajes** — `resumen (737, 300.0, 551, 62, 84, 23, 17)` · 638 `/medio` · `/texto` · chiches · `fin (737,)` | OK |
| Espec escrito en `td/spec_fluir.json` (737 medios, 16 chiches) | OK |

---

## 7. Decisiones de diseño y lecciones

1. **Delegar en `puente_td._procesar_rafaga`** en vez de duplicar el envío:
   cero divergencia de contrato; la GUI solo arma `selecciones`.
2. **Raíz del proyecto según ubicación del script**: 1 nivel para
   `gui_fluir.py` (raíz), 3 niveles para scripts bajo `scripts/td/`. Bug real:
   subir de más dejó el import de `loop_db` apuntando afuera del proyecto y el
   error murió en un hilo sin rastro visible ("no llega nada por 9002").
3. **Sin debounce cuando la GUI es origen**: ráfaga/debounce eran necesarios
   porque TD mandaba todo en un click; con checkboxes el estado ya está
   consolidado al momento de enviar.
4. **Top-200 por frecuencia ≠ primeras 200 alfabéticas**: el corte va sobre
   frecuencia y el orden alfabético es solo presentación dentro del corte.
5. **Valor puro vs texto mostrado**: el checkbox puede decir `carteles
   colorid… (9)` pero lo que viaja a filtros es el valor completo sin freq ni
   truncado.
6. **Selección sobrevive al filtro**: las `BooleanVar` viven aparte de los
   widgets; reconstruir el grid nunca pierde marcas.
7. **Toques a widgets solo desde el hilo de tkinter**: el hilo de envío reporta
   con `root.after(0, ...)`.

---

## 8. Relación con otros documentos

| Doc | Qué aporta |
|---|---|
| `docs/retorno_fluir_td.md` | Contrato byte a byte del canal 9002 y armado del receptor (`osc_in2`, tablas `fluir_*`) |
| `docs/motor_loop.md` | Semántica del loop que produce `loop_db.generar_loop` (arcos horarios, prioridad, keypoints) |
| `scripts/td/puente_td.py` | Función delegada `_procesar_rafaga` y modo CLI `fluir` (alternativa sin GUI) |
| `scripts/td/elecciones.py` | Fuente de la lógica de conteo/filtrado de tags y del orden alfabético normalizado |
