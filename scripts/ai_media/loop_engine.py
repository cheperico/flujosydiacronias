#!/usr/bin/env python3
"""
loop_engine.py — Núcleo matemático del motor de loop de la instalación Flujos.

Módulo PURO (sin DB ni renderizador): implementa el cálculo de segmentos
horarios, la posición de un medio en el tiempo del loop según su hora de día,
y el armado de la spec JSON agnóstica del renderizador (web / TouchDesigner).

La matemática sigue al pie de la letra `docs/motor_loop.md` §3:

    - N horas elegidas → N−1 segmentos de duración igual (loop_secs / (N−1)).
    - Cada segmento va de H[i] → H[i+1] sobre un reloj de 24h.
    - Un segmento "cruza medianoche" cuando H[i+1] <= H[i]: su arco horario
      es 24 + (H[i+1] - H[i]).
    - La fracción del arco donde cae la hora h de un medio define su t_loop:
      t_loop = t_inicio_seg + frac * duracion_seg.

Este módulo NO importa sqlite ni web. Es 100% testeable en aislamiento.

Uso (desde otros scripts):
    from scripts.ai_media.loop_engine import armar_spec, posicionar_hora
    spec = armar_spec([7, 16, 13, 18], 300, medios, chiches)

Funciones públicas:
    calcular_segmentos(horas, loop_secs) -> list[dict]
    hora_en_fraccion(hora_h, seg)        -> float | None
    posicionar_hora(hora, segmentos)     -> dict | None
    posicionar_medio(hora, segmentos)    -> dict | None   (alias de posicionar_hora)
    armar_spec(horas, loop_secs, medios, chiches) -> dict
"""

import logging
from typing import Any

log = logging.getLogger(__name__)

# Tolerancia de punto flotante para el chequeo 0 <= frac <= 1
_EPSILON = 1e-6


def calcular_segmentos(horas: list[int], loop_secs: float) -> list[dict]:
    """
    Calcula los N−1 segmentos temporales del loop a partir de N horas elegidas.

    Args:
        horas: Lista de horas (0..23) en el ORDEN de elección del usuario.
               Debe tener al menos 2 elementos.
        loop_secs: Duración total del loop en segundos (> 0).

    Returns:
        Lista de N−1 segmentos, cada uno con:
            i: int              índice del segmento
            from: float        hora inicial del arco (H[i])
            to: float          hora final del arco (H[i+1])
            arco_horas: float  distancia horaria del arco en el reloj
            t_start: float     tiempo del loop donde comienza (seg)
            t_end: float       tiempo del loop donde termina (seg)
            duracion_seg: float  duración del segmento en segundos

    Raises:
        ValueError: si hay menos de 2 horas o loop_secs <= 0.
    """
    if len(horas) < 2:
        raise ValueError(
            f"Se necesitan al menos 2 horas para formar segmentos "
            f"(recibidas: {len(horas)}).")
    if loop_secs <= 0:
        raise ValueError(f"loop_secs debe ser positivo (recibido: {loop_secs}).")

    n_seg = len(horas) - 1
    duracion_seg = loop_secs / n_seg

    segmentos: list[dict] = []
    for i in range(n_seg):
        h_de = float(horas[i])
        h_a = float(horas[i + 1])
        # Cruza medianoche cuando la hora final no es mayor que la inicial
        if h_a > h_de:
            arco = h_a - h_de
        else:
            arco = 24.0 + (h_a - h_de)

        t_start = i * duracion_seg
        segmentos.append({
            "i": i,
            "from": h_de,
            "to": h_a,
            "arco_horas": arco,
            "t_start": t_start,
            "t_end": t_start + duracion_seg,
            "duracion_seg": duracion_seg,
        })

    return segmentos


def _cruza_medianoche(seg: dict) -> bool:
    """True si el arco del segmento cruza medianoche (hora final <= inicial)."""
    return seg["to"] <= seg["from"]


def hora_en_fraccion(hora_h: float, seg: dict) -> float | None:
    """
    Devuelve la fracción [0,1] donde cae la hora `hora_h` dentro del arco del
    segmento `seg`, o `None` si la hora no pertenece al arco.

    Para segmentos nocturnos (que cruzan medianoche) la hora se "adelanta"
    24 horas si es menor que la hora inicial del segmento: ej. las 3:00 de la
    madrugada cae en el arco 16→13 como 27 h → frac = (27−16)/21 = 0.52.

    Args:
        hora_h: Hora real del medio (float, 0..23.99).
        seg:    Segmento (dict de `calcular_segmentos`).

    Returns:
        Fracción en [0, 1], o None si la hora no cae dentro del arco.
    """
    if hora_h < 0 or hora_h >= 24:
        return None

    # Si cruza medianoche y estamos "antes" de la hora inicial del segmento,
    # la hora real está en la madrugada del día siguiente → sumar 24.
    h_avance = hora_h
    if hora_h < seg["from"] and _cruza_medianoche(seg):
        h_avance = hora_h + 24.0

    arco = seg["arco_horas"]
    if arco <= 0:
        return None

    frac = (h_avance - seg["from"]) / arco
    if -_EPSILON <= frac <= 1.0 + _EPSILON:
        return max(0.0, min(1.0, frac))
    return None


def posicionar_hora(hora_hora: float, segmentos: list[dict]) -> dict | None:
    """
    Ubica un medio (punto) con hora real `hora_hora` dentro del loop.

    Recorre los segmentos en orden y devuelve la primera coincidencia.

    Args:
        hora_hora: Hora real del medio (float, 0..23.99).
        segmentos: Lista de segmentos (de `calcular_segmentos`).

    Returns:
        dict {"seg_i": int, "t_loop": float} si cae en algún segmento, o None
        si la hora no pertenece a ningún arco.
    """
    for seg in segmentos:
        frac = hora_en_fraccion(hora_hora, seg)
        if frac is None:
            continue
        t_loop = seg["t_start"] + frac * seg["duracion_seg"]
        return {"seg_i": seg["i"], "t_loop": t_loop}
    return None


def posicionar_medio(hora_hora: float, segmentos: list[dict]) -> dict | None:
    """
    Alias público de `posicionar_hora` (nomenclatura de medios).

    Los medios (image, text) ocupan un instante → posición única. Para
    videos/audios la resolución de duración de fragmento se documenta en
    `docs/motor_loop.md` §8 y no se resuelve en este núcleo.

    Args:
        hora_hora: Hora real del medio (float, 0..23.99).
        segmentos: Segmentos del loop.

    Returns:
        {"seg_i": int, "t_loop": float} o None si cae fuera del loop.
    """
    return posicionar_hora(hora_hora, segmentos)


def armar_spec(
    horas: list[int],
    loop_secs: float,
    medios: list[dict],
    chiches: list[dict],
) -> dict:
    """
    Construye el spec JSON del loop.

    Args:
        horas: Horas elegidas en orden de elección (>=2).
        loop_secs: Duración del loop en segundos.
        medios:  Lista de dicts. Cada uno debe tener `hora` (float 0..23.99);
                 el resto son metadatos libres (media_id, tipo, ruta, etc.).
        chiches: Lista de dicts. Cada uno con `hora` y `texto`.

    Returns:
        Dict:
            loop_secs: float
            segmentos: list[dict]
            medios:    list[dict] — los que cayeron en el arco, con `t_loop`
                       y `seg_i` agregados.
            chiches:   list[dict] — los que cayeron, como
                       {"t": t_loop, "tipo": "chiche", "texto": ...}.

    Los elementos que NO caen en ningún segmento se descartan.
    """
    segmentos = calcular_segmentos(horas, loop_secs)

    medios_posicionados: list[dict] = []
    for medio in medios:
        hora = medio.get("hora")
        if hora is None:
            continue
        pos = posicionar_hora(float(hora), segmentos)
        if pos is None:
            continue
        item = dict(medio)
        item["t_loop"] = pos["t_loop"]
        item["seg_i"] = pos["seg_i"]
        medios_posicionados.append(item)

    chiches_posicionados: list[dict] = []
    for chich in chiches:
        hora = chich.get("hora")
        if hora is None:
            continue
        pos = posicionar_hora(float(hora), segmentos)
        if pos is None:
            continue
        item = {
            "t": pos["t_loop"],
            "tipo": "chiche",
            "texto": chich.get("texto", ""),
            "hora": float(hora),
        }
        # Campos opcionales geo (ubicar geográficamente el chiche)
        for k in ("familia", "municipio", "provincia", "departamento", "lat", "lon", "ubicacion"):
            if k in chich and chich[k] is not None:
                item[k] = chich[k]
        chiches_posicionados.append(item)

    return {
        "loop_secs": loop_secs,
        "segmentos": segmentos,
        "medios": medios_posicionados,
        "chiches": chiches_posicionados,
    }


if __name__ == "__main__":
    # Demostración rápida del motor puro
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    horas: list[int] = [7, 16, 13, 18]
    loop: float = 300
    segs = calcular_segmentos(horas, loop)
    log.info("Segmentos para horas=%s loop=%ss:", horas, loop)
    for s in segs:
        log.info("  seg %d: %s→%s arco=%.1fh t=[%.1f..%.1f] (dur=%.1fs)",
                 s["i"], s["from"], s["to"], s["arco_horas"],
                 s["t_start"], s["t_end"], s["duracion_seg"])
    for h in (3, 8, 17, 22):
        p = posicionar_hora(h, segs)
        log.info("  hora %-4.1f → %s", h, p if p else "(fuera del arco)")