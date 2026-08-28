#!/usr/bin/env python3
"""
corregir_timestamp_360.py — Corrige timestamps de videos 360° Insta360 post-ingesta.

Problema (docs/discrepancia_horarios_camaras.md): `QuickTime:CreateDate`
embebido es UTC; la ingesta lo trataba como ART (-3) → +3h de desfase.
El filename lleva relojes LA+7h / UTC+1 / reconfigurada → hasta ±7h si se usa
como fallback. Este script es **post-ingesta, temporario**: re-lee
`QuickTime:CreateDate` del archivo y recalcula timestamp_utc/timestamp_original
correctos.

Re-uso: `diagnosticar_camaras_360.py` (`_extraer_create_date`,
`_parsear_create_date`, `_extraer_video_info`) + `ubicar_videos_gpx.py`
(limpieza de ubicacion_video tras corregir).

Uso:
    python scripts/corregir_timestamp_360.py --dry-run
    python scripts/corregir_timestamp_360.py --mode update
    python scripts/corregir_timestamp_360.py --mode replace --reubicar
    python scripts/corregir_timestamp_360.py --json --mode skip

Modos (solo skip|update|replace, como inferir_hora_textos):
    skip    (default) solo 360 sin marca 360_UTC o con delta >10 min
    update  reprocesa todos los subtype='360'
    replace limpia y regenera (con backup automático)

Sale 0 si ok; 1 si hubo errores.
"""

import argparse
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Reutilizar helpers de diagnosticar_camaras_360 sin importar el módulo (evita
# arrastrar su main). Copiamos las funciones mínimas necesarias.
UTC = timezone.utc
OFFSET_ARGENTINA = timedelta(hours=3)
DELTA_UMBRAL_S = 600  # 10 min: skip solo corrige si el desfase supera esto

log = logging.getLogger(__name__)

# Permitir importar db.util desde la raíz
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.util import abrir, resolver_db  # noqa: E402


# ── ExifTool / ffprobe helpers (copiados de diagnosticar_camaras_360.py) ───────

def _buscar_exiftool() -> str | None:
    candidatos = [
        r"C:\Program Files\digiKam\exiftool.exe",
        r"C:\Program Files\exiftool.exe",
        "exiftool",
    ]
    for ruta in candidatos:
        if ruta == "exiftool":
            hallado = shutil.which(ruta)
            if hallado:
                return hallado
        elif os.path.isfile(ruta):
            return ruta
    return None


def _extraer_create_date(exiftool: str, ruta: str) -> str | None:
    cmd = [exiftool, "-json", "-n", "-QuickTime:CreateDate", ruta]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if res.returncode != 0:
            return None
        datos = json.loads(res.stdout)
        if not datos:
            return None
        return str(datos[0].get("CreateDate")) if datos[0].get("CreateDate") else None
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None


def _parsear_create_date(fecha_str: str) -> datetime | None:
    if not fecha_str:
        return None
    normalizado = fecha_str.replace(":", "-", 2)
    formatos = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S+00:00",
    ]
    for fmt in formatos:
        try:
            dt = datetime.strptime(normalizado, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except ValueError:
            continue
    return None


def _normalizar_dt(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except (ValueError, TypeError):
        return None


# ── Query candidatos ──────────────────────────────────────────────────────────

def _query_candidatos(conn: sqlite3.Connection, mode: str) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    # subtype='360' lo setea improve_db --step video_metadata; el diagnóstico
    # recomienda correrlo antes. Filtrado fino por delta/marca se hace en Python.
    return conn.execute("""
        SELECT id, filename_original, filepath_absoluto, timestamp_utc,
               timestamp_original, timezone_note, duration_secs, subtype
        FROM media
        WHERE subtype='360'
    """).fetchall()


# ── Backup ────────────────────────────────────────────────────────────────────

def _auto_backup(db_path: str) -> str | None:
    backup_dir = os.path.join(os.path.dirname(db_path), "backups")
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(backup_dir, f"flujos_autobackup_{ts}.db")
    try:
        shutil.copy2(db_path, dest)
        log.info("  Backup: %s", os.path.basename(dest))
        return dest
    except Exception as e:
        log.warning("  No se pudo crear backup: %s", e)
        return None


# ── Core ──────────────────────────────────────────────────────────────────────

def procesar(
    db_path: str | None = None,
    mode: str = "skip",
    dry_run: bool = False,
    reubicar: bool = False,
    json_out: bool = False,
    verbose: bool = False,
) -> int:
    db_resolved = resolver_db(db_path)
    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(message)s", force=True)
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s", force=True)

    exiftool = _buscar_exiftool()
    if not exiftool:
        log.error("exiftool no encontrado. Instalalo o agregalo al PATH.")
        return 1

    if mode == "replace" and not dry_run and os.path.isfile(db_resolved):
        _auto_backup(db_resolved)

    conn = abrir(db_resolved)
    conn.row_factory = sqlite3.Row

    filas = _query_candidatos(conn, mode)
    if not filas:
        log.info("Sin candidatos subtype='360'. Verificá que la ingesta haya corrido y video_metadata haya marcado 360.")
        conn.close()
        if json_out:
            print(json.dumps([], ensure_ascii=False, indent=2))
        return 0

    log.info("Candidatos 360: %d (mode=%s, dry_run=%s)", len(filas), mode, dry_run)

    resultados: list[dict] = []
    corregidos = 0
    omitidos = 0
    errores = 0
    sin_archivo = 0

    for row in filas:
        mid = row["id"]
        fpath = row["filepath_absoluto"]
        fname = row["filename_original"] or Path(fpath or "").name
        ts_utc_old = row["timestamp_utc"]

        # Filtro skip: si ya tiene marca 360_UTC, solo corregir si delta > umbral
        necesita = True
        if mode == "skip" and row["timezone_note"] and "360_UTC" in row["timezone_note"]:
            necesita = False  # se re-evalúa abajo si delta grande

        create_str = _extraer_create_date(exiftool, fpath) if fpath and os.path.isfile(fpath) else None
        if not create_str:
            if fpath and not os.path.isfile(fpath):
                sin_archivo += 1
                resultados.append({"id": mid, "archivo": fname, "estado": "sin_archivo", "ruta": fpath})
            else:
                errores += 1
                resultados.append({"id": mid, "archivo": fname, "estado": "sin_CreateDate"})
            continue

        dt_utc = _parsear_create_date(create_str)
        if not dt_utc:
            errores += 1
            resultados.append({"id": mid, "archivo": fname, "estado": "CreateDate no parseable", "valor": create_str})
            continue

        dt_utc = dt_utc.astimezone(UTC)
        hora_real = dt_utc - OFFSET_ARGENTINA
        nuevo_utc = dt_utc.isoformat()
        nuevo_orig = hora_real.isoformat()
        dur = row["duration_secs"]
        nuevo_end = None
        if dur is not None:
            try:
                nuevo_end = (dt_utc + timedelta(seconds=float(dur))).isoformat()
            except (ValueError, TypeError):
                nuevo_end = None

        # Calcular delta vs viejo para decidir skip y para reporte
        delta_s = None
        if ts_utc_old:
            old_dt = _normalizar_dt(ts_utc_old)
            if old_dt:
                delta_s = abs((dt_utc - old_dt).total_seconds())

        if mode == "skip" and not necesita:
            if delta_s is not None and delta_s <= DELTA_UMBRAL_S:
                omitidos += 1
                resultados.append({"id": mid, "archivo": fname, "estado": "ok_ya_corregido", "delta_s": round(delta_s, 1)})
                continue
            # delta grande → corregir aunque tenga marca (archivo re-escrito, etc.)

        # Dry-run: solo reportar
        if dry_run:
            resultados.append({
                "id": mid, "archivo": fname, "estado": "corregir",
                "viejo_utc": ts_utc_old, "nuevo_utc": nuevo_utc,
                "nuevo_original": nuevo_orig, "delta_s": round(delta_s, 1) if delta_s is not None else None,
            })
            corregidos += 1
            continue

        # UPDATE real + limpieza de ubicacion_video (forzar re-ubicar)
        try:
            conn.execute(
                "UPDATE media SET timestamp_original=?, timestamp_utc=?, timezone_note=?, end_time=?, updated_at=datetime('now') WHERE id=?",
                (nuevo_orig, nuevo_utc, "360_UTC: CreateDate -3h", nuevo_end, mid),
            )
            conn.execute("DELETE FROM media_keypoints WHERE media_id=? AND key='ubicacion_video'", (mid,))
            conn.execute("DELETE FROM media_metadata WHERE media_id=? AND key IN ('ubicacion_video_estado','ubicacion_video_gaps')", (mid,))
            corregidos += 1
            resultados.append({"id": mid, "archivo": fname, "estado": "corregido", "nuevo_utc": nuevo_utc, "delta_s": round(delta_s, 1) if delta_s is not None else None})
        except sqlite3.OperationalError as e:
            errores += 1
            resultados.append({"id": mid, "archivo": fname, "estado": f"error DB: {e}"})

    if not dry_run:
        conn.commit()
    conn.close()

    log.info("")
    log.info("Resumen: candidatos=%d corregidos=%d omitidos=%d sin_archivo=%d errores=%d",
             len(filas), corregidos, omitidos, sin_archivo, errores)
    if dry_run:
        log.info("Dry-run: no se escribió en la DB. Sacá --dry-run para aplicar.")

    if json_out:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass
        print(json.dumps(resultados, ensure_ascii=False, indent=2))

    if reubicar and not dry_run and corregidos:
        log.info("")
        log.info("Re-ubicando videos 360 contra GPX (--reubicar)...")
        try:
            from scripts.ubicar_videos_gpx import procesar as ubicar_procesar
            stats = ubicar_procesar(resolver_db(db_path), mode="update", solo_360=True)
            log.info("  ubicar_videos_gpx: %s", stats)
        except Exception as e:
            log.warning("  No se pudo re-ubicar automáticamente: %s", e)
            log.warning("  Ejecutá manualmente: python scripts/ubicar_videos_gpx.py --solo-360 --mode update")

    return 0 if errores == 0 else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Corrige timestamps 360 post-ingesta (CreateDate UTC->ART).")
    p.add_argument("--db", default=None, help="Ruta a flujos.db")
    p.add_argument("--mode", choices=["skip", "update", "replace"], default="skip", help="skip: solo pendientes (default)")
    p.add_argument("--dry-run", action="store_true", help="No escribe en DB, solo reporta")
    p.add_argument("--json", action="store_true", help="Salida JSON a stdout")
    p.add_argument("--reubicar", action="store_true", help="Tras corregir, re-ejecuta ubicar_videos_gpx --solo-360 --mode update")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args(argv)
    return procesar(db_path=args.db, mode=args.mode, dry_run=args.dry_run,
                    reubicar=args.reubicar, json_out=args.json, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
