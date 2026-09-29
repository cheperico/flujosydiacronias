"""
Análisis visual de videos con IA — detecta cambios de escena + muestreo
inteligente dentro de cada escena.

Algoritmo:
  1. Obtener duración del video (ffprobe)
  2. Detectar cambios de escena (ffmpeg scene detection)
  3. Agrupar los cambios en escenas [inicio, fin)
  4. Subdividir escenas largas (> `--max-duracion-escena`) en sub-escenas
     contiguas (evita que un video continuo de N minutos quede como UNA
     sola escena analizada con una única llamada IA)
  5. Muestrear ~N imágenes distribuidas dentro de cada escena
  6. Seleccionar las mejores por nitidez (sin IA, varianza Laplaciano)
  7. Analizar la imagen más representativa de cada escena con UNA llamada
     de visión (prompt combinado de prompts.yaml: keywords + descripción)
  8. Guardar resultados en DB y/o sidecar

Formato del sidecar .video.json:
    {
        "file_hash": "abc123def456",
        "duracion_seg": 600.0,
        "imgs_por_escena": 10,
        "mejores_por_escena": 3,
        "sensibilidad_escena": 0.4,
        "modelo": "minicpm-v4.6:latest",
        "fecha": "2026-07-14T10:30:00",
        "fotogramas": [
            {"timestamp": 0.0,  "tags": [...], "descripcion": "...", "origen": "escena", "escena": 0},
            ...
        ],
        "escenas": [
            {
                "indice": 0, "inicio": 0.0, "fin": 42.3, "duracion": 42.3,
                "keywords": ["paisaje", "ruta", "campo"],
                "descripcion": "...",
                "fotogramas": [{"timestamp": 0.0, "puntaje_nitidez": 123.4}, ...]
            },
            ...
        ]
    }

Uso:
    # Analizar un video
    python scripts/ai_media/analyze_video.py --file video.mp4

    # Analizar desde DB
    python scripts/ai_media/analyze_video.py --db db/flujos.db

    # Muestreo personalizado por escena
    python scripts/ai_media/analyze_video.py --file video.mp4 --por-escena 10

    # Conservar más/menos imágenes por escena tras la selección por nitidez
    python scripts/ai_media/analyze_video.py --file video.mp4 --mejores-por-escena 5

    # Limitar la duración de cada escena (las más largas se subdividen en
    # sub-escenas contiguas de <= N segundos; útil en videos 360° continuos)
    python scripts/ai_media/analyze_video.py --file video.mp4 --max-duracion-escena 180

    # Solo ver qué se haría
    python scripts/ai_media/analyze_video.py --db db/flujos.db --dry-run
"""

import argparse
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# Permitir importar scripts/ como paquete
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.ai_media.image_analysis import analizar_imagen_completo
from scripts.ai_media.prompts import get_config
from scripts.ai_media.batch_selector import seleccionar_mejores_n

logger = logging.getLogger(__name__)

# Extensiones de video
EXT_VIDEO = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".mxf", ".mts", ".m2ts"}

# Claves en media_metadata
KEY_VIDEO_ANALYSIS = "video_analysis"

# Modelos de visión recomendados (default efectivo: prompts.yaml vision.combinado)
MODELOS_RECOMENDADOS = ["minicpm-v4.6:latest", "qwen2.5vl:latest", "qwen2.5vl:3b",
                        "llama3.2-vision:latest", "gemma4:e4b"]

# Sensibilidad por defecto para scene detection (0.0 - 1.0, menor = más sensible)
SENSIBILIDAD_ESCENA = 0.4


def _modelo_efectivo(modelo: str | None) -> str:
    """Modelo de visión efectivo (explícito > prompts.yaml vision.combinado)."""
    return modelo or get_config("vision.combinado")["modelo"]


# ═══════════════════════════════════════════════════════════════
#  UTILIDADES
# ═══════════════════════════════════════════════════════════════

def _hash_rapido(ruta: str) -> str:
    """Fingerprint rápido: tamaño + fecha modificación."""
    import hashlib
    stat = Path(ruta).stat()
    h = hashlib.md5()
    h.update(str(stat.st_size).encode())
    h.update(str(stat.st_mtime).encode())
    return h.hexdigest()[:12]


def _es_video(ruta: str) -> bool:
    """Verifica si un archivo es video por extensión."""
    return Path(ruta).suffix.lower() in EXT_VIDEO


def _duracion_video(ruta: str) -> float:
    """Obtiene la duración en segundos usando ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        ruta,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return float(result.stdout.strip())
    except Exception as e:
        raise RuntimeError(f"Error obteniendo duración de {ruta}: {e}")


# ═══════════════════════════════════════════════════════════════
#  SCENE DETECTION (ffmpeg)
# ═══════════════════════════════════════════════════════════════

def _detectar_cambios_escena(
    ruta: str,
    sensibilidad: float = SENSIBILIDAD_ESCENA,
) -> list[float]:
    """
    Detecta cambios de escena en un video usando ffmpeg.

    Args:
        ruta: Ruta al video.
        sensibilidad: Threshold de scene detection (0.0-1.0).
                      Menor valor = detecta cambios más sutiles.

    Returns:
        Lista de timestamps (segundos) donde ocurren cambios de escena.
    """
    cmd = [
        "ffmpeg",
        "-i", ruta,
        "-filter:v", f"select='gt(scene,{sensibilidad})',showinfo",
        "-f", "null", "-",
    ]

    logger.info("  Detectando cambios de escena (sensibilidad=%.2f)...", sensibilidad)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=3600,  # 1 hora máx para videos muy largos
        )
    except subprocess.TimeoutExpired:
        logger.warning("  Scene detection timed out. Continuando sin detección.")
        return []

    stderr = result.stderr
    timestamps = []

    for line in stderr.splitlines():
        if "pts_time:" in line:
            match = re.search(r"pts_time:([0-9.]+)", line)
            if match:
                ts = round(float(match.group(1)), 1)
                if ts not in timestamps:
                    timestamps.append(ts)

    logger.info("  Cambios de escena detectados: %d", len(timestamps))
    return timestamps


# ═══════════════════════════════════════════════════════════════
#  PLAN DE MUESTREO (por escenas)
# ═══════════════════════════════════════════════════════════════

def _agrupar_escenas(
    duracion: float,
    cambios_escena: list[float],
) -> list[dict]:
    """
    Agrupa los cambios de escena en intervalos [inicio, fin).

    La primera escena arranca en 0; cada cambio de escena cierra la anterior
    y abre la siguiente; la última termina en `duracion`. Los cambios fuera
    del rango (0, duracion) se ignoran.

    Args:
        duracion: Duración del video en segundos.
        cambios_escena: Lista de timestamps (segundos) con cambios de escena.

    Returns:
        Lista de dicts con {"indice", "inicio", "fin", "duracion"},
        ordenada por inicio. Si no hay cambios, una única escena [0, duracion).
    """
    limites = sorted(c for c in cambios_escena if 0 < c < duracion)
    limites = [0.0] + limites + [duracion]

    escenas: list[dict] = []
    indice = 0
    for i in range(len(limites) - 1):
        inicio, fin = limites[i], limites[i + 1]
        if fin - inicio < 0.05:
            continue  # escenas degeneradas (cambio casi inmediato)
        escenas.append({
            "indice": indice,
            "inicio": round(inicio, 1),
            "fin": round(fin, 1),
            "duracion": round(fin - inicio, 1),
        })
        indice += 1  # B6: índices contiguos (no usar i: deja huecos al descartar)
    return escenas


def _subdividir_escenas_largas(
    escenas: list[dict],
    max_duracion_escena: float = 180.0,
) -> list[dict]:
    """
    Subdivide escenas que superan `max_duracion_escena` en sub-escenas
    contiguas de duración <= max.

    Motivo: en videos 360° equirectangulares continuos (sin cortes), el
    scene detection no encuentra cambios → todo el video queda como UNA
    sola escena → se muestrean ~10 imágenes pero solo se analiza la más
    nítida con UNA llamada IA. Subdividir escenas largas reparte el análisis
    temporalmente: un video de 9 min con max=180s produce ~3-4 sub-escenas,
    cada una con su contexto visual.

    Las sub-escenas conservan el formato original (indice/inicio/fin/duracion)
    y los índices se renumeran contiguos. Si `max_duracion_escena` es <= 0,
    no subdivide (comportamiento legacy).

    Args:
        escenas: Lista de dicts {"indice", "inicio", "fin", "duracion"}.
        max_duracion_escena: Máximo de segundos por escena (default 180).
            Las escenas más largas se cortan en segmentos de esa duración.

    Returns:
        Lista de dicts con el mismo formato, subdividida y renumerada.
    """
    if max_duracion_escena <= 0:
        return escenas

    sub_escenas: list[dict] = []
    indice = 0
    for escena in escenas:
        inicio, fin = escena["inicio"], escena["fin"]
        duracion = escena["duracion"]
        if duracion <= max_duracion_escena:
            sub_escenas.append({
                "indice": indice,
                "inicio": inicio,
                "fin": fin,
                "duracion": duracion,
            })
            indice += 1
            continue

        # Escena larga: cortar en segmentos de <= max_duracion_escena
        cursor = inicio
        while fin - cursor > max_duracion_escena + 1e-6:
            corte = round(cursor + max_duracion_escena, 1)
            sub_escenas.append({
                "indice": indice,
                "inicio": round(cursor, 1),
                "fin": corte,
                "duracion": round(corte - cursor, 1),
            })
            indice += 1
            cursor = corte

        # Último segmento (puede ser más corto que max)
        if fin - cursor > 0.05:
            sub_escenas.append({
                "indice": indice,
                "inicio": round(cursor, 1),
                "fin": round(fin, 1),
                "duracion": round(fin - cursor, 1),
            })
            indice += 1

    return sub_escenas


def _muestrear_escena(
    escena: dict,
    imgs_por_escena: int = 10,
) -> list[float]:
    """
    Genera timestamps distribuidos uniformemente dentro de una escena.

    Reemplaza el relleno fijo "cada N segundos": en lugar de muestrear el
    video de forma global, distribuye ~`imgs_por_escena` puntos dentro de
    cada escena. Si la escena dura menos que la cantidad pedida, toma los
    puntos que quepan (mínimo 1).

    Args:
        escena: Dict con {"inicio", "fin", "duracion"}.
        imgs_por_escena: Cantidad objetivo de imágenes por escena.

    Returns:
        Lista de timestamps (segundos) ordenada, sin duplicados.
    """
    inicio = escena["inicio"]
    duracion = escena["duracion"]
    if duracion <= 0:
        return [inicio]

    n = max(1, min(imgs_por_escena, int(duracion) + 1))
    timestamps: list[float] = []
    for i in range(n):
        # Evitar el último instante (fin) para no colisionar con la próxima escena
        ts = inicio + (duracion * i) / n
        timestamps.append(round(ts, 1))
    return timestamps


def _muestreo_video(
    duracion: float,
    cambios_escena: list[float],
    imgs_por_escena: int = 10,
    max_duracion_escena: float = 180.0,
) -> list[dict]:
    """
    Arma el plan de muestreo completo del video: escenas + timestamps.

    Agrupa los cambios de escena, subdivide las escenas largas (según
    `max_duracion_escena`) y asigna a cada escena su lista de timestamps
    a analizar. Las escenas vacías (sin timestamps) se descartan.

    Args:
        duracion: Duración del video en segundos.
        cambios_escena: Lista de timestamps con cambios de escena.
        imgs_por_escena: Imágenes objetivo por escena.
        max_duracion_escena: Máximo de segundos por escena (default 180).
            Las escenas más largas se subdividen en sub-escenas contiguas.

    Returns:
        Lista de dicts: {"indice", "inicio", "fin", "duracion", "timestamps"}.
    """
    escenas = _agrupar_escenas(duracion, cambios_escena)
    escenas = _subdividir_escenas_largas(escenas, max_duracion_escena)
    plan = []
    for escena in escenas:
        timestamps = _muestrear_escena(escena, imgs_por_escena)
        if timestamps:
            plan.append({**escena, "timestamps": timestamps})
    return plan


# ═══════════════════════════════════════════════════════════════
#  EXTRACCIÓN DE FOTOGRAMAS
# ═══════════════════════════════════════════════════════════════

def _extraer_fotograma(
    ruta_video: str,
    timestamp: float,
    output_path: str,
) -> bool:
    """
    Extrae un fotograma en un timestamp dado usando ffmpeg.

    Returns:
        True si se extrajo correctamente, False si no.
    """
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(timestamp),
        "-i", ruta_video,
        "-vframes", "1",
        "-q:v", "2",
        output_path,
    ]
    try:
        subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=60,
        )
        return Path(output_path).exists()
    except Exception as e:
        logger.warning("  Error extrayendo fotograma en t=%.1f: %s", timestamp, e)
        return False


# ═══════════════════════════════════════════════════════════════
#  SIDECAR .video.json
# ═══════════════════════════════════════════════════════════════

def _ruta_sidecar(ruta_video: str) -> str:
    """Ruta del sidecar .video.json para un video."""
    return f"{ruta_video}.video.json"


def _sidecar_existe_valido(ruta_video: str) -> Optional[dict[str, Any]]:
    """Verifica si existe un sidecar válido."""
    sidecar = _ruta_sidecar(ruta_video)
    if not Path(sidecar).exists():
        return None

    try:
        with open(sidecar, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("file_hash") == _hash_rapido(ruta_video):
            return data
    except Exception:
        pass
    return None


def _escribir_sidecar(ruta_video: str, resultado: dict[str, Any]):
    """Escribe el sidecar .video.json."""
    sidecar = _ruta_sidecar(ruta_video)
    try:
        with open(sidecar, "w", encoding="utf-8") as f:
            json.dump(resultado, f, ensure_ascii=False, indent=2)
        logger.info("  -> Sidecar escrito: %s", Path(sidecar).name)
    except Exception as e:
        logger.warning("  -> Error escribiendo sidecar: %s", e)


# ═══════════════════════════════════════════════════════════════
#  BASE DE DATOS
# ═══════════════════════════════════════════════════════════════

def conectar_db(ruta_db: str) -> sqlite3.Connection:
    if not Path(ruta_db).exists():
        raise FileNotFoundError(f"No se encuentra la DB: {ruta_db}")

    conn = sqlite3.connect(ruta_db)
    conn.row_factory = sqlite3.Row

    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='media'"
    )
    if not cursor.fetchone():
        raise RuntimeError(f"La DB {ruta_db} no contiene la tabla 'media'")

    return conn


def obtener_videos_sin_analisis(
    conn: sqlite3.Connection, limite: Optional[int] = None
) -> list[dict]:
    """Busca videos en la DB que no tienen análisis visual."""
    query = """
        SELECT m.id, m.filepath_absoluto, m.filename_original
        FROM media m
        WHERE m.type = 'video'
        AND m.id NOT IN (
            SELECT mm.media_id
            FROM media_metadata mm
            WHERE mm.key = ?
        )
        ORDER BY m.id
    """
    params = [KEY_VIDEO_ANALYSIS]

    if limite:
        query += " LIMIT ?"
        params.append(limite)

    cursor = conn.execute(query, params)
    filas = cursor.fetchall()

    resultado = []
    for fila in filas:
        ruta = fila["filepath_absoluto"]
        if Path(ruta).exists():
            resultado.append({
                "id": fila["id"],
                "ruta": ruta,
                "nombre": fila["filename_original"],
            })
        else:
            logger.warning("  -> Video no encontrado en disco: %s", ruta)

    return resultado


def guardar_en_db(
    conn: sqlite3.Connection, media_id: int, resultado: dict[str, Any]
):
    """Guarda el análisis visual en media_metadata.

    Guarda el dict completo del resultado (duración, modelo, fotogramas
    planos y escenas estructuradas). `fotogramas` conserva el formato
    anterior (cada fotograma con timestamp/tags/descripcion/origen) para
    no romper consumidores existentes; `escenas` agrega la metadata por
    escena (índice, duración, keywords).
    """
    try:
        conn.execute(
            "INSERT OR REPLACE INTO media_metadata (media_id, key, value) "
            "VALUES (?, ?, ?)",
            (media_id, KEY_VIDEO_ANALYSIS,
             json.dumps(resultado, ensure_ascii=False)),
        )
        conn.commit()
    except Exception as e:
        logger.error("  -> Error guardando en DB (id=%d): %s", media_id, e)


# ═══════════════════════════════════════════════════════════════
#  ANÁLISIS IA DE ESCENAS
# ═══════════════════════════════════════════════════════════════

# Máximo de keywords a conservar por escena
MAX_KEYWORDS_POR_ESCENA = 20


def analizar_escena(
    ruta_fotograma: str,
    modelo: str | None = None,
    usar_proxy: bool = True,
) -> dict[str, Any]:
    """
    Analiza el fotograma representativo de una escena con UNA llamada de IA.

    Usa el prompt combinado de prompts.yaml (keywords + descripción en un solo JSON) con
    temperatura baja para resultados estables.

    Args:
        ruta_fotograma: Ruta al fotograma a analizar.
        modelo: Modelo de visión.
        usar_proxy: Si True, usa proxy redimensionado a 800px.

    Returns:
        Dict con "keywords" (lista, máx MAX_KEYWORDS_POR_ESCENA) y
        "descripcion" (str).
    """
    resultado = analizar_imagen_completo(
        ruta_fotograma,
        modelo=modelo,
        temperatura=0.1,
        usar_proxy=usar_proxy,
    )

    keywords = resultado.get("keywords", [])[:MAX_KEYWORDS_POR_ESCENA]
    return {
        "keywords": keywords,
        "descripcion": (resultado.get("description", "") or "").strip().strip('"'),
    }


# ═══════════════════════════════════════════════════════════════
#  PROCESAMIENTO DE VIDEO
# ═══════════════════════════════════════════════════════════════

def _elegir_mejores_por_nitidez(
    rutas_frames: list[str],
    mejores_por_escena: int = 3,
) -> list[dict]:
    """
    Elige las N imágenes más nítidas de una lista (sin IA).

    Reutiliza el criterio de nitidez de batch_selector (varianza del
    Laplaciano, 100% computacional). Si hay una sola imagen, la devuelve
    directamente (batch_selector no genera evaluaciones en ese caso).

    Args:
        rutas_frames: Lista de rutas a fotogramas.
        mejores_por_escena: Cuántas imágenes conservar (default 3).

    Returns:
        Lista de dicts {"ruta", "puntaje"} ordenada por puntaje desc.
    """
    if not rutas_frames:
        return []
    if len(rutas_frames) == 1:
        return [{"ruta": rutas_frames[0], "puntaje": 0.0}]

    mejores = seleccionar_mejores_n(
        rutas_frames, n=mejores_por_escena, criterio="nitidez",
    )
    return [
        {"ruta": m["ruta"], "puntaje": m.get("puntaje", 0.0)}
        for m in mejores
    ]


def analizar_video(
    ruta: str,
    modelo: str | None = None,
    imgs_por_escena: int = 10,
    mejores_por_escena: int = 3,
    sensibilidad: float = SENSIBILIDAD_ESCENA,
    usar_proxy: bool = True,
    dry_run: bool = False,
    max_duracion_escena: float = 180.0,
) -> Optional[dict[str, Any]]:
    """
    Analiza un video completo: scene detection + muestreo por escena + IA.

    Por cada escena detectada (con subdivisión de escenas largas según
    `max_duracion_escena`):
      1. Se muestrean ~`imgs_por_escena` fotogramas distribuidos en la escena.
      2. Se eligen las `mejores_por_escena` imágenes por nitidez (sin IA).
      3. La imagen más nítida se analiza con UNA llamada de visión
         (prompt combinado de prompts.yaml: keywords + descripción de la escena).

    Returns:
        Dict con el análisis completo (escenas + fotogramas planos), o None
        si falló.
    """
    modelo = _modelo_efectivo(modelo)
    nombre = Path(ruta).name

    if dry_run:
        logger.info("  [DRY RUN] Se analizaría: %s", nombre)
        return None

    # 1. Duración
    logger.info("  Obteniendo duración...")
    try:
        duracion = _duracion_video(ruta)
    except Exception as e:
        logger.error("  Error obteniendo duración: %s", e)
        return None

    logger.info("  Duración: %.1f seg (%.1f min)", duracion, duracion / 60)

    # 2. Scene detection
    try:
        cambios = _detectar_cambios_escena(ruta, sensibilidad=sensibilidad)
    except Exception as e:
        logger.warning("  Error en scene detection: %s. Continuando sin escenas.", e)
        cambios = []

    # 3. Plan de muestreo por escenas
    plan = _muestreo_video(duracion, cambios, imgs_por_escena=imgs_por_escena,
                           max_duracion_escena=max_duracion_escena)
    logger.info("  Escenas detectadas: %d | imágenes por escena: ~%d",
                len(plan), imgs_por_escena)

    # 4. Por escena: extraer, elegir nítidas, analizar la mejor con 1 visión
    escenas_resultado: list[dict] = []
    fotogramas_planos: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="fotogramas_") as tmpdir:
        for idx, escena in enumerate(plan, 1):
            logger.info("  [Escena %d/%d] t=%.1f → %.1f (%.1fs, %d muestras)",
                        idx, len(plan), escena["inicio"], escena["fin"],
                        escena["duracion"], len(escena["timestamps"]))

            # Extraer los fotogramas de la escena
            rutas_escena: list[str] = []
            timestamps_por_ruta: dict[str, float] = {}
            for ts in escena["timestamps"]:
                frame_path = os.path.join(tmpdir, f"esc{idx}_t{ts:.1f}.jpg")
                if _extraer_fotograma(ruta, ts, frame_path):
                    rutas_escena.append(frame_path)
                    timestamps_por_ruta[frame_path] = ts
                else:
                    logger.warning("  -> No se pudo extraer fotograma en t=%.1f", ts)

            if not rutas_escena:
                logger.warning("  -> Escena sin fotogramas extraíbles. Se saltea.")
                continue

            # Elegir las más nítidas (sin IA)
            mejores = _elegir_mejores_por_nitidez(rutas_escena, mejores_por_escena)
            mejor_ruta = mejores[0]["ruta"] if mejores else rutas_escena[0]

            # Analizar la más representativa con UNA llamada de visión
            try:
                analisis = analizar_escena(
                    mejor_ruta, modelo=modelo, usar_proxy=usar_proxy
                )
            except Exception as e:
                logger.warning("  -> Error analizando escena t=%.1f: %s",
                               escena["inicio"], e)
                analisis = {"keywords": [], "descripcion": ""}

            keywords = analisis["keywords"]
            descripcion = analisis["descripcion"]

            # Guardar los fotogramas elegidos dentro de la escena
            fotogramas_escena = [
                {
                    "timestamp": timestamps_por_ruta[m["ruta"]],
                    "puntaje_nitidez": m["puntaje"],
                }
                for m in mejores
            ]

            escenas_resultado.append({
                "indice": escena["indice"],
                "inicio": escena["inicio"],
                "fin": escena["fin"],
                "duracion": escena["duracion"],
                "keywords": keywords,
                "descripcion": descripcion,
                "fotogramas": fotogramas_escena,
            })

            # Vista plana (compatible con formato anterior): cada fotograma
            # conserva su timestamp y hereda keywords/descripción de la escena
            for m in mejores:
                fotogramas_planos.append({
                    "timestamp": timestamps_por_ruta[m["ruta"]],
                    "tags": keywords,
                    "descripcion": descripcion,
                    "origen": "escena",
                    "escena": escena["indice"],
                    "puntaje_nitidez": m["puntaje"],
                })

            if keywords:
                logger.info("    Keywords (%d): %s",
                            len(keywords), ", ".join(keywords[:4]))

    # 5. Armar resultado final
    resultado_final = {
        "file_hash": _hash_rapido(ruta),
        "duracion_seg": round(duracion, 1),
        "imgs_por_escena": imgs_por_escena,
        "mejores_por_escena": mejores_por_escena,
        "sensibilidad_escena": sensibilidad,
        "max_duracion_escena": max_duracion_escena,
        "modelo": modelo,
        "fecha": datetime.now().isoformat(),
        "fotogramas": fotogramas_planos,
        "escenas": escenas_resultado,
    }

    return resultado_final


# ═══════════════════════════════════════════════════════════════
#  FLUJOS PRINCIPALES
# ═══════════════════════════════════════════════════════════════

def procesar_desde_db(
    ruta_db: str,
    modelo: str | None = None,
    imgs_por_escena: int = 10,
    mejores_por_escena: int = 3,
    sensibilidad: float = SENSIBILIDAD_ESCENA,
    usar_proxy: bool = True,
    limite: Optional[int] = None,
    sidecar: bool = False,
    dry_run: bool = False,
    max_duracion_escena: float = 180.0,
):
    logger.info("Conectando a DB: %s", ruta_db)
    conn = conectar_db(ruta_db)

    videos = obtener_videos_sin_analisis(conn, limite=limite)

    if not videos:
        logger.info("No hay videos sin analizar en la DB.")
        conn.close()
        return

    logger.info("Videos a procesar: %d%s", len(videos),
                " (dry run)" if dry_run else "")

    ok = 0
    fail = 0

    for i, vid in enumerate(videos, 1):
        logger.info("[%d/%d] %s", i, len(videos), vid["nombre"])

        if dry_run:
            ok += 1
            continue

        # Verificar sidecar existente
        if sidecar:
            existente = _sidecar_existe_valido(vid["ruta"])
            if existente is not None:
                logger.info("  -> Ya tiene sidecar válido (%d fotogramas). Skip.",
                            len(existente.get("fotogramas", [])))
                guardar_en_db(conn, vid["id"], existente)
                ok += 1
                continue

        resultado = analizar_video(
            vid["ruta"],
            modelo=modelo,
            imgs_por_escena=imgs_por_escena,
            mejores_por_escena=mejores_por_escena,
            sensibilidad=sensibilidad,
            usar_proxy=usar_proxy,
            dry_run=False,
            max_duracion_escena=max_duracion_escena,
        )

        if resultado:
            # Sidecar
            if sidecar:
                _escribir_sidecar(vid["ruta"], resultado)
            # DB
            guardar_en_db(conn, vid["id"], resultado)
            ok += 1
        else:
            fail += 1

    conn.close()

    logger.info("=== RESUMEN: %d analizados, %d fallos (de %d) ===",
                ok, fail, len(videos))


def procesar_desde_carpeta(
    carpeta: str,
    modelo: str | None = None,
    imgs_por_escena: int = 10,
    mejores_por_escena: int = 3,
    sensibilidad: float = SENSIBILIDAD_ESCENA,
    usar_proxy: bool = True,
    dry_run: bool = False,
    max_duracion_escena: float = 180.0,
):
    carpeta = Path(carpeta)
    if not carpeta.exists():
        raise FileNotFoundError(f"La carpeta no existe: {carpeta}")

    rutas = sorted(
        str(p) for p in carpeta.rglob("*")
        if _es_video(str(p))
    )

    if not rutas:
        logger.info("No se encontraron videos en %s", carpeta)
        return

    logger.info("Videos encontrados: %d%s", len(rutas),
                " (dry run)" if dry_run else "")

    ok = 0
    fail = 0
    ya_analizados = 0

    for i, ruta in enumerate(rutas, 1):
        nombre = Path(ruta).name
        logger.info("[%d/%d] %s", i, len(rutas), nombre)

        if not dry_run:
            existente = _sidecar_existe_valido(ruta)
            if existente is not None:
                logger.info("  -> Ya tiene sidecar válido (%d fotogramas). Skip.",
                            len(existente.get("fotogramas", [])))
                ya_analizados += 1
                continue

        if dry_run:
            ok += 1
            continue

        resultado = analizar_video(
            ruta, modelo=modelo, imgs_por_escena=imgs_por_escena,
            mejores_por_escena=mejores_por_escena,
            sensibilidad=sensibilidad, usar_proxy=usar_proxy,
            dry_run=False,
            max_duracion_escena=max_duracion_escena,
        )

        if resultado:
            _escribir_sidecar(ruta, resultado)
            ok += 1
        else:
            fail += 1

    logger.info("=== RESUMEN: %d nuevos, %d ya tenían sidecar, %d fallos (de %d) ===",
                ok, ya_analizados, fail, len(rutas))


def procesar_archivo_individual(
    ruta: str,
    modelo: str | None = None,
    imgs_por_escena: int = 10,
    mejores_por_escena: int = 3,
    sensibilidad: float = SENSIBILIDAD_ESCENA,
    usar_proxy: bool = True,
    json_out: Optional[str] = None,
    max_duracion_escena: float = 180.0,
):
    if not Path(ruta).exists():
        raise FileNotFoundError(f"No se encuentra: {ruta}")

    if not _es_video(ruta):
        raise ValueError(f"No es un video soportado: {Path(ruta).suffix}")

    resultado = analizar_video(
        ruta, modelo=modelo, imgs_por_escena=imgs_por_escena,
        mejores_por_escena=mejores_por_escena,
        sensibilidad=sensibilidad, usar_proxy=usar_proxy,
        dry_run=False,
        max_duracion_escena=max_duracion_escena,
    )

    if not resultado:
        logger.error("Error analizando video.")
        return

    # Mostrar resumen
    fotos = resultado["fotogramas"]
    escenas = resultado.get("escenas", [])
    print(f"\n  Duración: {resultado['duracion_seg']:.0f}s "
          f"({resultado['duracion_seg']/60:.0f} min)")
    print(f"  Escenas detectadas: {len(escenas)}")
    print(f"  Fotogramas analizados: {len(fotos)}")
    print(f"  Modelo: {resultado['modelo']}")
    print()

    for f in fotos[:5]:
        tags_str = ", ".join(f["tags"][:4])
        print(f"  [{f['timestamp']:6.1f}s] (escena {f.get('escena', '?')}) {tags_str}")
    if len(fotos) > 5:
        print(f"  ... y {len(fotos) - 5} fotogramas más")

    # Sidecar
    _escribir_sidecar(ruta, resultado)

    # JSON externo
    if json_out:
        with open(json_out, "w", encoding="utf-8") as f:
            json.dump(resultado, f, ensure_ascii=False, indent=2)
        print(f"  JSON exportado: {json_out}")


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════

def listar_modelos():
    from scripts.ai_media.ollama_client import asegurar_ollama
    if not asegurar_ollama():
        print("  ⚠️  Ollama no está disponible. No se pueden listar modelos.")
        return
    try:
        import ollama
        response = ollama.list()
        if hasattr(response, "models"):
            modelos = response.models
        elif isinstance(response, dict):
            modelos = response.get("models", [])
        else:
            modelos = list(response)

        print("=== Modelos instalados en Ollama ===\n")
        for m in modelos:
            nombre = m.model if hasattr(m, "model") else str(m)
            print(f"  {nombre}")
        print("\nRecomendados para visión:")
        for m in MODELOS_RECOMENDADOS:
            print(f"  - {m}")
        print()
    except Exception as e:
        print(f"Error al conectar con Ollama: {e}")


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        description="Análisis visual de videos con IA.\n\n"
                    "Detecta cambios de escena (ffmpeg), subdivide las escenas\n"
                    "largas (> --max-duracion-escena), muestrea ~N imágenes\n"
                    "distribuidas dentro de cada escena, elige las más nítidas\n"
                    "por varianza del Laplaciano y analiza la más representativa\n"
                    "con UNA llamada de visión por escena (minicpm-v4.6,\n"
                    "keywords + descripción combinadas).\n\n"
                    "Modos:\n"
                    "  --file ruta    : analiza un solo video\n"
                    "  --db ruta      : procesa videos de la DB (post-ingesta)\n"
                    "  --folder ruta  : analiza todos los videos de una carpeta\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    modo = parser.add_argument_group("Modo de operación")
    modo.add_argument("--file", help="Ruta a un archivo de video")
    modo.add_argument("--db", help="Ruta a la base de datos SQLite")
    modo.add_argument("--folder", help="Ruta a carpeta con videos")

    parser.add_argument("--modelo", default=None,
                        help="Modelo de visión (default: prompts.yaml vision.combinado). "
                             "Usar --list-models para ver los instalados.")
    parser.add_argument("--por-escena", dest="imgs_por_escena", type=int, default=10,
                        help="Imágenes a muestrear dentro de cada escena "
                             "(default: 10; si la escena dura menos, se toman las que quepan)")
    parser.add_argument("--mejores-por-escena", type=int, default=3,
                        help="Cuántas imágenes conservar por escena tras la "
                             "selección por nitidez (default: 3)")
    parser.add_argument("--max-duracion-escena", type=float, default=180.0,
                        help="Máximo de segundos por escena; las escenas más "
                             "largas se subdividen en sub-escenas contiguas de "
                             "<= ese valor (default: 180; 0 = sin subdivisión)")
    parser.add_argument("--sensibilidad", type=float, default=SENSIBILIDAD_ESCENA,
                        help="Sensibilidad de detección de escenas 0.0-1.0, "
                             "menor = más sensible (default: 0.4)")
    parser.add_argument("--no-proxy", action="store_true",
                        help="No usar proxies (deshabilita redimensionado)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Solo mostrar qué se haría")
    parser.add_argument("--list-models", action="store_true",
                        help="Mostrar modelos Ollama instalados y salir")

    # Modo DB
    parser.add_argument("--limit", type=int, default=None,
                        help="Limitar cantidad (solo modo --db)")
    parser.add_argument("--sidecar", action="store_true",
                        help="Generar sidecar .video.json (solo modo --db)")

    # Exportación individual
    parser.add_argument("--json", help="Exportar resultado a JSON (solo modo --file)")

    args = parser.parse_args(argv)

    if args.list_models:
        listar_modelos()
        return

    # Configurar logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    modos = [args.file, args.db, args.folder]
    activos = sum(1 for m in modos if m)
    if activos == 0:
        parser.error("Debe especificar --file, --db o --folder")
    if activos > 1:
        parser.error("Use solo un modo a la vez (--file, --db o --folder)")

    usar_proxy = not args.no_proxy

    try:
        if args.file:
            procesar_archivo_individual(
                ruta=args.file,
                modelo=args.modelo,
                imgs_por_escena=args.imgs_por_escena,
                mejores_por_escena=args.mejores_por_escena,
                sensibilidad=args.sensibilidad,
                usar_proxy=usar_proxy,
                json_out=args.json,
                max_duracion_escena=args.max_duracion_escena,
            )

        elif args.db:
            procesar_desde_db(
                ruta_db=args.db,
                modelo=args.modelo,
                imgs_por_escena=args.imgs_por_escena,
                mejores_por_escena=args.mejores_por_escena,
                sensibilidad=args.sensibilidad,
                usar_proxy=usar_proxy,
                limite=args.limit,
                sidecar=args.sidecar,
                dry_run=args.dry_run,
                max_duracion_escena=args.max_duracion_escena,
            )

        elif args.folder:
            procesar_desde_carpeta(
                carpeta=args.folder,
                modelo=args.modelo,
                imgs_por_escena=args.imgs_por_escena,
                mejores_por_escena=args.mejores_por_escena,
                sensibilidad=args.sensibilidad,
                usar_proxy=usar_proxy,
                dry_run=args.dry_run,
                max_duracion_escena=args.max_duracion_escena,
            )

    except KeyboardInterrupt:
        logger.info("\nProceso interrumpido por el usuario.")
        sys.exit(1)

    except Exception as e:
        logger.error("Error: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
