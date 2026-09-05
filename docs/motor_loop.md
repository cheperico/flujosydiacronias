# Motor de Loop — Diseño y Especificación

> Diseño del motor de presentación en loop. Complementa `diseno_instalacion.md`
> (concepto) y `linea_de_tiempo.md` (línea temporal). Aquí se define la
> **matemática y la arquitectura** concretas para implementar el cerebro
> agnóstico del renderizador (deploy / TouchDesigner).
> Sesión de diseño: Ago 2026.

---

## 1. Objetivo

Un módulo Python puro (sin dependencias de renderizador) que:

1. Recibe **elecciones** del usuario (horas, municipios, colores, tags,
   días, clima, ideas).
2. **Filtra** la DB y arma la selección de medios.
3. Construye un **loop de 5 minutos** (configurable) con:
   - Medios posicionados en el tiempo del loop según su ubicación
     relativa real (tiempo-espacio).
   - **Chiches** (eventos ambientales) disparados en el instante del loop
     donde su condición está activa.
4. Devuelve una **especificación en JSON**, agnóstica del renderizador,
   que web (`deploy/js/app.js`) y TD (`scripts/td/puente_td.py`/OSC) consumen
   igual.

> El motor NO reproduce nada. Solo produce el "guión" (timeline compilado).

---

## 2. Entrada

```json
{
  "loop_secs": 300,                       // duración del loop (5 min)
  "horas_elegidas": [7, 16, 13, 18],      // orden = orden de elección
  "modalidad_ubicaciones": "geo"|"eleccion",
  "filtros": {                            // todos opcionales
    "municipios": ["Inriville", ...],
    "colores": ["rojo", ...],
    "tags": ["paisaje", ...],
    "dias": ["lunes", ...],
    "clima": ["Cielo despejado", ...],
    "ideas": ["La mesa de viaje", ...]    // quieres a biodiversidad
  }
}
```

Notas:
- Si `horas_elegidas` tiene < 2 elementos, se usa la secuencia completa de
  00→23 (de baja `diseno_instalacion` §2.1; modo "todas las horas").
- `modalidad_ubicaciones`: `geo` ordena municipios por `cumul_distance_m`
  (recorrido real BsAs→Tucumán); `eleccion` respeta el orden de elección.

---

## 3. Núcleo matemático (puro, testeable)

### 3.1 Segmentos horarios

Dadas N horas elegidas en orden de `H[0..N-1]` (N ≥ 2):

- Se forman **N−1 segmentos** de arco temporal, cada uno va de `H[i]` →
  `H[i+1]` (i va de 0 a N−2). El último rincón elegido es el **fin** del
  último segmento; el loop simplemente da la vuelta.
- Es exactamente lo que describe `diseno_instalacion` §2.1: con 4 horarios
  se divide en **tercios** (N−1 = 3 segmentos).
- `H[i]` y `H[i+1]` se interpretan en horas en punto de un **reloj de 24h**.
- Cada segmento ocupa una porción **igual** del loop: `duracion_seg =
  loop_secs / (N−1)`.

**Arco temporal de un segmento** = distancia horaria en el reloj:
- si `H[i+1] > H[i]`: arco = `H[i+1] - H[i]` (horas)
- si `H[i+1] <= H[i]`: arco = `24 + (H[i+1] - H[i])` (cruza medianoche)

**Tiempo del loop** donde comienza cada segmento:
- `t0[0] = 0`
- `t0[i] = i * (loop_secs / (N−1))`

**Ejemplo (H=[2,16,19,18], loop=300s, N=4 → 3 segmentos de 100s):**

| Seg | De → A | Arco (h) | Loop desde (s) | Loop hasta (s) |
|-----|--------|----------|----------------|----------------|
| 0   | 7 → 16 | 9 | 0 | 100 |
| 1   | 16 → 13 | 21 (cruza noche) | 100 | 200 |
| 2   | 13 → 18 | 5 | 200 | 300 |

(Esto respeta al pie de la letra el ejemplo de `diseno_instalacion` §2.1:
"4 horarios → 3 tercios", con el segmento nocturno 16→13 donde el cursor da
la vuelta al reloj.)

### 3.2 Posición de un medio en el loop

Un medio tiene una **hora de día real** `h` (0..23.x) derivada de su
`timestamp_utc`.

Para cada segmento `i`, se calcula la **fracción** del arco en la que cae
la hora `h`:

```
frac = (h_avance - H[i]) / arco        ; solo si h cae dentro del arco
```
donde `h_avance = h` si `h >= H[i]`, o `h + 24` si cruza medianoche
(para segmentos nocturnos).

- Si `0 <= frac <= 1` → el medio **pertenece** a ese segmento y se ubica en:
  `t_loop = t_inicio_seg[i] + frac * duracion_seg`
- Si no cae en el arco → **descartado** para ese segmento (y del loop, salvo
  que cine quieras que reaparezca).

**Colisión de posiciones:** si varios medios caen en el mismo `t_loop`, se
resuelve en el renderizador (filo múltiple). El motor puede devolver posiciones
normalizadas y, opcionalmente, un pequeño *jitter* determinista.

### 3.3 Exclusivamente para PUNTOS vs SEGMENTOS

- **Punto** (image, text): ocupa un instante → posición única.
- **Segmento** (video, audio): ocupa un intervalo. Se coloca por su
  `duration_secs`: si entra completo en la porción restante del segmento,
  se muestra entero; si no, se elige un **fragmento** (decisión de
  curcular, se documenta).

---

## 4. Filtrado de medios (consulta /reglas)

El motor arma una query sobre `media` + `media_metadata` combinando,
con AND (todas las condiciones presentes deben cumplirse):

| Grupo | Condición |
|-------|-----------|
| Horas (arco) | el `HARpart` de la hora cae en un segmento (ver §3.2) |
| Municipios | `media.municipio IN (...)` |
| Colores | **prioridad** (no filtra): suman al `score` de cada medio |
| Tags | `EXISTS media_metadata.key IN (5 fuentes de keywords) AND value LIKE '%tag%'` — **OR** entre tags (un medio pasa si contiene ALGUNA); filtro DURO cuando se eligen |
| Días | `media_metadata.key='dia_semana' AND value IN (...)` |
| Clima | `media_metadata.key='weather_label' AND value IN (...)` |
| Ideas | query semántica por embeddings (definido en docs/instalación §1.2) |

**Tags = filtro duro con fallback**: cuando el visitante elige tags, SOLO entran
al loop los medios que contienen alguna (OR de LIKE `%tag%` sobre las 5 fuentes
de keywords: `ia_keywords`, `ia_keywords_transcripcion`, `ia_keywords_texto`,
`ia_keywords_sonido`, `ia_keywords_video` — el mismo universo que arma la nube
`elec_tags`). Si el arco queda con menos de `MIN_MEDIOS_FALLBACK_TAGS`
(default 1) medios, se cae a **prioridad** (se ignoran las tags y se rellena
con todo el filtro base + score) para que la instalación nunca se quede sin
contenido; el resumen anota `FALLBACK a prioridad`. **Pendiente**: reemplazar
el fallback por un aviso real de "no hay suficientes medios seleccionados".

Si no hay criterios, se incluyen todos los medios (modo exploración).

---

## 5. Chiches (eventos ambientales) — v2.3

Se calculan a partir de los campos calculados de la DB y se disparan en
el instante del loop donde la condición está activa (en h del arco).
Cada chiche tiene `t_loop` + `hora` y se ubica geográficamente (`lat/lon/municipio/provincia/departamento` del medio que lo disparó).

| Familia | Texto (variantes true-random) | Condición (DB) | Sostenido |
|---|---|---|---|
| `alba` | "Salió el sol" | `0 ≤ sun_elevation ≤ 3` | no |
| `mediodia` | "Es el mediodía" | `abs(secs_since_noon) ≤ 900` | no |
| `calor` | "Hace calor" 90% / "La calor que hace" 10% | `weather_temp_c > 30` | no |
| `frio` | "Hace frío" | `weather_temp_c < 10` | no |
| `viento` | "Hay mucho viento" 75% / "Se nos vuelan las chapas" 25% | `weather_wind_speed_kmh > 40` | **sí ≥2** |
| `lluvia` | "Está lloviendo" 90% / "Se largó ya" 10% | `weather_precip_mm > 1.0` | **sí ≥2** |
| `nublado` | "Está nublado" | `weather_cloud_pct ≥ 70` (o WMO code 3) | **sí ≥2** |
| `despejado` | "Cielo despejado" | `weather_cloud_pct ≤ 20` (o WMO 0-1) | **sí ≥2** |
| `sol` | "Pega el sol" / "El sol castiga" / "El sol pega fuerte" (33% c/u) | `despejado + temp>28 + sun_elevation>20 + twilight=dia` | **sí ≥2** |
| `noche` | "Es la noche" | `twilight_period IN ('crepúsculo_*','noche')` | no |
| `geo` | "Entramos a X" / "Salimos de Y" (por provincia/depto/municipio) | cambio de `provincia/departamento/municipio` en secuencia ordenada por `hora` | no |

**Sostenido ≥2:** las familias `viento/lluvia/nublado/despejado/sol` solo se emiten cuando la condición lleva 2 medios consecutivos (mitiga transiciones horarias de Open-Meteo; `SOSTEN_MIN=2` en `loop_db.py`). Dedup por `(familia, int(hora))`, no por texto literal.

**Geo:** ingresos/egresos se detectan en orden **geográfico** (`items` ordenados por `cumul_distance_m`, con fallback a `timestamp_utc`) para no zigzaguear por hora del día; se posicionan igual por `hora` (`t_loop`) y se ubican con `lat/lon` del medio que marca el cambio. Primer medio genera solo "Entramos".

Se emite como `{"t": t_loop, "tipo":"chiche", "texto":..., "familia":..., "hora":..., "lat":..., "lon":..., "municipio":..., "provincia":..., "departamento":..., "ubicacion":{lat,lon}}`. Wire OSC 9002: `/flujos/fluir/chiche <hora> <texto> [lat] [lon] [municipio] [provincia] [departamento]` → `fluir_chiches [hora,texto,lat,lon,municipio,provincia,departamento]`.

---

## 6. Salida (especificación de loop)

```json
{
  "loop_secs": 300,
  "segmentos": [ {"from":7,"to":16,"t_start":0,"t_end":75}, ... ],
  "medios": [
    {"media_id": 42, "tipo":"image", "t_loop": 11.2, "duracion": 0,
     "ruta": "abs", "hora": 8.0, "ubicacion": {"lat":..,"lon":..},
     "municipio":"..", "color":"..", "tags":[".."], "desc":".."},
    ...
  ],
  "chiches": [ {"t": 32.0, "tipo":"chiche", "texto":"Salió el sol"}, ... ]
}
```

El render renderer consume esta spec: posiciona los elementos en su `t_loop`
dentro de `[0, loop_secs)`, avanza el cursor, y dispara los chiches.

---

## 7. Arquitectura del módulo (archivos)

```
scripts/ai_media/loop_engine.py      # Motor puro (sin DB ni render):
                                      #   calcular_segmentos(), posicionar_medio(),
                                      #   _hora_en_fraccion(), _t_loop()
scripts/ai_media/loop_db.py          # Build / integración DB: filtra media+metadata,
                                      #   y genera la spec (usa loop_engine + ModoHelper)
scripts/test_motor_loop.py           # Tests del núcleo puro
docs/motor_loop.md                   # ← este documento
```

Reglas:
- `loop_engine` **NO importa sqlite ni web** — funciones puras (testeable).
- `loop_db` importa `loop_engine` + `db.util`.
- Si exporta hacia web: `loop_motor` también puede emitir JSON y el web
  `app.js` lo consume; si es TD, `puente_td.py` lo envía vía OSC o JSON.

---

## 8. Pendiente / abierto (documentado, no bloquea implementación)

- **Duración de fragmento** de videos/audios largos (§3.3).
- **Clustering** de muchas fotos del mismo minuto (§linea_de_tiempo).
- **Conexión** de `embeddings` ideas (requiere $flección semántica).
- Exacto render de mapa.
- Resolución de la duración de loop variable según número de horas.