# Visualizaciones — Sesión 28 Jul 2026

Documentación de las decisiones de diseño tomadas durante la sesión
de desarrollo de la visualización web (deploy) (lienzo).

---

## 1. Sistema de bloques

### Decisión
Reemplazar la UI fija (slider de hora + barra de chips) por un sistema de
**bloques HTML en coordenadas mundo**, sincronizados con la cámara del canvas.

### Motivación
- Queremos un espacio explorable con zoom/pan donde cada elemento tiene
  tamaño y posición propia
- Los bloques pueden mezclar HTML nativo (chips, texto, `<video>`, `<img>`)
  con el canvas de fondo (grilla, paleta horaria)
- Cada bloque escala con el zoom manteniendo su proporción interna

### Implementación
- `#mundo` → contenedor `position:fixed` que cubre toda la pantalla
- Cada bloque es un `<div>` con `position:absolute; transform-origin: 0 0`
- La posición y escala se setean vía `transform: translate(X,Y) scale(S)`
- `syncBlocks()` se llama desde `dibujar()` (cada frame si hay cambio de paleta,
  y en cada zoom/pan)

### Bloques definidos

| Bloque | Tipo | Tamaño mundo | Contenido |
|--------|------|-------------|-----------|
| colores | selector | 360×52 | 9 chips de color toggleables |
| horas | selector | 340×76 | 24 chips numéricos (00–23) |
| provincias | selector | 290×48 | CABA, Buenos Aires, Córdoba |
| municipios | selector | 390×48 | Top 10 municipios |
| tags | selector | 240×160 | Nube de palabras (placeholder) |
| imágenes | media | 320×220 | Placeholder |
| videos | media | 280×200 | Placeholder |
| textos | media | 200×150 | Placeholder |
| sonidos | media | 140×90 | Placeholder |
| mapa | media | 280×200 | Placeholder |

---

## 2. Colocación aleatoria

### Decisión
Cada vez que se carga la página, los bloques se reposicionan aleatoriamente.

### Algoritmo: territorio de medios (contiguo)
1. Se mezcla el orden de los 5 bloques de medios
2. El primero se coloca en una posición aleatoria centrada
3. Cada bloque siguiente se adhiere a un borde aleatorio de un bloque ya colocado
4. Se permiten offsets aleatorios a lo largo del borde para forma orgánica
5. Si no encuentra posición sin superposición tras 80 intentos, cae a la derecha de todo

Resultado: una figura irregular no cuadrada donde todos los medios comparten
al menos un borde con otro (territorio común).

### Algoritmo: selectores (dispersos)
1. Se mezcla el orden de los 5 selectores
2. Cada uno se coloca en una posición aleatoria dentro de un área de 1200×1200
3. Se rechazan posiciones que se superpongan con cualquier bloque ya colocado
4. Hasta 120 intentos por bloque

### Auto-fit de cámara
Después de colocar todos los bloques, la cámara se ajusta para que se vean
todos con padding de 1.6×.

---

## 3. Selector de horas (chips)

### Decisión
Reemplazar el slider de 24 horas por chips individuales toggleables,
idénticos en comportamiento a los chips de color.

### Por qué
- Permite seleccionar múltiples horas a la vez (no solo una)
- Libera espacio en pantalla (se eliminó el slider y su UI asociada)
- Es consistente con el resto de selectores (colores, provincias, municipios)

### Comportamiento
- Cada chip de hora (00–23) es un botón compacto que se togglea al clickear
- Al clickear, la hora se agrega o remueve de `horasSeleccionadas[]`
- La paleta del fondo transiciona hacia la ÚLTIMA hora clickeada
- Durante el FLOW, los chips de hora se deshabilitan

---

## 4. Botón "Fluir" y duración

### Decisión
Se agrega un botón "Fluir" flotante (fixed, bottom center) que activa un
ciclo de 5 minutos de duración.

### Comportamiento
- **Estado idle**: botón muestra "Fluir"
- **Al clickear**: 
  - Toma las horas seleccionadas como ciclo (si hay menos de 2, usa las 24)
  - La paleta del fondo empieza a recorrer esas horas durante 5 minutos
  - El botón muestra cuenta regresiva (MM:SS)
- **Durante el flujo**: 
  - Los chips de hora están deshabilitados
  - La paleta interpola suavemente entre las horas del ciclo
  - El wrapping horario se maneja correctamente (23→03 va por 00,01,02)
- **Al terminar**: 
  - La paleta se queda en la última hora
  - El botón vuelve a "Fluir"
  - Los chips se re-activan

### Próximos pasos (documentados)
- Cuando otros bloques tengan contenido, el "Fluir" activará también
  la reproducción/rotación de medios
- La duración de 5 minutos podría configurarse o depender de la cantidad
  de contenido seleccionado

---

## 5. Filtros conectados a todos los medios (Ago 2026)

### Decisión
Todos los chips de selección (colores, horas, provincias, municipios, tags)
filtran **todos los bloques de medios** (imágenes, videos, sonidos, textos).
La conexión es **en vivo**: al tocar cualquier chip se recargan los medios
(`cargarMediosFiltrados()`), con la protección de carreras `MEDIOS_REQUEST_ID`.

### Reglas
- **Filtros combinados con AND** en `medios_filtrados.php`: municipio IN, provincia IN,
  color IN (color_1..3), tag (LIKE sobre keywords), horas (rango).
- **Horas — franja simple**: `[min(horas), max(horas)]` en hora local (Argentina UTC−3).
  Sin horas → sin filtro; 1 hora → esa hora exacta. El snapshot guarda `hora` en UTC
  y la API convierte a local.
- **Videos incluidos**: el bloque Videos se llena cuando los filtros coinciden (lista de
  descripciones). La reproducción (y 360°) sigue pendiente.

### Limitaciones (fase "compleja" pendiente)
- Sin **cruce de medianoche**: 22 y 2 → franja 2–22 amplia.
- **Textos sin hora**: no tienen `timestamp_utc` → quedan fuera del filtro por horas.
- **Sin resultados**: si un filtro no encuentra medios, los bloques muestran "—";
  falta decidir qué hacer en ese caso.

### Bloque "Videos 360°" (Ago 2026)
- Nuevo bloque de medios que lista los videos `subtipo='360'` filtrados por los
  mismos chips (segundo fetch `tipo=video&subtipo=360` en `cargarMediosFiltrados`).
- Click en un ítem → **visor 360 embebido en el propio bloque** (no fullscreen)
  con Three.js local (`js/three.min.js`): esfera + drag/zoom/auto-rotación y
  barra ◀▶ para navegar entre videos. Requiere HTTP Range en `servir_medio.php`
  (implementado). Detalle en `docs/videos_360_web.md` y `docs/deploy.md`.

---

## 6. Pendientes / Próximos pasos

### Inmediatos
- Conectar API de medios a los bloques de imágenes, videos, textos, sonidos
- Implementar el mapa (Folium? Leaflet? Canvas?)
- ~~Poblar la nube de tags con datos reales de `ia_keywords` de la DB~~ → ✅ resuelto:
  `deploy/api/tags.php` arma la nube desde la columna `keywords` del snapshot
  (exportada por `exportar_visualizacion.py` desde `ia_keywords`).
- Que la selección de colores/provincias/municipios filtre el contenido
  de los bloques de medios

### Visuales
- Transiciones entre bloques al cambiar selección
- Efecto de "recorrido" (línea multicolor — pendiente de definir geometría)
- Animación de entrada de bloques al cargar la página

### Técnicos
- Evaluar performance con contenido real (imágenes, videos)
- Optimizar `syncBlocks()` si hay muchos bloques
- Separar el JS en módulos si crece demasiado
