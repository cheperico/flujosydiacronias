#!/usr/bin/env python3
"""
loop_db.py — Construcción del motor de loop integrado con la DB de Flujos.

Lee la base `db/flujos.db` (SOLO LECTURA), aplica los filtros elegidos por el
usuario (municipios, colores, tags, días, clima), calcula la hora de día de
cada medio desde `timestamp_utc`, genera los chiches ambientales y produce el
spec JSON del loop agnóstico del renderizador (web / TouchDesigner). No
escribe nada en la DB.

Modelo de selección (rediseño 2026-08-09):
  * FILTROS DUROS (descartan):
      1. Horario: rango [min(horas), max(horas)] en hora LOCAL del viaje
         (Argentina, UTC-3), ambas puntas inclusive. Sin horas → todo el
         día (0..23); con 1 hora → esa hora exacta [h, h]; con 2+ → [min, max].
      2. Municipios: `media.municipio IN (...)` si vienen elegidos.
      3. (legado) Días y clima vía `media_metadata` (EXISTS).
    * PRIORIDADES (NO descartan; suman al `score` de cada medio):
      - Color: +1 por slot (color_1/2/3_name_basic) coincidente. Solo las
        fotos tienen score de color; videos/audios/textos → 0.
      - Etiquetas: +1 por etiqueta elegida contenida en `ia_keywords`.
    * PUNTUACIÓN total = n_colores_coinciden + n_etiquetas_coinciden.
    * ORDEN de salida: por hora local asc, y como desempate el mayor score.
    * keypoint = `t_loop` (ubicación temporal dentro del loop) que ya produce
      `loop_engine.armar_spec`; se replica como campo `keypoint` en cada medio.

Pipeline de `generar_loop`:
    1. Normaliza las horas: define el rango de filtrado y los segmentos.
    2. Recupera los N−1 segmentos temporales del motor puro (`loop_engine`).
    3. Consulta `media` (+ `media_metadata`) con filtros duros + score.
    4. Ordena: hora asc, score desc como desempate.
    5. Genera los chiches desde los campos calculados (weather_*, sun_*).
    6. Llama `loop_engine.armar_spec` y devuelve la spec con `medios`,
       `por_tipo` (una tabla por tipo de medio) y `resumen` (debug/puente).

Uso:
    python scripts/ai_media/loop_db.py --horas 6 13 --salida spec.json
    python scripts/ai_media/loop_db.py --horas 6 13 --municipios Inriville --dry-run
    python scripts/ai_media/loop_db.py --por-tipo --horas 9 17 --salida spec.json

Requiere: db.util (abrir, resolver_db) y loop_engine.
"""

import argparse
import json
import logging
import os
import random
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

# La consola de Windows por defecto usa cp1252, que no puede codificar los
# caracteres de caja '─' que imprime el CLI (ni '→'). Reconfiguramos stdout a
# UTF-8 con fallback 'replace' (mismo fix que en test_motor_loop y limpiar_tandas).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass  # Python < 3.7 o stdout sin reconfigure

log = logging.getLogger(__name__)

# Directorios en sys.path para ejecución standalone desde cualquier lugar:
#  - proyecto raíz (raíz/): permite `from db.util import ...`
#  - scripts/ai_media/    : permite `import loop_engine` sin disparar el
#                           costoso __init__.py del paquete ai_media (que
#                           importaría ollama_client y demás).
_AI_MEDIA_DIR = os.path.dirname(os.path.abspath(__file__))
# Proyecto raíz: scripts/ai_media/ → subir 2 niveles (scripts/ → raíz/)
sys.path.insert(0, os.path.dirname(os.path.dirname(_AI_MEDIA_DIR)))
sys.path.insert(0, _AI_MEDIA_DIR)

from db.util import abrir, resolver_db  # noqa: E402
import loop_engine  # noqa: E402

# ── Umbrales de chiches (ver docs/motor_loop.md §5) ─────────────────────────
MEDIODIA_UMBRAL_SEG = 900.0     # secs_since_noon ≈ 0 → ±15 min del cenit
TEMP_CALOR = 30.0               # weather_temp_c > 30  → "Hace calor"
TEMP_FRIO = 10.0                # weather_temp_c < 10  → "Hace frío"
TEMP_SOL = 28.0                 # temp para "Pega el sol" (despejado + calor)
VIENTO_ALTO = 40.0              # weather_wind_speed_kmh > 40 → "Hay mucho viento"
PRECIP_LLUVIA = 1.0             # weather_precip_mm > 1.0 → "Está lloviendo"
ELEVACION_ALBA_BAJA = 0.0       # sun_elevation cruza 0° al alba
ELEVACION_ALBA_ALTA = 3.0
ELEVACION_SOL = 20.0            # sun_elevation mínima para "Pega el sol"
NUBE_NUBLADO = 70.0             # weather_cloud_pct >=70 → "Está nublado"
NUBE_DESPEJADO = 20.0           # weather_cloud_pct <=20 → "Cielo despejado"
SOSTEN_MIN = 2                  # muestras consecutivas para mitigar Open-Meteo

# Variantes con peso (true random). Familia -> (variantes, pesos).
VARIANTES_CHICHES: dict[str, tuple[list[str], list[float]]] = {
    "viento": (["Hay mucho viento", "Se nos vuelan las chapas"], [0.75, 0.25]),
    "lluvia": (["Está lloviendo", "Se largó ya"], [0.90, 0.10]),
    "calor": (["Hace calor", "La calor que hace"], [0.90, 0.10]),
    "sol": (["Pega el sol", "El sol castiga", "El sol pega fuerte"], [1, 1, 1]),
}


def _elegir_variante(familia: str, fallback: str) -> str:
    """Elige texto true-random con peso para familia, o fallback si no hay variantes."""
    entry = VARIANTES_CHICHES.get(familia)
    if not entry:
        return fallback
    variantes, pesos = entry
    return random.choices(variantes, weights=pesos, k=1)[0]

# Hora de día por defecto para textos sin timestamp (puntos curados).
HORA_DEFECTO_TEXTO = 12.0

# Fuentes de keywords sobre las que se aplica el filtro duro de tags y el
# `score` de prioridad. Es el MISMO universo que arma la nube de elecciones
# (scripts/td/elecciones.py::CLAVES_TAGS): así cualquier tag que el visitante
# pueda elegir en TouchDesigner tiene correspondencia aquí.
CLAVES_TAGS_LOOP = (
    "ia_keywords",
    "ia_keywords_transcripcion",
    "ia_keywords_texto",
    "ia_keywords_sonido",
    "ia_keywords_video",
)

# Umbral de fallback de tags: si el filtro DURO por tags deja MENOS de este
# número de medios dentro del arco, se cae a prioridad (se ignoran las tags y
# se rellena con todo el filtro base + score) para que la instalación nunca se
# quede sin contenido. El aviso de insuficiencia real ("no hay suficientes
# medios seleccionados") queda pendiente como reemplazo de este fallback.
MIN_MEDIOS_FALLBACK_TAGS = 1

# Períodos de twilight considerados "noche"
NOCHE_PERIODOS = {
    "noche", "crepuculo_civil", "crepuculo_nautico", "crepuculo_astronomico",
}
# Período del amanecer (refuerzo de "Salió el sol")
ALBA_PERIODOS = {"golden_hour", "blue_hour"}

# Claves de media_metadata que se incorporan al spec
CLAVES_METADATA = [
    "ia_keywords",
    "ia_description",
    "ia_keywords_texto",
    "texto_completo",
    "titulo_seccion",
    "weather_temp_c",
    "weather_wind_speed_kmh",
    "weather_precip_mm",
    "weather_cloud_pct",
    "weather_code",
    "weather_label",
]

# Orden estable de claves de `por_tipo` en la spec final
TIPOS_POR_DEFECTO = ["image", "video", "audio", "text"]


# ── Timestamps UTC (formato mixto Z / +00:00) ────────────────────────────────


def _parsear_timestamp(valor: Optional[str]) -> Optional[datetime]:
    """
    Parsea un `timestamp_utc` a datetime.

    Los timestamps vienen en formato mixto: algunos terminan en 'Z' y otros en
    '+00:00'. Aunque Python 3.11+ ya soporta 'Z' en `fromisoformat`, por
    robustez se reemplaza 'Z' por '+00:00' antes de parsear.

    Args:
        valor: timestamp_utc en texto (o None).

    Returns:
        datetime, o None si es vacío o no se puede parsear.
    """
    texto = (valor or "").strip()
    if not texto:
        return None
    if texto.endswith("Z"):
        texto = texto[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(texto)
    except ValueError:
        log.debug("  timestamp_utc no parseable: %r", valor)
        return None


# Zona horaria del viaje (Argentina, UTC-3). La instalación agrupa por hora
# LOCAL del viaje (7, 13, 18 = "las 7 de la mañana en la ruta"), pero
# timestamp_utc está normalizado a UTC. Sin esta conversión los medios
# caerían 3h tarde (una foto de las 07:00 local = 10:00 UTC).
_ZONA_ARGENTINA = timezone(timedelta(hours=-3))


def _extraer_hora(timestamp_utc: Optional[str]) -> Optional[float]:
    """
    Calcula la hora de día LOCAL (float 0..23.99) desde un timestamp_utc.

    El timestamp_utc está normalizado a UTC; se convierte a Argentina (UTC-3)
    para que el loop use la hora real del viaje.

    Ej: 08:00 local → 8.0; 14:30 → 14.5.

    Args:
        timestamp_utc: valor de media.timestamp_utc.

    Returns:
        Hora en fracción, o None si no hay timestamp válido.
    """
    dt = _parsear_timestamp(timestamp_utc)
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(_ZONA_ARGENTINA)
    return dt.hour + dt.minute / 60.0 + dt.second / 3600.0


# ── Rango horario del filtro duro ────────────────────────────────────────────


def _rango_de_horas(horas: list[int]) -> tuple[int, int, str]:
    """
    Determina el rango horario del FILTRO DURO [hmin, hmax], ambas puntas
    inclusive, en hora local.

    Regla de ajuste con predominio:
        - 0 horas elegidas → todo el día [0, 23] (sin restricción horaria).
        - 1 hora h        → [h, h] (esa hora en punto exacta).
        - 2+ horas        → [min(horas), max(horas)].

    Es una decisión INDEPENDIENTE de los segmentos del loop (ver `generar_loop`):
    los segmentos requieren ≥2 horas y se completan a 0..23 cuando hace falta,
    pero el rango de filtrado respeta la cantidad real de horas elegidas.

    Args:
        horas: horas elegidas por el usuario (en el orden de la ráfaga).

    Returns:
        (hmin, hmax, nota): extremos inclusivos y una nota corta de legibilidad.
    """
    horas_limpias = [int(h) for h in horas if h is not None]
    if not horas_limpias:
        return (0, 23, "sin horas → todo el día (0-23)")
    if len(horas_limpias) == 1:
        h = horas_limpias[0]
        return (h, h, f"1 hora → {h} exacta")
    hmin, hmax = min(horas_limpias), max(horas_limpias)
    return (hmin, hmax, f"rango {hmin}-{hmax}")


# ── Consultas a DB ───────────────────────────────────────────────────────────


def _consultar_metadata(conn: sqlite3.Connection,
                         media_ids: list[int]) -> dict[int, dict[str, str]]:
    """
    Recupera las duplas de metadata de interés para una lista de media_ids.

    Args:
        conn: conexión SQLite (row_factory = sqlite3.Row).
        media_ids: ids de medios.

    Returns:
        {media_id: {clave: valor}}.
    """
    if not media_ids:
        return {}
    marcadores = ",".join("?" * len(media_ids))
    claves_ph = ",".join("?" * len(CLAVES_METADATA))
    filas = conn.execute(
        f"SELECT media_id, key, value FROM media_metadata "
        f"WHERE media_id IN ({marcadores}) AND key IN ({claves_ph})",
        [*media_ids, *CLAVES_METADATA],
    ).fetchall()
    resultado: dict[int, dict[str, str]] = {mid: {} for mid in media_ids}
    for fila in filas:
        resultado[fila["media_id"]][fila["key"]] = fila["value"]
    return resultado


def _filtrar_media(conn: sqlite3.Connection,
                    filtrar: dict,
                    rango_horas: Optional[tuple[int, int]] = None) -> list[sqlite3.Row]:
    """
    Filtrado DURO de medios de `media` (+ `media_metadata`).

    Evalúa en SQL (AND) los criterios que descartan medios:
        - municipios: `m.municipio IN (...)` cuando vienen elegidos.
        - días y clima (legado): EXISTS sobre `media_metadata`.
        - tags: EXISTS sobre `media_metadata` cuando vienen elegidas
          (OR de LIKE `%tag%` sobre las 5 fuentes de keywords; un medio pasa
          si contiene ALGUNA de las tags).
    El rango horario se aplica en Python por `_extraer_hora` (hora local) y
    descarta lo que queda fuera de [hmin, hmax] (ambos inclusive) o sin hora.

    Los COLORES NO se aplican aquí (prioridad: `_calcular_score`). Las TAGS,
    en cambio, SON filtro duro cuando se eligen — con fallback a prioridad si
    el arco queda vacío (ver `generar_loop`).

    Args:
        conn: conexión SQLite.
        filtrar: dict opcional: municipios, colores, tags, días, clima
                 (tags = filtro duro; colores = solo prioridad).
        rango_horas: (hmin, hmax) del filtro horario o None (sin restricción).

    Returns:
        Lista de filas (sqlite3.Row) de `media` que cumplen los filtros duros.
    """
    base = """
        SELECT m.id AS media_id, m.type AS tipo, m.subtype,
               m.filename_original, m.filepath_absoluto AS ruta,
               m.duration_secs, m.latitude AS lat, m.longitude AS lon,
               m.municipio, m.provincia, m.departamento,
               m.color_1_name_basic AS color,
               m.color_2_name_basic AS color_2,
               m.color_3_name_basic AS color_3,
               m.author, m.cumul_distance_m, m.sun_elevation,
               m.secs_since_noon, m.twilight_period, m.timestamp_utc
        FROM media m
    """
    condiciones: list[str] = []

    municipios = filtrar.get("municipios") or []
    dias = filtrar.get("dias") or []
    clima = filtrar.get("clima") or []

    # municipios: AND, IN (...)
    cond_media: list[str] = []
    params_media: list[Any] = []

    if municipios:
        cond_media.append(f"m.municipio IN ({','.join('?' * len(municipios))})")
        params_media.extend(municipios)

    # Colores: NO condicionan (prioridad de score, no filtro).
    # Tags: FILTRO DURO cuando se eligen (OR de LIKE sobre las 5 fuentes de
    # keywords). Un medio pasa si contiene ALGUNA de las tags elegidas.

    # Días: EXISTS sobre dia_semana (legado).
    # Clima: EXISTS sobre weather_label del medio (legado).
    cond_meta: list[str] = []
    params_meta: list[Any] = []

    tags = [t for t in (filtrar.get("tags") or []) if t and t.strip()]
    if tags:
        marcadores_claves = ",".join("?" * len(CLAVES_TAGS_LOOP))
        likes = " OR ".join(["md_t.value LIKE ?"] * len(tags))
        cond_meta.append(
            "EXISTS (SELECT 1 FROM media_metadata md_t "
            "WHERE md_t.media_id = m.id "
            f"AND md_t.key IN ({marcadores_claves}) "
            f"AND ({likes}))")
        params_meta.extend(CLAVES_TAGS_LOOP)
        params_meta.extend([f"%{t}%" for t in tags])

    if dias:
        cond_meta.append(
            "EXISTS (SELECT 1 FROM media_metadata md_d "
            "WHERE md_d.media_id = m.id AND md_d.key = 'dia_semana' "
            f"AND md_d.value IN ({','.join('?' * len(dias))}))")
        params_meta.extend(dias)

    if clima:
        cond_meta.append(
            "EXISTS (SELECT 1 FROM media_metadata md_c "
            "WHERE md_c.media_id = m.id AND md_c.key = 'weather_label' "
            f"AND md_c.value IN ({','.join('?' * len(clima))}))")
        params_meta.extend(clima)

    condiciones = cond_media + cond_meta
    params = params_media + params_meta

    query = base
    if condiciones:
        query += " WHERE " + " AND ".join(condiciones)
    query += " ORDER BY m.id"
    filas = conn.execute(query, params).fetchall()

    # Rango horario flexible (hora LOCAL) — no se puede hacer en SQL limpio
    # por la conversión UTC->Argentina, así que se filtra con _extraer_hora.
    # Los textos sin timestamp (puntos curados) no se descartan aquí: usan la
    # hora por defecto (HORA_DEFECTO_TEXTO) para pasar por el mismo filtro.
    if rango_horas is not None:
        hmin, hmax = rango_horas
        dentro: list[sqlite3.Row] = []
        for fila in filas:
            hora = _extraer_hora(fila["timestamp_utc"])
            if hora is None:
                if fila["tipo"] == "text":
                    hora = HORA_DEFECTO_TEXTO
                else:
                    continue
            if hmin <= hora <= hmax:
                dentro.append(fila)
        filas = dentro

    return filas


def _calcular_score(fila: sqlite3.Row,
                    metadata: dict[str, str],
                    colores: list[str],
                    tags: list[str]) -> int:
    """
    Puntúa la afinidad del medio con las prioridades elegidas (nunca descarta).

    Score = n_colores_coinciden + n_etiquetas_coinciden.
      - Color: cuenta cuántos de los 3 slots dominantes (color_1/2/3_name_basic)
        coinciden (case-insensitive) con algún color elegido. Solo las
        imágenes factualmente lo tienen; videos/audios/textos → 0.
      - Etiquetas: +1 por etiqueta elegida que aparezca (substring,
        case-insensitive, LIKE '%tag%') en el valor de `ia_keywords`.

    Args:
        fila: fila sqlite3.Row de `media`.
        metadata: metadata del medio ({clave: valor, ...}).
        colores: colores elegidos (lista cruda).
        tags: etiquetas elegidas (lista cruda).

    Returns:
        Puntaje entero >= 0.
    """
    score = 0
    base_colores = {c.strip().lower() for c in (colores or []) if c and c.strip()}
    base_tags = [t.strip().lower() for t in (tags or []) if t and t.strip()]

    if fila["tipo"] == "image" and base_colores:
        for slot in ("color", "color_2", "color_3"):
            valor = fila[slot]
            if valor and str(valor).strip().lower() in base_colores:
                score += 1

    if base_tags:
        texto_ia = (metadata.get("ia_keywords") or "").lower()
        for t in base_tags:
            if t in texto_ia:
                score += 1

    return score


def _flotante(val: Optional[str]) -> Optional[float]:
    """Convierte un valor a float, o None si no es numérico."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _parse_tags(valor: Optional[str]) -> list[str]:
    """Divide keywords coma-separadas en lista limpia."""
    if not valor:
        return []
    return [t.strip() for t in valor.split(",") if t.strip()]


# ── Selección ordenada de candidatos ─────────────────────────────────────────


def _seleccionar(conn: sqlite3.Connection,
                 filtrar: dict,
                 rango_horas: Optional[tuple[int, int]] = None) -> list[dict]:
    """
    Combina filtros duros, prioridades y el orden de salida final.

    Pasos:
        1. `_filtrar_media`: municipios (+ rangos horario/días/clima).
        2. `_calcular_score`: prioridad colores + etiquetas por medio.
        3. Orden: hora local ascendente, desempate por score desc y id asc.

    Args:
        conn: conexión SQLite.
        filtrar: dict de filtros/prioridades (municipios/colores/tags/dias/clima).
        rango_horas: (hmin, hmax) del filtro horario o None (todo el día).

    Returns:
        Lista de dicts {"fila": row, "hora": float|None, "score": int, "meta": dict}.
    """
    filas = _filtrar_media(conn, filtrar, rango_horas)
    media_ids = [f["media_id"] for f in filas] if filas else []
    metadata = _consultar_metadata(conn, media_ids)

    colores = [c for c in (filtrar.get("colores") or []) if c and c.strip()]
    tags = [t for t in (filtrar.get("tags") or []) if t and t.strip()]

    items: list[dict] = []
    for fila in filas:
        hora = _extraer_hora(fila["timestamp_utc"])
        if hora is None:
            if fila["tipo"] == "text":
                hora = HORA_DEFECTO_TEXTO
            else:
                continue
        mid = fila["media_id"]
        meta = metadata.get(mid, {})
        score = _calcular_score(fila, meta, colores, tags)
        items.append({"fila": fila, "hora": hora, "score": score, "meta": meta})

    # Orden de salida: por hora local asc; desempate por score desc e id asc.
    items.sort(key=lambda it: (it["hora"], -it["score"], it["fila"]["media_id"] or 0))
    return items


# ── Chiches (eventos ambientales) ────────────────────────────────────────────


def _chiches_de_medios(campos: dict) -> list[tuple[str, str]]:
    """
    Evalúa condiciones ambientales de un medio y devuelve chiches activos.

    Retorna lista de (familia, texto). La familia es clave estable para
    dedup/sostenimiento; el texto es la variante true-random con peso.
    """
    pares: list[tuple[str, str]] = []

    elev = campos.get("sun_elevation")
    if elev is not None and ELEVACION_ALBA_BAJA <= elev <= ELEVACION_ALBA_ALTA:
        pares.append(("alba", "Salió el sol"))

    ssn = campos.get("secs_since_noon")
    if ssn is not None and abs(ssn) <= MEDIODIA_UMBRAL_SEG:
        pares.append(("mediodia", "Es el mediodía"))

    temp = campos.get("weather_temp_c")
    if temp is not None:
        if temp > TEMP_CALOR:
            pares.append(("calor", _elegir_variante("calor", "Hace calor")))
        elif temp < TEMP_FRIO:
            pares.append(("frio", "Hace frío"))

    # Se resuelven retrocompatibles via sostenimiento fuera; se mantienen aquí
    # para casos sin sostenimiento (compat legacy: se filtran luego).
    viento = campos.get("weather_wind_speed_kmh")
    if viento is not None and viento > VIENTO_ALTO:
        pares.append(("viento", _elegir_variante("viento", "Hay mucho viento")))

    precip = campos.get("weather_precip_mm")
    if precip is not None and precip > PRECIP_LLUVIA:
        pares.append(("lluvia", _elegir_variante("lluvia", "Está lloviendo")))

    cloud = campos.get("weather_cloud_pct")
    code = campos.get("weather_code")
    # Nublado / despejado por cloud_pct, con fallback por WMO code
    hay_nublado = False
    hay_despejado = False
    if cloud is not None:
        if cloud >= NUBE_NUBLADO:
            hay_nublado = True
        elif cloud <= NUBE_DESPEJADO:
            hay_despejado = True
    if not hay_nublado and not hay_despejado and code is not None:
        try:
            ci = int(float(code))
            if ci in (3,):
                hay_nublado = True
            elif ci in (0, 1):
                hay_despejado = True
        except (ValueError, TypeError):
            pass
    if hay_nublado:
        pares.append(("nublado", "Está nublado"))
    elif hay_despejado:
        pares.append(("despejado", "Cielo despejado"))

    # Sol fuerte: despejado + calor + elevación alta + día
    if hay_despejado and temp is not None and temp > TEMP_SOL and elev is not None and elev > ELEVACION_SOL:
        twi = campos.get("twilight_period") or ""
        if twi == "dia" or not twi:
            # si no hay twilight, igual considerar día si elev > 5
            if twi == "dia" or elev > 5:
                pares.append(("sol", _elegir_variante("sol", "Pega el sol")))

    twi2 = campos.get("twilight_period") or ""
    if twi2 in NOCHE_PERIODOS:
        pares.append(("noche", "Es la noche"))

    return pares


# Familias que requieren sostenimiento (≥ SOSTEN_MIN consecutivos)
FAMILIAS_SOSTENIDAS = {"viento", "lluvia", "nublado", "despejado", "sol"}


# ── Generación del loop ──────────────────────────────────────────────────────


def generar_loop(
    db_path: str,
    horas: list[int],
    loop_secs: float = 300.0,
    modalidad_ubicaciones: str = "geo",
    filtros: Optional[dict] = None,
    salida: Optional[str] = None,
) -> dict:
    """
    Genera el spec JSON del loop a partir de las elecciones (solo lectura).

    Modelo de selección (rediseño 2026-08):
      Filtros DUROS: rango horario [min(horas), max(horas)] (hora local,
      puntas incluidas) + municipios (+ días/clima legado) + TAGS (cuando se
      eligen: OR de LIKE `%tag%` sobre las 5 fuentes de keywords; un medio
      pasa si contiene ALGUNA). Si el arco queda con menos de
      `MIN_MEDIOS_FALLBACK_TAGS`, FALLBACK a prioridad (se ignoran las tags y
      se rellena con todo el filtro base; nunca vacío) y se anota en el resumen.
      Prioridades (NO descartan): color (solo fotos) y etiquetas → suman al
      `score` de cada medio.
      Orden de salida: por hora local asc; desempate entre la misma hora por
      el mejor `score` (los más relevantes adelante).
      keypoint = `t_loop` (instante del loop donde el medio es prot) — lo
      produce `loop_engine.armar_spec` y se replica en `keypoint` por medio.

    Args:
        db_path: ruta a la base de datos.
        horas: horas elegidas en el OS ORDEN de la ráfaga (sin normalizar).
        loop_secs: duración del loop (default 300 s).
        modalidad_ubicaciones: se mantiene en la firma por compat; el orden
            definitivo lo fija el modelo hora+score (§ "Orden").
        filtros: dict opcional: municipios/colores/tags/dias/clima/ideas.
        salida: ruta de archivo JSON opcional para volcar el spec.

    Returns:
        Spec dict: loop_secs, segmentos, medios, chiches, por_tipo, resumen.
            - `medios`: lista única (mismo formato que antes, cada uno con su
              `t_loop`, `seg_i`, `score` y `keypoint`).
            - `por_tipo`: {image: [...], video: [...], audio: [...], text: [...]}
              con el mismo schema de cada item de `medios`.
            - `resumen`: {total, image, video, audio, text, rango_horas,
              filtros, notas}. `filtros` expone lo que eligió el usuario
              (horas, municipios, colores, tags, dias, clima) para que el
              puente y TD lo reflejen en el estado del loop.

    Raises:
        FileNotFoundError: si la DB no existe.
    """
    # 1. Normalizar horas: rango del filtro + horas para los segment del loop.
    horas_raw = [int(h) for h in horas if h is not None]
    hmin, hmax, nota_horas = _rango_de_horas(horas_raw)
    # Los segmentos necesitan al menos 2 horas; con menos se completa al día.
    horas_norm = horas_raw if len(horas_raw) >= 2 else list(range(0, 24))
    if len(horas_raw) < 2:
        log.info("  Menos de 2 horas → segmentos de todo el día (0..23).")
    log.info("  Rango horario del filtro duro: [%d-%d] (%s)", hmin, hmax, nota_horas)

    filtros_ = filtros or {}
    if filtros_.get("ideas"):
        log.warning("  Filtro 'ideas' documentado pero NO implementado "
                    "(requiere embeddings semánticos).")

    # 2. Segmentos del motor puro (validación temprana)
    loop_engine.calcular_segmentos(horas_norm, loop_secs)

    conn = abrir(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tags_elegidas = [t for t in (filtros_.get("tags") or []) if t and t.strip()]
        fallback_tags = False

        if tags_elegidas:
            # Filtro DURO por tags: solo medios que contengan alguna tag.
            items = _seleccionar(conn, filtros_, (hmin, hmax))
            if len(items) < MIN_MEDIOS_FALLBACK_TAGS:
                # Fallback a prioridad: el arco quedó sin medios con las tags;
                # se rellena con el filtro base + score (nunca vacío). El aviso
                # de insuficiencia ("no hay suficientes medios seleccionados")
                # queda pendiente de reemplazar este fallback.
                log.warning(
                    "  Fallback de tags: solo %d medio(s) con las tags en el "
                    "arco (mínimo %d); se re-genera sin filtro de tags "
                    "(prioridad).", len(items), MIN_MEDIOS_FALLBACK_TAGS)
                filtros_sin_tags = {k: v for k, v in filtros_.items()
                                    if k != "tags"}
                items = _seleccionar(conn, filtros_sin_tags, (hmin, hmax))
                fallback_tags = True
        else:
            items = _seleccionar(conn, filtros_, (hmin, hmax))

        # 3+4+5. Armar medios (con score) y chiches (consolidados).
        medios: list[dict] = []
        chiches: list[dict] = []
        # Dedup por (familia, hora ENTERA) para no disparar por cada medio;
        # sostenimiento ≥ SOSTEN_MIN para mitigar ruido horario Open-Meteo.
        chiches_vistos: set[tuple[str, int]] = set()
        racha: dict[str, int] = {}
        for it in items:
            fila = it["fila"]
            hora = it["hora"]
            score = it["score"]
            meta = it["meta"]
            mid = fila["media_id"]

            tags_text = meta.get("ia_keywords_texto") if fila["tipo"] == "text" else None
            medio = {
                "media_id": mid,
                "tipo": fila["tipo"],
                "es_360": fila["tipo"] == "video" and (fila["subtype"] == "360"),
                "ruta": fila["ruta"],
                "hora": hora,
                "duracion": fila["duration_secs"] or 0.0,
                "municipio": fila["municipio"],
                "provincia": fila["provincia"],
                "departamento": fila["departamento"],
                "color": fila["color"],
                "tags": _parse_tags(tags_text or meta.get("ia_keywords")),
                "desc": (meta.get("texto_completo") or meta.get("ia_description", "")) if fila["tipo"] == "text" else meta.get("ia_description", ""),
                "titulo": meta.get("titulo_seccion", ""),
                "ubicacion": (
                    {"lat": fila["lat"], "lon": fila["lon"]}
                    if fila["lat"] is not None else None),
                "clima": {
                    "temp_c": _flotante(meta.get("weather_temp_c")),
                    "viento_kmh": _flotante(meta.get("weather_wind_speed_kmh")),
                    "precip_mm": _flotante(meta.get("weather_precip_mm")),
                    "cloud_pct": _flotante(meta.get("weather_cloud_pct")),
                    "code": _flotante(meta.get("weather_code")),
                    "etiqueta": meta.get("weather_label", ""),
                },
                "score": score,
            }
            medios.append(medio)

            # Chiches ambientales (weather + astronomía + geo)
            campos = {
                "sun_elevation": fila["sun_elevation"],
                "secs_since_noon": fila["secs_since_noon"],
                "twilight_period": fila["twilight_period"],
                "weather_temp_c": _flotante(meta.get("weather_temp_c")),
                "weather_wind_speed_kmh": _flotante(meta.get("weather_wind_speed_kmh")),
                "weather_precip_mm": _flotante(meta.get("weather_precip_mm")),
                "weather_cloud_pct": _flotante(meta.get("weather_cloud_pct")),
                "weather_code": meta.get("weather_code"),
            }
            hora_entera = int(hora)
            ubic = medio["ubicacion"]
            # Pares (familia, texto) del medio actual
            pares = _chiches_de_medios(campos)
            familias_activas = {fam for fam, _ in pares}
            # Actualizar rachas para familias sostenidas
            for fam in FAMILIAS_SOSTENIDAS:
                if fam in familias_activas:
                    racha[fam] = racha.get(fam, 0) + 1
                else:
                    racha[fam] = 0

            def _emitir(familia: str, texto: str) -> None:
                clave = (familia, hora_entera)
                if clave in chiches_vistos:
                    return
                chiches_vistos.add(clave)
                chiches.append({
                    "hora": hora,
                    "texto": texto,
                    "familia": familia,
                    "ubicacion": ubic,
                    "municipio": fila["municipio"],
                    "provincia": fila["provincia"],
                    "departamento": fila["departamento"],
                    "lat": fila["lat"],
                    "lon": fila["lon"],
                })

            for familia, texto in pares:
                if familia in FAMILIAS_SOSTENIDAS:
                    if racha.get(familia, 0) >= SOSTEN_MIN:
                        _emitir(familia, texto)
                else:
                    _emitir(familia, texto)
        # Geo chiches: ingresos/egresos provincia/departamento/municipio
        # Se detectan en orden GEOGRÁFICO (cumul_distance_m) no en orden horario,
        # para que el viaje BsAs→Tucumán no parezca zigzag por la hora del día.
        # Se posicionan igual por hora (t_loop), pero el cambio se detecta por ruta.
        def _orden_geo(it: dict) -> float:
            cd = it["fila"]["cumul_distance_m"]
            if cd is not None:
                try:
                    return float(cd)
                except (TypeError, ValueError):
                    pass
            # fallback: timestamp_utc
            ts = it["fila"]["timestamp_utc"]
            dt = _parsear_timestamp(ts) if ts else None
            if dt is not None:
                return dt.timestamp()
            return float(it["fila"]["media_id"] or 0)

        items_geo = sorted(items, key=_orden_geo)
        prev_provincia: Optional[str] = None
        prev_departamento: Optional[str] = None
        prev_municipio: Optional[str] = None
        for it in items_geo:
            fila = it["fila"]
            hora = it["hora"]
            hora_entera = int(hora)
            ubic_geo = {"lat": fila["lat"], "lon": fila["lon"]} if fila["lat"] is not None else None
            cur_prov = str(fila["provincia"] or "").strip() or None
            cur_depto = str(fila["departamento"] or "").strip() or None
            cur_muni = str(fila["municipio"] or "").strip() or None

            def _geo_emit_geo(prev_val: Optional[str], cur_val: Optional[str], fam_pref: str):
                if not cur_val or not cur_val.strip():
                    return
                cur_val = cur_val.strip()
                if prev_val is None:
                    texto = f"Entramos a {cur_val}"
                    clave = (texto, hora_entera)
                    if clave not in chiches_vistos:
                        chiches_vistos.add(clave)
                        chiches.append({
                            "hora": hora, "texto": texto,
                            "familia": f"{fam_pref}_entra",
                            "ubicacion": ubic_geo,
                            "municipio": fila["municipio"],
                            "provincia": fila["provincia"],
                            "departamento": fila["departamento"],
                            "lat": fila["lat"], "lon": fila["lon"],
                        })
                elif cur_val != prev_val:
                    for fam, txt in [(f"{fam_pref}_sale", f"Salimos de {prev_val}"),
                                     (f"{fam_pref}_entra", f"Entramos a {cur_val}")]:
                        clave2 = (txt, hora_entera)
                        if clave2 not in chiches_vistos:
                            chiches_vistos.add(clave2)
                            chiches.append({
                                "hora": hora, "texto": txt,
                                "familia": fam,
                                "ubicacion": ubic_geo,
                                "municipio": fila["municipio"],
                                "provincia": fila["provincia"],
                                "departamento": fila["departamento"],
                                "lat": fila["lat"], "lon": fila["lon"],
                            })
            _geo_emit_geo(prev_provincia, cur_prov, "provincia")
            _geo_emit_geo(prev_departamento, cur_depto, "depto")
            _geo_emit_geo(prev_municipio, cur_muni, "municipio")
            if cur_prov:
                prev_provincia = cur_prov
            if cur_depto:
                prev_departamento = cur_depto
            if cur_muni:
                prev_municipio = cur_muni
    finally:
        conn.close()

    # 6+7. armar_spec (posiciona los que caen en el arco y agrega t_loop).
    spec = loop_engine.armar_spec(horas_norm, loop_secs, medios, chiches)

    # keypoint: replicar t_loop en cada medio posicionado (ubicación temporal
    # dentro del loop, que ya calculó loop_engine.armar_spec).
    for m in spec["medios"]:
        m["keypoint"] = m["t_loop"]

    # por_tipo: una lista por tipo de medio.
    por_tipo: dict[str, list[dict]] = {t: [] for t in TIPOS_POR_DEFECTO}
    for m in spec["medios"]:
        tipo = m.get("tipo") or "otros"
        if tipo in por_tipo:
            por_tipo[tipo].append(m)
        else:
            por_tipo.setdefault("otros", []).append(m)
    spec["por_tipo"] = por_tipo

    # resumen (útil para debug y para el puente).
    conteo = Counter(m.get("tipo") or "otros" for m in spec["medios"])
    notas = [nota_horas]
    municipios = filtros_.get("municipios") or []
    colores = [c for c in (filtros_.get("colores") or []) if c.strip()]
    tags = [t for t in (filtros_.get("tags") or []) if t.strip()]
    dias = filtros_.get("dias") or []
    clima = filtros_.get("clima") or []

    if municipios:
        notas.append(f"municipios: {len(municipios)} → {', '.join(municipios)}")
    else:
        notas.append("municipios: sin restricción")
    if colores:
        notas.append(f"prioridad colores: {', '.join(colores)} (no filtra)")
    else:
        notas.append("colores: sin prioridad")
    if tags:
        if fallback_tags:
            notas.append(
                f"tags: {'; '.join(tags)} (elegidas pero sin medios en el arco → "
                "FALLBACK a prioridad)")
        else:
            notas.append(f"filtro duro tags: {'; '.join(tags)} (solo medios con alguna)")
    else:
        notas.append("tags: sin prioridad")
    if dias:
        notas.append(f"filtro día: {', '.join(dias)}")
    if clima:
        notas.append(f"filtro clima: {', '.join(clima)}")
    if not spec["medios"]:
        notas.append("no hay medios dentro del rango con los filtros duros")

    resumen = {
        "total": len(spec["medios"]),
        "image": conteo.get("image", 0),
        "video": conteo.get("video", 0),
        "audio": conteo.get("audio", 0),
        "text": conteo.get("text", 0),
        "rango_horas": [hmin, hmax],
        "filtros": {
            "horas": horas_raw,
            "municipios": municipios,
            "colores": colores,
            "tags": tags,
            "dias": dias,
            "clima": clima,
        },
        "notas": notas,
    }
    if conteo.get("otros"):
        resumen["otros"] = conteo["otros"]
    spec["resumen"] = resumen

    if salida:
        with open(salida, "w", encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False, indent=2)
        log.info("  Spec escrita en: %s", salida)

    return spec


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Genera el spec JSON del motor de loop de Flujos a partir de "
                    "las elecciones (horas, municipios, colores, tags, días, clima). ",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--horas", nargs="+", type=int, default=None,
                        help="Horas elegidas en orden (ej: 6 13 9 18). Sin horas → "
                             "todo el día; 1 hora → esa hora; 2+ → rango [min,max].")
    parser.add_argument("--loop-secs", type=float, default=300.0,
                        help="Duración del loop en segundos (default: 300).")
    parser.add_argument("--municipios", default=None,
                        help="Municipios separados por coma (filtro duro).")
    parser.add_argument("--colores", default=None,
                        help="Colores básicos separados por coma (prioridad, no filtra).")
    parser.add_argument("--tags", default=None,
                        help="Etiquetas separadas por ';' (prioridad, no filtra).")
    parser.add_argument("--dias", default=None,
                        help="Días separados por coma (ej: 'lunes,martes').")
    parser.add_argument("--clima", default=None,
                        help="Etiquetas de clima separadas por coma.")
    parser.add_argument("--modalidad", default="geo", choices=["geo", "eleccion"],
                        help="Orden de medios (el orden definitivo es hora+score).")
    parser.add_argument("--db", default=None, help="Ruta a la DB (default: db/flujos.db).")
    parser.add_argument("--salida", default=None,
                        help="Ruta de archivo JSON donde volcar el spec.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Previsualiza rango/cantidades/score medio sin escribir nada.")
    parser.add_argument("--por-tipo", action="store_true",
                        help="Imprime el resumen por tipo (JSON) en consola.")
    parser.add_argument("--verbose", action="store_true", help="Log detallado.")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    db_path = resolver_db(args.db)
    if not os.path.isfile(db_path):
        log.error("  No existe la DB: %s", db_path)
        sys.exit(1)

    horas = [int(h) for h in args.horas] if args.horas else []
    filtros: dict[str, Any] = {}
    if args.municipios:
        filtros["municipios"] = [s.strip() for s in args.municipios.split(",") if s.strip()]
    if args.colores:
        filtros["colores"] = [s.strip() for s in args.colores.split(",") if s.strip()]
    if args.tags:
        filtros["tags"] = [s.strip() for s in args.tags.split(";") if s.strip()]
    if args.dias:
        filtros["dias"] = [s.strip() for s in args.dias.split(",") if s.strip()]
    if args.clima:
        filtros["clima"] = [s.strip() for s in args.clima.split(",") if s.strip()]

    horas_display = horas if len(horas) >= 2 else list(range(0, 24))
    hmin, hmax, rango_nota = _rango_de_horas(horas)

    print("\n  ── Motor de loop ───────────────────────────────")
    print(f"  DB:    {db_path}")
    print(f"  Horas: {horas_display}")
    print(f"  Rango del filtro duro: {hmin} - {hmax}  ({rango_nota})")
    print(f"  Loop:  {args.loop_secs}s | modalidad: {args.modalidad}")
    if filtros:
        for key, val in list(filtros.items()):
            tipo = "filtro" if key in ("municipios", "dias", "clima") else "prioridad"
            print(f"  {key} ({tipo}): {val}")
    segs = loop_engine.calcular_segmentos(horas_display, args.loop_secs)
    print(f"  Segmentos: {len(segs)}  (arco total = {sum(s['arco_horas'] for s in segs):.1f}h)")

    if args.dry_run:
        conn = abrir(db_path)
        conn.row_factory = sqlite3.Row
        try:
            items = _seleccionar(conn, filtros, (hmin, hmax))
        finally:
            conn.close()
        conteo = Counter(it["fila"]["tipo"] for it in items)
        n = len(items)
        avg = (sum(it["score"] for it in items) / n) if n else 0.0
        sin_prioridad = sum(1 for it in items if it["score"] == 0)
        print("\n  [DRY-RUN] Sin escribir ningún archivo:")
        print(f"  Rango horario: {hmin}-{hmax}  ({rango_nota})")
        print(f"  Candidatos: image={conteo.get('image',0)} | video={conteo.get('video',0)} "
              f"| audio={conteo.get('audio',0)} | text={conteo.get('text',0)} | total={n}")
        print(f"  Score medio: {avg:.2f}   (sin prioridad: {sin_prioridad}/{n})")
        return

    spec = generar_loop(
        db_path=db_path,
        horas=horas,
        loop_secs=args.loop_secs,
        modalidad_ubicaciones=args.modalidad,
        filtros=filtros,
        salida=args.salida,
    )

    n_medios = len(spec["medios"])
    n_chiches = len(spec["chiches"])
    print(f"\n  Medios posicionados: {n_medios}")
    print(f"  Chiches generados:   {n_chiches}")
    if args.por_tipo:
        print("\n  Resumen por tipo:")
        print(json.dumps(spec["resumen"], ensure_ascii=False, indent=2))
    print("\n  Segmentos:")
    for s in spec["segmentos"]:
        print(f"    seg {s['i']}: {s['from']:>4.0f}h → {s['to']:>4.0f}h "
              f"(arco {s['arco_horas']:.1f}h) t=[{s['t_start']:.1f}..{s['t_end']:.1f}]s")
    print(f"\n  Spec completa: {args.salida or '(en memoria)'}")
    if args.salida:
        print(f"  ✔ Guardado en: {args.salida}")


if __name__ == "__main__":
    main()