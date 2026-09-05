# Diseño de la Instalación — Flujo de Presentación (Loop)

> Documento de diseño de la instalación interactiva.
> Sesión de diseño: Jul 2026. Fuente: conversación con el autor.
> **Objetivo**: definir el flujo completo desde la DB hasta la presentación
> en loop, tanto para renderizador TouchDesigner como HTML.

---

## Visión general

```
[DB: metadatos] ──→ [Elecciones (nubes de metadatos)] ──→ [Filtros]
                                                              │
                                                              ▼
                                                     [Medios seleccionados]
                                                              │
                                                              ▼
                                              [Presentación = loop de 5 min]
                                          imágenes, audios, videos, textos,
                                          chat Telegram, mapa, "chiches"
```

- Desde la base de datos llegan **metadatos** (etiquetas, ciudades, horarios,
  colores, días, clima, ideas) que sirven de **filtro** para armar las
  presentaciones.
- Las presentaciones por ahora duran **cinco minutos** (el loop es de 5 min).
- Las presentaciones se arman con los **medios que sirve la DB** a partir de
  lo filtrado.
- Cada grupo de metadatos se presenta en forma de **nube seleccionable**.
- Es una instalación sobre un **viaje**, por lo que la duración del viaje y
  el recorrido son relevantes.

---

## 1. Grupos de metadatos (7)

| Grupo | Fuente DB | Operación de filtrado |
|-------|-----------|------------------------|
| **Horas** | `timestamp_utc` | `substr(timestamp_utc, 12, 2)` en rango del arco |
| **Municipios** | `municipio` | `municipio IN (...)` |
| **Colores** | `color_{1,2,3}_name_basic` | `color_{1,2,3}_name_basic IN (...)` |
| **Tags** | `media_metadata` key=`ia_keywords` | `value LIKE '%tag%'` |
| **Días** | `media_metadata` key=`dia_semana` | `value IN (...)` |
| **Clima** | `media_metadata` key=`weather_label` | `value IN (...)` |
| **Ideas** | embeddings (`media_embeddings`) | búsqueda semántica (similitud coseno) |

> **Nota clave**: cada grupo tiene su propia **operación de filtrado** sobre la
> DB (método de selección de medios). La **forma de mostrarlo** (chips, nube,
> peso visual) es una dimensión independiente y puede variar por grupo.

### 1.1 Días de la semana (incluido, meses excluido)

- **Días**: interesante porque un día (ej: lunes) te posiciona en varios
  lugares diferentes del viaje → bueno para la dérive.
- **Meses**: descartado — el viaje ocupó poco tiempo y se movieron en bloque.

### 1.2 Ideas (desarrolladas)

El clustering crudo no alcanza como "ideas". Se proponen ideas **curadas**,
cada una con definición mixta: tags exactos + query semántica por embeddings.

| Idea | Definición operativa |
|------|----------------------|
| **El aguacate** | El objeto recurrente (`LIKE '%aguacate%'` + similares) |
| **Gente que encontramos** | retrato grupal, retrato, autores... |
| **La mesa de viaje** | comida, restaurante, café |
| **La llanura infinita** | paisaje, naturaleza, cielo, campo |
| **A toda máquina** | callejera, arquitectura, urbano, ruta, camino |
| **La noche en la ruta** | nocturna, crepúsculo, luna, oscuridad |
| **Bestiario** | animales, caballo, vaca, perro |
| **Detalles que se ven** | macro, abstracto, textura, color |

*(La lista de ideas puede extenderse en futuras sesiones.)*

---

## 2. Modalidad de las elecciones

### 2.1 Horas — orden secuencial por elección

- Las horas se acomodan a la duración del loop **en orden secuencial según
  elección**: lo primero elegido va primero, lo segundo segundo, etc.
- **Ejemplo**: elige 7, 16, 13, 18 → cuatro horarios → se divide en tercios
  (N horarios → N−1 segmentos):
  - Primer tercio: 7 → 16
  - Segundo tercio: 16 → 13 (cubre la noche, el cursor da la vuelta)
  - Tercer tercio: 13 → 18
- El cruce nocturno es el caso interesante: el arco temporal da la vuelta al
  reloj (16→24→13).

### 2.2 Ubicaciones (municipios) — dos modalidades

1. **Por geografía**: el orden es de BsAs a Tucumán, según el orden de
   aparición en el viaje (punto de partida: BsAs; destino: Tucumán).
   Se puede ordenar con `cumul_distance_m`.
2. **Por elección**: el orden de elección ordena — el viaje es "nuevo" en el
   orden presentado (no respeta la ubicación geográfica).

---

## 3. Posicionamiento de medios en el loop

Con horas y ubicaciones definidas, se arma la selección de medios en función
de horarios y ubicaciones: chat de Telegram, fotos, audios, etc.

### 3.1 Regla de posición (interpolación sobre el arco elegido)

Cada medio se ubica en el loop por **la fracción de su hora real dentro del
arco del segmento** donde cae.

Ejemplo (segmento 7→16, dura 100s si es 1 de 3):

| Medio (hora real) | Cálculo | Posición en el segmento |
|---|---|---|
| Foto 8:00 | (8−7)/(16−7) = 1/9 | ~11% → a los ~11s |
| Foto 14:30 | (14.5−7)/9 | ~83% → a los ~83s |
| Audio 12:00 | (12−7)/9 | ~55% → a los ~55s |

Cruce nocturno (16→13, arco real 21h):

| Medio (hora real) | Cálculo | Posición |
|---|---|---|
| Foto 22:00 | 6h desde 16 / 21h | ~29% |
| Audio 3:00 | 8h + 3h = 11h / 21h | ~52% |

**Si el medio cae fuera del arco del segmento** (ej: medio de medianoche en
segmento 7→16), **no aparece en ese segmento** — queda descartado de la
presentación o aparece solo si otro segmento lo cubre.

### 3.2 Duración de medios (audios/videos)

- **Completos** si son cortos.
- **Fragmento** si son largos — se elige un fragmento.
- Filtros adicionales (ej: audios con contenido hablado vía transcripciones)
  ayudarán a decidir el fragmento.

### 3.3 Mapa

Puede renderizarse un **mapa con las ubicaciones** de los medios
seleccionados.

---

## 4. Contenido devuelto (lo que se presenta)

1. **Metadatos seleccionables** (nubes): keywords, municipios, horarios,
   colores, días, clima, ideas.
2. **Usuario elige** cosas de las listas → criterios de filtrado.
3. **Devuelve**: videos, sonidos, textos, chats, fotografías (slideshows),
   mapa, chiches.
4. **Presentación = loop** con:
   - Imágenes ordenadas
   - Chat vivo (Telegram)
   - Sonidos que se disparan
   - Textos
   - Videos
   - Chiches (eventos ambientales)

---

## 5. "Chiches" (eventos ambientales) — v2.3

Triggers que se disparan en el momento del loop donde esa condición está
activa. Todos los datos ya están calculados en la DB (ver `docs/motor_loop.md` §5 para tabla completa):

* Viento `>40 km/h` con variantes `"Hay mucho viento"` 75% / `"Se nos vuelan las chapas"` 25% (sostenido ≥2)
* Lluvia `>1.0 mm` con variantes `"Está lloviendo"` 90% / `"Se largó ya"` 10% (sostenido ≥2)
* Nubosidad `"Está nublado"` (≥70%) / `"Cielo despejado"` (≤20%) + compuesto `"Pega el sol"` / `"El sol castiga"` / `"El sol pega fuerte"` (despejado+calor, sostenido)
* Calor con variante `"La calor que hace"` 10% (true random)
* Geo: ingresos/egresos a provincia/departamento/municipio (`"Entramos a X"` / `"Salimos de Y"`), ubicados con `lat/lon` del medio que lo disparó
* Sol alba/mediodía/noche se mantienen

Cada chiche lleva `hora + lat/lon/municipio/provincia/departamento` (wire `/chiche` extendido → `fluir_chiches` de 7 columnas).

---

## 6. Arquitectura de implementación

La arquitectura es la misma para TD o HTML: un "cerebro" que toma elecciones,
filtra la DB, y arma una línea de tiempo de 5 min con posición de medios y
triggers de chiches. Lo único que cambia es el renderizador.

- **HTML (`deploy/`)**: ya tiene ~80% del esqueleto visual (bloques, chips,
  botón Fluir de 5 min, carga desde API). Falta: motor de loop real (posición
  de medios, chiches, mapa interactivo, render de fotos/audios).
- **TouchDesigner**: el cerebro ya existe en Python (`scripts/td/puente_td.py` +
  OSC); TD sería el músculo de reproducción.

**Próximo paso natural**: motor de loop en Python puro (matemática de arcos +
posicionamiento + triggers), agnóstico al renderizador y testeable sin él.
