# Discrepancia de horarios en cámaras Insta360 (videos 360°)

> **Problema**: los videos 360° del viaje Buenos Aires → Tucumán fueron grabados con **dos cámaras Insta360 cuyos relojes NO estaban sincronizados** entre sí ni con la hora real argentina. Si se ingiere confiando en el `filename` o en el reloj de la cámara, los timestamps quedan mal. Este documento describe el modelo validado de corrección.

---

## 1. Hallazgo principal

El `QuickTime:CreateDate` embebido en los MP4 360° es **UTC** (verificado por GPX y observaciones de luz del usuario). Por lo tanto:

```
hora real local (Argentina, UTC-3) = QuickTime:CreateDate − 3 h
```

Los `filename` (`VID_YYYYMMDD_HHMMSS_...`) muestran el reloj de cada cámara, que **no es la hora real**:

| Cámara | Reloj de fábrica | Offset filename → embebido UTC | Hora real desde filename | Perfil |
|---|---|---|---|---|
| **A** | Los Ángeles (UTC−7) | **+7.0 h** | `filename + 4 h` | 62 Mbps, 29.97 fps |
| **B** | UTC+1 | **−1.0 h** | `filename − 4 h` | 30-52 Mbps, 29.97 fps |
| **B reconfigurada** | mixto (tocada a mano) | −0.5 / 0.0 / +0.5 / +1.0 h | variable | 30-45 Mbps, 29.97 fps |

**Regla operativa**: SIEMPRE usar el `QuickTime:CreateDate` embebido (o `ffprobe`/ExifTool) para deducir la hora real. Nunca confiar en el `filename` solo.

---

## 2. Identificación de cámaras

La cámara se identifica por **bitrate + fps + offset de reloj** (los videos 360° fueron remuxeados con ffmpeg, así que **perdieron GPS y serial/Make/Model**).

### Cámara A — reloj LA (UTC−7)
- 30 de los 45 videos + el video de Luján (`Testeo_5/8-12_...`).
- Offset constante **+7.0 h** (filename → embebido).
- 62 Mbps constante, 29.97 fps.
- Cubre todo el viaje (11-ago → 31-ago).

### Cámara B — reloj UTC+1
- 9 videos + el de Termas (`Testeo_5/9-02_...`).
- Offset **−1.0 h**.
- Bitrate variable 30-52 Mbps, 29.97 fps.
- **Excepción**: el video de Termas es **24 fps** (46 Mbps) — única vez que B grabó con otra config.

### Cámara B con reloj reconfigurado (huérfanos)
5 videos con bitrate de B (30-45 Mbps) pero offsets distintos:

| Fecha | Archivo | Offset | Hora real deducida |
|---|---|---|---|
| 11-ago | `VID_20250811_214600_00_014.mp4` | +0.5 h | 19:15:58 |
| 11-ago | `VID_20250811_214600_00_015.mp4` | +1.0 h | 19:45:56 |
| 23-ago | `VID_20250823_211330_00_032.mp4` | −0.5 h | 17:43:30 |
| 23-ago | `VID_20250823_211330_00_033(1).mp4` | −0.0 h | 18:13:29 |
| 26-ago | `VID_20250826_141728_00_057.mp4` | −0.5 h | 10:47:28 |

**Caso ambiguo**: `INSTA 5 - 8-25_...VID_20250824_195218_00_152.mp4` tiene offset de A (+7.0 h) pero 50 Mbps (bitrate de B). Es el único archivo con prefijo "INSTA 5" en la carpeta.

> **Lección 2026-08-24 (gap del track en este video)**: el video `_152` (hora real 23:52 local
> del 24-ago = 02:52Z del 25-ago) arranca **dentro de un gap del track GPX de 9110 s (2.5 h)**
> (25-ago 00:40Z → 03:12Z). La interpolación lineal a través del hueco fabricaba una posición
> falsa para el inicio del video. `ubicar_videos_gpx.py` ahora **no emite** muestras dentro de
> gaps > `--umbral-gap` (default 1800 s = 30 min) y los mapas marcan esos medios como
> "posición incierta" (`--umbral-gap-aviso`). O sea: un timestamp correcto + un track con
> hueco = posición interpolada no confiable → se omite en vez de inventarla.

---

## 3. Anomalías detectadas

1. **Offset +7.5 h en 3 archivos** (`..._091159_00_052`, `..._135749_00_118`, `..._072443_00_458`):
   - Mismo `filename` que su par (`_051`, `_117`, `_457`) pero embebido 30 min después.
   - **No son duplicados ni artefactos de remux**: el GPX los ubica en posiciones consecutivas de la ruta.
   - Causa: el reloj del `filename` **no avanzó 30 min** entre tomas consecutivas; el embebido es el correcto.
2. **24 fps en el video de Termas**: cámara B con config de fps distinta ese día (29.97 → 24).
3. **Videos del 11-ago**: están **fuera del track GPX** (el track arranca el 12-ago en Luján, primer punto `-34.5595,-59.1230`).

---

## 4. Metodología de validación

| Método | Qué confirma |
|---|---|
| Interpolación contra `tracks/Al_FaB_Tucuman.gpx` (3920 pts) | Los 45 videos caen en los segmentos de ruta correctos según su carpeta |
| Pares visuales (usuario mirando los videos) | 178/014 (11 min, misma ubicación), 117/032 (6.3 km), 469/138 (**0.58 km**), 057 (Sinsacate exacto) |
| Observación de luz del atardecer (197/198/199, 26-ago) | Consistente con +4 h dentro del margen de obstrucción del horizonte (~20 min) |
| Consistencia interna | Todos los A con 62 Mbps y +7.0 h constante |

---

## 5. Procedimiento reutilizable (videos nuevos)

> **Estos 45 videos son una MUESTRA**: pueden aparecer más videos de las mismas cámaras (mal sincronizadas o reconfiguradas). Ante cualquier video nuevo, aplicar este diagnóstico:

### Paso 1 — Leer el reloj embebido (siempre la fuente de verdad)
```bash
exiftool -json -n -QuickTime:CreateDate <video.mp4>
```
El `CreateDate` es **UTC**. Hora real local = `CreateDate − 3 h`.

### Paso 2 — Identificar la cámara (offset primero, bitrate como refuerzo)
**El offset `filename → CreateDate` es el clasificador PRIMARIO** (no el bitrate). El bitrate solo refuerza:
```bash
exiftool -json -n -QuickTime:CreateDate <video.mp4>
ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate,bit_rate -of json <video.mp4>
```
| Señal | A | B (normal) | B reconfigurada |
|---|---|---|---|
| Offset filename→embebido | **+7.0 h** | **−1.0 h** | +1.0 / +0.5 / −0.0 / −0.5 h |
| Bitrate | ~62 Mbps | 30-52 Mbps | 30-52 Mbps |
| fps | 29.97 | 29.97 (24 en Termas) | 29.97 |

⚠️ **El bitrate solo NO alcanza**: el 20-ago el `_024` (62.1 Mbps, offset +7.0 h → **A**) convive en la misma carpeta con `_002`/`_003`/`_004` (B). Si se clasificara solo por bitrate, un video A con bitrate de B (caso `INSTA 5 ..._152`) queda ambiguo. **Siempre cruzar offset + bitrate**, y en caso de duda sumar contador (dentro de una misma carpeta los números bajos suelen ser B y los altos A) + validación de luz/GPX.

### Paso 3 — Validar el offset del filename contra la fecha
El offset `filename → embebido` NO es fijo en cámara B. Consultar la tabla de estados (sección 6) por fecha. Si el offset no coincide con lo esperado → es B reconfigurada: **usar el embebido, no el filename**.

### Paso 4 — Cruzar contra el track GPX (si aplica)
Interpolar la hora real deducida contra `tracks/Al_FaB_Tucuman.gpx` (patrón: `scripts/inferir_hora_textos.py`). Si la posición cae en el segmento esperado → confirmado. Ojo: los videos del 11-ago están fuera del track.

### Paso 5 — Validación de luz (si no hay GPX o hay dudas)
Comparar la hora real deducida contra amanecer/atardecer astronómico (NOAA, `scripts/astronomia.py`) y contra la luz visible en el video (puesta/noche/día). El margen de obstrucción del horizonte (árboles) puede ser de ~20 min.

**Casos validados por luz (consistente con +4 h, cámara A):** `_120` 23-ago (real 18:50:06→19:12:48, atardecer 18:54:48 **durante** el video) y `_024` 20-ago (real 18:31:16→18:32:35, atardecer 18:47:26, **15 min después** del fin).

---

## 6. Estado del reloj de cámara B por fecha (tabla de referencia)

| Fecha | Offset filename→embebido | Videos | Cámara |
|---|---|---|---|
| 11-ago | **+0.5 h / +1.0 h** | `_014`, `_015` | B reconfigurada |
| 17-ago | −1.0 h | `_053` | B |
| 20-ago | −1.0 h | `_002`, `_003`, `_004` | B |
| 23-ago | **−0.5 h / −0.0 h** | `_032`, `_033(1)` | B reconfigurada |
| 26-ago | **−0.5 h** | `_057` | B reconfigurada |
| 27-ago | −1.0 h | `_084` | B |
| 31-ago | −1.0 h | `_132`, `_136(1)`, `_137`, `_138` | B |
| 3-sep (Termas) | −1.0 h | `_198` | B (24 fps) |

**Cámara A**: offset **+7.0 h estable** en todos los días (11-31 ago). Única excepción: 3 archivos con +7.5 h por filename atrasado 30 min (el embebido es el correcto).

---

## 7. Implicancias para la ingesta

1. **Fuente de verdad**: `QuickTime:CreateDate` embebido = UTC → hora real = −3 h.
2. Si solo se tiene el `filename` (sin metadatos embebidos):
   - Cámara A: `filename + 4 h` = hora real.
   - Cámara B: `filename − 4 h` = hora real.
   - B reconfigurada: NO confiable — usar el embebido.
3. Los videos 360° remuxeados **no tienen GPS**: la posición debe venir del track GPX interpolado (ver `inferir_hora_textos.py` como patrón de interpolación).
4. Verificar `--mode skip|update|replace` según corresponda al corregir timestamps existentes.

---

## 8. Archivos clave

- Carpeta de videos: `C:\Users\Federico\Desktop\Flujos\Ingesta_2\Videos equirrectangulares\`
- Track GPX: `tracks/Al_FaB_Tucuman.gpx`
- DB: `db/flujos.db` (tabla `media`, `timestamp_utc`, `sunset_ts`, etc.)
- Scripts de análisis (temp, no versionados): `%TEMP%\opencode\listas_nombres.py`, `cruce_gpx_definitivo.py`, `validar_pares_visuales.py`, `sol_199.py`

---

## 9. Herramientas usadas

- `exiftool -json -n -QuickTime:CreateDate` → reloj embebido (UTC).
- `ffprobe -select_streams v:0 -show_entries stream=r_frame_rate,bit_rate` → identidad de cámara.
- Interpolación lineal sobre el GPX (patrón: `scripts/inferir_hora_textos.py`).
- Cálculo astronómico NOAA (en `scripts/astronomia.py`) para validar con luz real.