#!/usr/bin/env python3
"""
improve_db.py — Mejora la base de datos de Flujos con pasos de post-procesamiento.

Uso:
    python scripts/improve_db.py                          # Todos los pasos (modo skip)
    python scripts/improve_db.py --all                    # Ídem
    python scripts/improve_db.py --list                   # Listar pasos disponibles
    python scripts/improve_db.py --steps colors,keywords  # Solo esos pasos
    python scripts/improve_db.py --mode update            # Re-ejecutar y actualizar
    python scripts/improve_db.py --mode replace           # Borrar y regenerar
    python scripts/improve_db.py --db ruta/a/flujos.db    # DB personalizada
    python scripts/improve_db.py --steps keywords            # muestra en vivo cada keyword (default)
    python scripts/improve_db.py --steps keywords --no-mostrar  # silencioso
    python scripts/improve_db.py --steps keywords --workers 1  # 1 request a la vez (default, estable)

    ⚠️ --workers 2+: NO recomendado. Ollama serializa la inferencia; 2 requests
    concurrentes compiten por memoria y pueden desestabilizar el modelo
    (síntoma: @@@@@ y tags vacíos, reportado en otra máquina). Medido: 25x más lento.

Modos:
    skip    (default) Saltar medios que ya tienen el dato procesado
    update  Re-ejecutar el paso (actualiza lo existente)
    replace Borra todo lo generado por el paso y lo regenera desde cero

Pasos:
    colors        Extraer colores dominantes de imágenes
    keywords      Etiquetar imágenes con IA (visión EN + traducción ES)
    descriptions  Describir imágenes con IA (visión EN + traducción ES)
    combinado     Keywords + descripción en UNA llamada de visión (EN) + 1 de traducción (ES)
    transcribe    Transcribir audios/videos (faster-whisper)
    keypoints     Poblar media_keypoints desde transcripciones
    timestamps    Inferir timestamps faltantes por clúster + orden
    gps           Inferir GPS desde medios cercanos en el tiempo

FLUJO IA (keywords/descriptions/combinado):
    Los modelos de visión (minicpm) responden mejor en inglés, así que el
    pipeline interno genera EN y lo guarda en claves temporales:
        ia_keywords_en / ia_description_en
    Luego traduce a español con el pipeline NO-AI (glosario + motor clásico,
    google por defecto) y guarda en:
        ia_keywords    / ia_description
    La INTERFAZ (lo que el usuario consume) es SIEMPRE español. El EN queda
    en DB para poder re-traducir sin re-correr visión (--mode update).
    Traducción reutilizable: scripts/ai_media/traducir_metadata.py
    (--motor ollama conserva el pipeline legacy con translategemma).
"""

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

from tqdm import tqdm

from db.util import abrir, resolver_db, ModoHelper

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("improve_db")

# ── Claves en media_metadata (pipeline IA EN → ES) ───────────────────────────
# La IA de visión genera en inglés (mejor calidad con minicpm) y se guarda en
# claves *_en; luego se traduce a español con un modelo de texto y se guarda
# en las claves definitivas (ia_keywords / ia_description) que consume el resto
# del proyecto. El EN queda persistido para poder re-traducir sin re-correr
# visión.
CLAVE_KW_EN = "ia_keywords_en"
CLAVE_DESC_EN = "ia_description_en"
CLAVE_KW_ES = "ia_keywords"
CLAVE_DESC_ES = "ia_description"


# ==============================================================================
# CHECK: determinar cuánto trabajo pendiente hay para cada paso
# ==============================================================================

def check_colors(conn) -> dict:
    """Cuenta imágenes que faltan procesar."""
    total = conn.execute("SELECT COUNT(*) FROM media WHERE type='image'").fetchone()[0]
    pendientes = conn.execute(
        "SELECT COUNT(*) FROM media WHERE type='image' AND color_1_hex IS NULL"
    ).fetchone()[0]
    return {"total": total, "pendientes": pendientes, "hecho": total - pendientes}


def check_keywords(conn) -> dict:
    """Cuenta imágenes sin keywords en media_metadata."""
    total = conn.execute("SELECT COUNT(*) FROM media WHERE type='image'").fetchone()[0]
    pendientes = conn.execute("""
        SELECT COUNT(*) FROM media m
        WHERE m.type='image'
          AND NOT EXISTS (
              SELECT 1 FROM media_metadata mm
              WHERE mm.media_id = m.id AND mm.key = 'ia_keywords'
          )
    """).fetchone()[0]
    return {"total": total, "pendientes": pendientes, "hecho": total - pendientes}


def check_descriptions(conn) -> dict:
    """Cuenta imágenes sin descripción en media_metadata."""
    total = conn.execute("SELECT COUNT(*) FROM media WHERE type='image'").fetchone()[0]
    pendientes = conn.execute("""
        SELECT COUNT(*) FROM media m
        WHERE m.type='image'
          AND NOT EXISTS (
              SELECT 1 FROM media_metadata mm
              WHERE mm.media_id = m.id AND mm.key = 'ia_description'
          )
    """).fetchone()[0]
    return {"total": total, "pendientes": pendientes, "hecho": total - pendientes}


def check_combinado(conn) -> dict:
    """Cuenta imágenes sin keywords Y descripción combinadas (ES finales)."""
    total = conn.execute("SELECT COUNT(*) FROM media WHERE type='image'").fetchone()[0]
    pendientes = conn.execute("""
        SELECT COUNT(*) FROM media m
        WHERE m.type='image'
          AND (
                NOT EXISTS (SELECT 1 FROM media_metadata mm WHERE mm.media_id = m.id AND mm.key = 'ia_keywords')
             OR NOT EXISTS (SELECT 1 FROM media_metadata mm WHERE mm.media_id = m.id AND mm.key = 'ia_description')
          )
    """).fetchone()[0]
    return {"total": total, "pendientes": pendientes, "hecho": total - pendientes}


def check_transcribe(conn) -> dict:
    """Cuenta videos/audios sin transcripción en media_metadata."""
    total = conn.execute(
        "SELECT COUNT(*) FROM media WHERE type IN ('video', 'audio')"
    ).fetchone()[0]
    pendientes = conn.execute("""
        SELECT COUNT(*) FROM media m
        WHERE m.type IN ('video', 'audio')
          AND NOT EXISTS (
              SELECT 1 FROM media_metadata mm
              WHERE mm.media_id = m.id AND mm.key = 'whisper_segments'
          )
    """).fetchone()[0]
    return {"total": total, "pendientes": pendientes, "hecho": total - pendientes}


def check_keypoints(conn) -> dict:
    """Cuenta transcripciones que aún no tienen keypoints."""
    # Medios que tienen transcripciones en media_metadata
    transcritos = conn.execute("""
        SELECT COUNT(*) FROM media m
        WHERE EXISTS (
            SELECT 1 FROM media_metadata mm
            WHERE mm.media_id = m.id AND mm.key = 'whisper_segments'
        )
    """).fetchone()[0]
    # De esos, cuántos ya tienen keypoints
    con_kp = conn.execute("""
        SELECT COUNT(DISTINCT media_id) FROM media_keypoints
    """).fetchone()[0]
    return {"total": transcritos, "pendientes": transcritos - con_kp, "hecho": con_kp}


def check_timestamps(conn) -> dict:
    """Cuenta medios con timestamp inferido o fallback (mejorables)."""
    total = conn.execute(
        "SELECT COUNT(*) FROM media WHERE timestamp_utc IS NOT NULL"
    ).fetchone()[0]
    # Los que tienen timestamp_utc pero vía fallback (modified_at) o están NULL
    mejorables = conn.execute("""
        SELECT COUNT(*) FROM media
        WHERE timestamp_utc IS NULL
           OR timezone_note LIKE '%fallback%'
           OR timezone_note LIKE '%no se pudo%'
    """).fetchone()[0]
    return {"total": total, "pendientes": mejorables, "hecho": total - mejorables}


def check_gps(conn) -> dict:
    """Cuenta medios sin GPS."""
    total = conn.execute(
        "SELECT COUNT(*) FROM media WHERE type IN ('image', 'video')"
    ).fetchone()[0]
    pendientes = conn.execute("""
        SELECT COUNT(*) FROM media
        WHERE type IN ('image', 'video')
          AND latitude IS NULL
    """).fetchone()[0]
    return {"total": total, "pendientes": pendientes, "hecho": total - pendientes}


# ==============================================================================
# RUN: ejecución de cada paso
# ==============================================================================

def run_colors(conn, db_path, mode, stats):
    """
    Extrae colores dominantes de imágenes que aún no los tienen.
    Re-implementación local para evitar el bug de webcolors en ingest.py.
    """
    log.info("Paso: colors — Extrayendo colores dominantes")

    from color_utils import extract_dominant_colors, get_color_names

    helper = ModoHelper(mode)

    # Replace: limpiar columnas de color en todas las imágenes
    helper.clean(conn, """
        UPDATE media SET
            color_1_hex = NULL, color_1_name_css = NULL, color_1_name_basic = NULL,
            color_2_hex = NULL, color_2_name_css = NULL, color_2_name_basic = NULL,
            color_3_hex = NULL, color_3_name_css = NULL, color_3_name_basic = NULL
        WHERE type='image'
    """)

    query = helper.build_query(
        base="SELECT id, filepath_absoluto FROM media m WHERE type='image'",
        check_col="color_1_hex",
    )

    rows = conn.execute(query).fetchall()
    if not rows:
        log.info("  No hay imágenes pendientes.")
        return

    ok = 0
    errors = 0
    for mid, fpath in tqdm(rows, desc="  Colores", unit="img", ncols=80):
        if not os.path.isfile(fpath):
            log.warning("  Archivo no encontrado: %s", fpath)
            stats["warnings"] += 1
            continue
        try:
            colors = extract_dominant_colors(fpath, n_colors=3)
            for i, hex_color in enumerate(colors, 1):
                name_css, name_basic = get_color_names(hex_color)
                conn.execute(
                    f"UPDATE media SET color_{i}_hex=?, color_{i}_name_css=?, "
                    f"color_{i}_name_basic=? WHERE id=?",
                    (hex_color, name_css, name_basic, mid),
                )
            ok += 1
        except Exception as e:
            log.warning("  ⚠ Error en media id=%s: %s", mid, e)
            errors += 1

    conn.commit()
    log.info("  ✅ Colores extraídos: %d  |  Errores: %d", ok, errors)
    stats["colors_ok"] = ok
    stats["colors_err"] = errors


def _crear_cliente_texto():
    """Crea el cliente Ollama para la fase de traducción."""
    from scripts.ai_media.ollama_client import asegurar_ollama
    if not asegurar_ollama():
        raise RuntimeError(
            "Ollama no está disponible. Verificá que el servidor esté "
            "corriendo (ollama serve) o que el binario esté en PATH."
        )
    import ollama
    return ollama.Client(timeout=300)


def _procesar_vision(conn, mode, stats, nombre, fn_vision, clave_en, clave_es,
                     mostrar_label, es_lista):
    """
    Fase A de keywords/descriptions: genera el dato EN con visión y lo guarda.

    Args:
        conn: conexión a la DB.
        mode: skip|update|replace.
        stats: dict de estadísticas.
        nombre: nombre del paso ("keywords" | "descriptions").
        fn_vision: función que recibe (fpath, modelo) y devuelve el dato EN
                   (list[str] si es_lista, str si no).
        clave_en: clave media_metadata donde se guarda el EN.
        clave_es: clave ES (se limpia en replace/update junto con el EN).
        mostrar_label: etiqueta para --mostrar.
        es_lista: True si fn_vision devuelve lista (keywords), False si str.
    """
    log.info("  [Fase A] Visión (EN) → %s", clave_en)

    from scripts.ai_media.image_analysis import MODELO_VISION_DEFAULT

    helper = ModoHelper(mode)

    # replace: limpiar BOTH claves (EN y ES) — la fase B retraducirá todo
    if mode == "replace":
        conn.execute(
            "DELETE FROM media_metadata WHERE key IN (?, ?)",
            (clave_en, clave_es),
        )
        conn.commit()

    if mode == "skip":
        query = f"""
            SELECT m.id, m.filepath_absoluto FROM media m
            WHERE m.type='image'
              AND NOT EXISTS (
                  SELECT 1 FROM media_metadata mm
                  WHERE mm.media_id = m.id AND mm.key = '{clave_en}'
              )
        """
    else:  # update / replace
        query = "SELECT id, filepath_absoluto FROM media WHERE type='image'"

    rows = conn.execute(query).fetchall()
    if not rows:
        log.info("  No hay imágenes pendientes (visión).")
        return

    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

    def _process_one(mid, fpath):
        if not os.path.isfile(fpath):
            return "warning", fpath
        try:
            dato = fn_vision(fpath, modelo=MODELO_VISION_DEFAULT)
            return "ok", (mid, dato)
        except Exception as e:
            return "error", (fpath, e)

    ok = 0
    errors = 0
    warnings = 0
    n_workers = max(1, CONTEXTO.get("workers", 1))
    timeout_future = CONTEXTO.get("timeout_future", 300)

    # ── Checkpoint por lote: guarda cada `cada` ítems y commitea ──
    # En lugar de acumular todo en memoria y commiter al final (que pierde
    # todo el progreso si el proceso se cuelga o se corta), se va guardando
    # por lote. El DELETE de las claves ES viejas se ejecuta en el MISMO lote
    # del checkpoint (misma transacción) para mantener la sesión coherente.
    from scripts.ai_media.checkpoint import Checkpoint

    cp = Checkpoint(conn, cada=20, etiqueta=nombre)
    lote: list = []

    def _guardar_lote():
        """Guarda el lote acumulado: DELETE ES viejo + INSERT INTO EN, y se hace commit."""
        nonlocal lote
        if not lote:
            return
        ids_ok = [int(mid) for mid, _ in lote]
        conn.execute(
            f"DELETE FROM media_metadata WHERE key = ? AND media_id IN ({','.join('?' * len(ids_ok))})",
            [clave_es] + ids_ok,
        )
        conn.executemany(
            "INSERT OR REPLACE INTO media_metadata (media_id, key, value) "
            f"VALUES (?, '{clave_en}', ?)", lote)
        conn.commit()
        lote = []

    pool = ThreadPoolExecutor(max_workers=n_workers)
    futures = {pool.submit(_process_one, mid, fpath): (mid, fpath)
               for mid, fpath in rows}
    try:
        # El timeout NO es un presupuesto total del lote: wait() retorna al
        # primer futuro completado, así un lote legítimo de cualquier duración
        # corre hasta el final. Solo si NINGÚN futuro avanza dentro de
        # `timeout_future` segundos se considera el pool colgado de verdad y
        # se cancela guardando el progreso. La capa real anti-cuelgue es el
        # timeout por request en ollama_client.py (180s por llamada HTTP);
        # este wait() solo detecta un pool muerto sin cortar lotes largos.
        with tqdm(total=len(futures), desc=f"  {mostrar_label} (EN)",
                  unit="img", ncols=80) as pbar:
            pendientes = set(futures)
            while pendientes:
                done, pendientes = wait(pendientes, timeout=timeout_future,
                                        return_when=FIRST_COMPLETED)
                if not done:
                    # Ningún futuro avanzó en `timeout_future` s → colgado real.
                    log.warning(
                        "  Sin progreso durante %d s: cancelo el resto y "
                        "guardo lo procesado.", timeout_future)
                    for f in pendientes:
                        f.cancel()
                    _guardar_lote()
                    break
                for f in done:
                    result, data = f.result()
                    if result == "warning":
                        log.warning("  Archivo no encontrado: %s", data)
                        warnings += 1
                    elif result == "ok":
                        mid, dato = data
                        valor = (", ".join(dato) if es_lista and isinstance(dato, list)
                                 else (str(dato) if dato else ""))
                        if CONTEXTO["mostrar"] and valor:
                            tqdm.write(f"    [media {mid}] {valor}")
                        if valor:
                            lote.append((mid, valor))
                        ok += 1
                    else:
                        fpath, exc = data
                        log.warning("  ⚠ Error en imagen %s: %s", fpath, exc)
                        errors += 1
                    cp.contar()
                    if len(lote) >= cp.cada:
                        _guardar_lote()
                    pbar.update(1)
    except KeyboardInterrupt:
        log.warning("  ⚠ Interrupción detectada en el pool. Cancelando futuros...")
        pool.shutdown(wait=False, cancel_futures=True)
        _guardar_lote()
        cp.finalizar()
        stats["warnings"] += warnings
        log.info("  ⚠ Visión %s interrumpida: %d ok | %d errores "
                 "(progreso guardado).", nombre, ok, errors)
        stats[f"{nombre}_ok"] = ok
        stats[f"{nombre}_err"] = errors
        # Relanza para que el main lo capture (manejar_interrupcion).
        raise
    else:
        pool.shutdown(wait=True)
        _guardar_lote()
        cp.finalizar()

    stats["warnings"] += warnings
    log.info("  ✅ Visión %s: %d  |  Errores: %d", nombre, ok, errors)
    stats[f"{nombre}_ok"] = ok
    stats[f"{nombre}_err"] = errors


def _traducir_metadata(conn, mode, stats, nombre, clave_en, clave_es, paso,
                       modelo_traduccion="translategemma", motor="google"):
    """
    Fase B de keywords/descriptions: traduce EN → ES sobre la DB.

    Lee registros con clave_en y traduce con el pipeline NO-AI (glosario +
    motor clásico, google por defecto) o, si motor == "ollama", con el
    pipeline legacy (translategemma). Escribe el resultado en clave_es.

    Args:
        conn: conexión a la DB.
        mode: skip|update|replace.
        stats: dict de estadísticas.
        nombre: nombre del paso ("keywords" | "descriptions").
        clave_en: clave con el texto EN.
        clave_es: clave donde se escribe el ES.
        paso: "keywords" | "descriptions" | "ambos".
        modelo_traduccion: modelo de texto legacy (solo motor ollama).
        motor: motor de traducción: "google" (default) | "argos" |
               "glosario" (solo léxico) | "ollama" (legacy con IA).
    """
    log.info("  [Fase B] Traducción (%s → %s) [motor=%s]", clave_en, clave_es, motor)

    if mode == "skip":
        query = f"""
            SELECT m.id,
                   (SELECT value FROM media_metadata mm WHERE mm.media_id = m.id AND mm.key = '{clave_en}') AS v_en
            FROM media m
            WHERE EXISTS (SELECT 1 FROM media_metadata mm
                          WHERE mm.media_id = m.id AND mm.key = '{clave_en}')
              AND NOT EXISTS (SELECT 1 FROM media_metadata mm
                              WHERE mm.media_id = m.id AND mm.key = '{clave_es}')
            ORDER BY m.id
        """
    else:  # update / replace: retraduce todos los que tienen EN
        query = f"""
            SELECT m.id,
                   (SELECT value FROM media_metadata mm WHERE mm.media_id = m.id AND mm.key = '{clave_en}') AS v_en
            FROM media m
            WHERE EXISTS (SELECT 1 FROM media_metadata mm
                          WHERE mm.media_id = m.id AND mm.key = '{clave_en}')
            ORDER BY m.id
        """

    rows = conn.execute(query).fetchall()
    if not rows:
        log.info("  No hay registros para traducir.")
        return

    if motor == "ollama":
        _traducir_metadata_ollama(conn, rows, nombre, clave_es, paso,
                                  modelo_traduccion, stats)
    else:
        _traducir_metadata_glosario(conn, rows, nombre, clave_es, paso,
                                    motor, stats)


def _traducir_metadata_ollama(conn, rows, nombre, clave_es, paso,
                              modelo_traduccion, stats):
    """Traduce EN → ES con el pipeline legacy de Ollama (translategemma)."""
    from scripts.ai_media.traducir_metadata import traducir_llamada, leer_valor_db

    cliente = _crear_cliente_texto()
    ok = 0
    errors = 0

    # Checkpoint por lote: commit cada `cada` traducciones (antes un solo
    # commit al final que perdía todo el progreso si se cortaba la corrida).
    from scripts.ai_media.checkpoint import Checkpoint
    cp = Checkpoint(conn, cada=20, etiqueta=f"traduccion_{nombre}")

    for mid, v_en in tqdm(rows, desc=f"  Traduciendo {mostrar_label_nombre(paso)}",
                          unit="img", ncols=80):
        try:
            if paso == "keywords":
                kw_en = leer_valor_db(v_en)
                kw_es, _, _ = traducir_llamada(cliente, kw_en, "", paso, modelo_traduccion)
                if kw_es:
                    conn.execute(
                        "INSERT OR REPLACE INTO media_metadata (media_id, key, value) VALUES (?, ?, ?)",
                        (mid, clave_es, ", ".join(kw_es)))
                    ok += 1
                else:
                    log.warning("  ⚠ Sin traducción para media %s", mid)
                    errors += 1
            elif paso == "descriptions":
                _, desc_es, _ = traducir_llamada(cliente, [], v_en or "", paso, modelo_traduccion)
                if desc_es:
                    conn.execute(
                        "INSERT OR REPLACE INTO media_metadata (media_id, key, value) VALUES (?, ?, ?)",
                        (mid, clave_es, desc_es))
                    ok += 1
                else:
                    log.warning("  ⚠ Sin traducción para media %s", mid)
                    errors += 1
            else:  # ambos: lee las dos claves EN y escribe las dos ES
                fila_kw = conn.execute(
                    "SELECT value FROM media_metadata WHERE media_id=? AND key=?",
                    (mid, CLAVE_KW_EN)).fetchone()
                fila_desc = conn.execute(
                    "SELECT value FROM media_metadata WHERE media_id=? AND key=?",
                    (mid, CLAVE_DESC_EN)).fetchone()
                kw_en = leer_valor_db(fila_kw[0] if fila_kw else None)
                desc_en = (fila_desc[0] if fila_desc else "") or ""
                kw_es, desc_es, _ = traducir_llamada(
                    cliente, kw_en, desc_en, "ambos", modelo_traduccion)
                cambios = 0
                if kw_es:
                    conn.execute(
                        "INSERT OR REPLACE INTO media_metadata (media_id, key, value) VALUES (?, ?, ?)",
                        (mid, CLAVE_KW_ES, ", ".join(kw_es)))
                    cambios += 1
                if desc_es:
                    conn.execute(
                        "INSERT OR REPLACE INTO media_metadata (media_id, key, value) VALUES (?, ?, ?)",
                        (mid, CLAVE_DESC_ES, desc_es))
                    cambios += 1
                if cambios:
                    ok += 1
                else:
                    log.warning("  ⚠ Sin traducción para media %s", mid)
                    errors += 1
        except Exception as e:
            log.warning("  ⚠ Error traduciendo media %s: %s", mid, e)
            errors += 1
        cp.contar()

    cp.finalizar()
    log.info("  ✅ Traducción %s: %d  |  Errores: %d", nombre, ok, errors)
    stats[f"traduccion_{nombre}_ok"] = ok
    stats[f"traduccion_{nombre}_err"] = errors


def _traducir_metadata_glosario(conn, rows, nombre, clave_es, paso,
                                motor, stats):
    """Traduce EN → ES con glosario + motor clásico (sin Ollama)."""
    from scripts.ai_media.glosario import Glosario, crear_motor
    from scripts.ai_media.traducir_metadata import leer_valor_db

    glosario = Glosario()
    glosario.cargar()
    motor_instancia = crear_motor(motor)  # None para 'glosario'
    if motor_instancia is not None:
        glosario.motor = motor_instancia

    ok = 0
    errors = 0

    # Checkpoint por lote: commit cada `cada` traducciones (antes un solo
    # commit al final que perdía todo el progreso si se cortaba la corrida).
    from scripts.ai_media.checkpoint import Checkpoint
    cp = Checkpoint(conn, cada=20, etiqueta=f"traduccion_{nombre}")

    for mid, v_en in tqdm(rows, desc=f"  Traduciendo {mostrar_label_nombre(paso)}",
                          unit="img", ncols=80):
        try:
            if paso == "keywords":
                kw_en = leer_valor_db(v_en)
                traducidas, _ = glosario.traducir_keywords(kw_en)
                if traducidas:
                    conn.execute(
                        "INSERT OR REPLACE INTO media_metadata (media_id, key, value) VALUES (?, ?, ?)",
                        (mid, clave_es, ", ".join(traducidas)))
                    ok += 1
                else:
                    log.warning("  ⚠ Sin traducción para media %s", mid)
                    errors += 1
            elif paso == "descriptions":
                desc_es = glosario.traducir_descripcion(v_en or "")
                if desc_es:
                    conn.execute(
                        "INSERT OR REPLACE INTO media_metadata (media_id, key, value) VALUES (?, ?, ?)",
                        (mid, clave_es, desc_es))
                    ok += 1
                else:
                    log.warning("  ⚠ Sin traducción para media %s", mid)
                    errors += 1
            else:  # ambos: lee las dos claves EN y escribe las dos ES
                fila_kw = conn.execute(
                    "SELECT value FROM media_metadata WHERE media_id=? AND key=?",
                    (mid, CLAVE_KW_EN)).fetchone()
                fila_desc = conn.execute(
                    "SELECT value FROM media_metadata WHERE media_id=? AND key=?",
                    (mid, CLAVE_DESC_EN)).fetchone()
                kw_en = leer_valor_db(fila_kw[0] if fila_kw else None)
                desc_en = (fila_desc[0] if fila_desc else "") or ""
                cambios = 0
                traducidas, _ = glosario.traducir_keywords(kw_en)
                if traducidas:
                    conn.execute(
                        "INSERT OR REPLACE INTO media_metadata (media_id, key, value) VALUES (?, ?, ?)",
                        (mid, CLAVE_KW_ES, ", ".join(traducidas)))
                    cambios += 1
                desc_es = glosario.traducir_descripcion(desc_en)
                if desc_es:
                    conn.execute(
                        "INSERT OR REPLACE INTO media_metadata (media_id, key, value) VALUES (?, ?, ?)",
                        (mid, CLAVE_DESC_ES, desc_es))
                    cambios += 1
                if cambios:
                    ok += 1
                else:
                    log.warning("  ⚠ Sin traducción para media %s", mid)
                    errors += 1
        except Exception as e:
            log.warning("  ⚠ Error traduciendo media %s: %s", mid, e)
            errors += 1
        cp.contar()

    cp.finalizar()
    log.info("  ✅ Traducción %s: %d  |  Errores: %d", nombre, ok, errors)
    stats[f"traduccion_{nombre}_ok"] = ok
    stats[f"traduccion_{nombre}_err"] = errors


def mostrar_label_nombre(paso):
    """Etiqueta corta para la barra de progreso de traducción."""
    return {"keywords": "Keywords", "descriptions": "Descripciones",
            "ambos": "Keywords+Desc"}.get(paso, paso)


def run_keywords(conn, db_path, mode, stats, motor="google"):
    """Etiqueta imágenes con IA: visión (EN) + traducción (ES, motor clásico)."""
    log.info("Paso: keywords — Etiquetando imágenes con IA (EN → ES)")

    from scripts.ai_media.image_analysis import extraer_keywords

    # Fase A: visión EN → ia_keywords_en
    _procesar_vision(
        conn, mode, stats,
        nombre="keywords",
        fn_vision=extraer_keywords,
        clave_en=CLAVE_KW_EN,
        clave_es=CLAVE_KW_ES,
        mostrar_label="Keywords",
        es_lista=True,
    )
    # Fase B: traducción → ia_keywords (ES)
    _traducir_metadata(
        conn, mode, stats,
        nombre="keywords",
        clave_en=CLAVE_KW_EN,
        clave_es=CLAVE_KW_ES,
        paso="keywords",
        motor=motor,
    )


def run_descriptions(conn, db_path, mode, stats, motor="google"):
    """Describe imágenes con IA: visión (EN) + traducción (ES, motor clásico)."""
    log.info("Paso: descriptions — Describiendo imágenes con IA (EN → ES)")

    from scripts.ai_media.image_analysis import describir_imagen

    # Fase A: visión EN → ia_description_en
    _procesar_vision(
        conn, mode, stats,
        nombre="descriptions",
        fn_vision=describir_imagen,
        clave_en=CLAVE_DESC_EN,
        clave_es=CLAVE_DESC_ES,
        mostrar_label="Descripciones",
        es_lista=False,
    )
    # Fase B: traducción → ia_description (ES)
    _traducir_metadata(
        conn, mode, stats,
        nombre="descriptions",
        clave_en=CLAVE_DESC_EN,
        clave_es=CLAVE_DESC_ES,
        paso="descriptions",
        motor=motor,
    )


def run_combinado(conn, db_path, mode, stats, motor="google"):
    """Keywords + descripción en UNA llamada de visión (EN) + 1 de traducción (ES, motor clásico)."""
    log.info("Paso: combinado — Keywords + descripción (1 visión + 1 traducción por imagen)")

    from scripts.ai_media.image_analysis import analizar_imagen_completo

    helper = ModoHelper(mode)
    if mode == "replace":
        conn.execute(
            "DELETE FROM media_metadata WHERE key IN (?, ?, ?, ?)",
            (CLAVE_KW_EN, CLAVE_DESC_EN, CLAVE_KW_ES, CLAVE_DESC_ES),
        )
        conn.commit()

    if mode == "skip":
        query = f"""
            SELECT m.id, m.filepath_absoluto FROM media m
            WHERE m.type='image'
              AND (
                    NOT EXISTS (SELECT 1 FROM media_metadata mm WHERE mm.media_id = m.id AND mm.key = '{CLAVE_KW_EN}')
                 OR NOT EXISTS (SELECT 1 FROM media_metadata mm WHERE mm.media_id = m.id AND mm.key = '{CLAVE_DESC_EN}')
              )
        """
    else:
        query = "SELECT id, filepath_absoluto FROM media WHERE type='image'"

    rows = conn.execute(query).fetchall()
    if not rows:
        log.info("  No hay imágenes pendientes (visión combinada).")
        return

    from scripts.ai_media.image_analysis import MODELO_VISION_DEFAULT
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

    def _process_one(mid, fpath):
        if not os.path.isfile(fpath):
            return "warning", fpath
        try:
            res = analizar_imagen_completo(fpath, modelo=MODELO_VISION_DEFAULT)
            return "ok", (mid, res)
        except Exception as e:
            return "error", (fpath, e)

    ok = 0
    errors = 0
    warnings = 0
    n_workers = max(1, CONTEXTO.get("workers", 1))
    timeout_future = CONTEXTO.get("timeout_future", 300)

    # ── Checkpoint por lote: guarda keywords+descripción cada `cada` ítems ──
    from scripts.ai_media.checkpoint import Checkpoint
    cp = Checkpoint(conn, cada=20, etiqueta="combinado")
    lote_kw: list = []
    lote_desc: list = []

    def _guardar_lote():
        """Guarda el lote: DELETE ES viejos + INSERT INTO EN, y se hace commit."""
        nonlocal lote_kw, lote_desc
        ids_ok = set(int(mid) for mid, _ in lote_kw + lote_desc)
        if not ids_ok:
            return
        conn.execute(
            f"DELETE FROM media_metadata WHERE key IN (?, ?) "
            f"AND media_id IN ({','.join('?' * len(ids_ok))})",
            [CLAVE_KW_ES, CLAVE_DESC_ES] + list(ids_ok),
        )
        if lote_kw:
            conn.executemany(
                "INSERT OR REPLACE INTO media_metadata (media_id, key, value) "
                f"VALUES (?, '{CLAVE_KW_EN}', ?)", lote_kw)
        if lote_desc:
            conn.executemany(
                "INSERT OR REPLACE INTO media_metadata (media_id, key, value) "
                f"VALUES (?, '{CLAVE_DESC_EN}', ?)", lote_desc)
        conn.commit()
        lote_kw = []
        lote_desc = []

    pool = ThreadPoolExecutor(max_workers=n_workers)
    futures = {pool.submit(_process_one, mid, fpath): (mid, fpath)
               for mid, fpath in rows}
    try:
        # Mismo patrón anti-cuelgue que _procesar_vision: wait() retorna al
        # primer futuro completado (sin presupuesto total); solo cancela si
        # ningún futuro avanza en `timeout_future` s. La capa real anti-hang
        # es el timeout por request de ollama_client.py (180s por HTTP).
        with tqdm(total=len(futures), desc="  Combinado (EN)",
                  unit="img", ncols=80) as pbar:
            pendientes = set(futures)
            while pendientes:
                done, pendientes = wait(pendientes, timeout=timeout_future,
                                        return_when=FIRST_COMPLETED)
                if not done:
                    # Ningún futuro avanzó en `timeout_future` s → colgado real.
                    log.warning(
                        "  Sin progreso durante %d s: cancelo el resto y "
                        "guardo lo procesado.", timeout_future)
                    for f in pendientes:
                        f.cancel()
                    _guardar_lote()
                    break
                for f in done:
                    result, data = f.result()
                    if result == "warning":
                        log.warning("  Archivo no encontrado: %s", data)
                        warnings += 1
                    elif result == "ok":
                        mid, res = data
                        kw = res.get("keywords", [])
                        desc = res.get("description", "")
                        if CONTEXTO["mostrar"]:
                            tqdm.write(f"    [media {mid}] kw={kw} | desc={desc[:80]}...")
                        if kw:
                            lote_kw.append((mid, ", ".join(kw)))
                        if desc:
                            lote_desc.append((mid, desc))
                        ok += 1
                    else:
                        fpath, exc = data
                        log.warning("  ⚠ Error en imagen %s: %s", fpath, exc)
                        errors += 1
                    cp.contar()
                    if len(lote_kw) + len(lote_desc) >= cp.cada:
                        _guardar_lote()
                    pbar.update(1)
    except KeyboardInterrupt:
        log.warning("  ⚠ Interrupción detectada en el pool combinado. "
                    "Cancelando futuros...")
        pool.shutdown(wait=False, cancel_futures=True)
        _guardar_lote()
        cp.finalizar()
        stats["warnings"] += warnings
        log.info("  ⚠ Combinado interrumpido: %d ok | %d errores "
                 "(progreso guardado).", ok, errors)
        stats["combinado_ok"] = ok
        stats["combinado_err"] = errors
        # Relanza para que el main lo capture (manejar_interrupcion).
        raise
    else:
        pool.shutdown(wait=True)
        _guardar_lote()
        cp.finalizar()

    stats["warnings"] += warnings
    log.info("  ✅ Visión combinada: %d  |  Errores: %d", ok, errors)
    stats["combinado_ok"] = ok
    stats["combinado_err"] = errors

    # Fase B: traducción de ambos
    _traducir_metadata(
        conn, mode, stats,
        nombre="combinado",
        clave_en=CLAVE_KW_EN,
        clave_es=CLAVE_KW_ES,
        paso="ambos",
        motor=motor,
    )


def run_transcribe(conn, db_path, mode, stats):
    """Transcribe audios y videos con faster-whisper (con VAD y filtro de confianza)."""
    log.info("Paso: transcribe — Transcribiendo audios/videos")

    if mode == "replace":
        query = "SELECT id, filepath_absoluto FROM media WHERE type IN ('video', 'audio')"
    elif mode == "update":
        query = "SELECT id, filepath_absoluto FROM media WHERE type IN ('video', 'audio')"
    else:
        # skip: retoma pendientes. Considera "pendiente" a los archivos que aún
        # no tienen whisper_estado (ni tocados, ni corte a mitad de batch /
        # checkpoint): así cualquier corrida interrumpida se auto-recupera en la
        # siguiente pasada. El marcador de "terminado" es whisper_estado, porque
        # un archivo sin_voz queda con estado pero SIN whisper_segments (no
        # guardamos basura): por eso NO se usa whisper_segments para detectar
        # pendientes (eso re-transcribiría todos los sin_voz en cada corrida).
        query = """
            SELECT m.id, m.filepath_absoluto FROM media m
            WHERE m.type IN ('video', 'audio')
              AND NOT EXISTS (
                  SELECT 1 FROM media_metadata me
                  WHERE me.media_id = m.id AND me.key = 'whisper_estado'
              )
        """

    rows = conn.execute(query).fetchall()
    if not rows:
        log.info("  No hay audios/videos pendientes.")
        return

    try:
        from scripts.ai_media.transcribe import transcribir_audio, clasificar_estado
    except ImportError:
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
            from scripts.ai_media.transcribe import transcribir_audio, clasificar_estado
        except ImportError as e:
            log.error("  No se pudo importar transcribir_audio: %s", e)
            stats["errors"] += 1
            return

    from scripts.ai_media.checkpoint import Checkpoint

    ok = 0
    errors = 0
    cp = Checkpoint(conn, cada=20, etiqueta="transcribe")
    for mid, fpath in tqdm(rows, desc="  Transcribe", unit="arch", ncols=80):
        if not os.path.isfile(fpath):
            log.warning("  Archivo no encontrado: %s", fpath)
            stats["warnings"] += 1
            continue
        try:
            segmentos, info = transcribir_audio(
                fpath,
                modelo="small",
                # Detectar persona hablando y descartar ruido/silencio:
                vad_filter=True,
                vad_parameters={"min_speech_duration_ms": 300},
                # Cortar lazos de repetición típicos de alucinaciones:
                condition_on_previous_text=False,
                # Exigir texto confiado / inteligible:
                no_speech_threshold=0.6,
                compression_ratio_threshold=2.4,
                log_prob_threshold=-0.8,
                incluir_metricas=True,
            )
            estado = clasificar_estado(segmentos)
            # Guardar los segmentos SOLO si la transcripción es confiable (ok).
            # En sin_voz (ruido/silencio sin habla útil) no se acumula basura:
            # solo queda la marca whisper_estado = sin_voz.
            if estado == "ok" and segmentos:
                conn.execute(
                    "INSERT OR REPLACE INTO media_metadata (media_id, key, value) VALUES (?, 'whisper_segments', ?)",
                    (mid, json.dumps(segmentos, ensure_ascii=False)),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO media_metadata (media_id, key, value) VALUES (?, 'whisper_info', ?)",
                    (mid, json.dumps({
                        "language": str(info.language),
                        "language_probability": float(info.language_probability),
                    })),
                )
            conn.execute(
                "INSERT OR REPLACE INTO media_metadata (media_id, key, value) VALUES (?, 'whisper_estado', ?)",
                (mid, estado),
            )
            ok += 1
        except Exception as e:
            log.warning("  ⚠ Error transcribiendo %s: %s", fpath, e)
            errors += 1
        cp.contar()

    cp.finalizar()
    conn.commit()
    log.info("  ✅ Transcripciones: %d  |  Errores: %d", ok, errors)
    stats["transcribe_ok"] = ok
    stats["transcribe_err"] = errors


def run_keypoints(conn, db_path, mode, stats):
    """
    Puebla media_keypoints desde transcripciones almacenadas en media_metadata.
    Calcula timestamp_absolute como timestamp_utc + offset.
    """
    log.info("Paso: keypoints — Poblando keypoints desde transcripciones")

    if mode == "replace":
        # Replace: limpia TODO y regenera desde cero.
        # El clean se hace justo antes del insert (misma transacción).
        query = """
            SELECT m.id, m.filepath_absoluto, m.timestamp_utc, mm.value AS segments_json
            FROM media m
            JOIN media_metadata mm ON mm.media_id = m.id AND mm.key = 'whisper_segments'
        """
    elif mode == "update":
        # Update: regenera keypoints para medios que tienen transcripcion.
        # El clean se hace justo antes del insert (misma transacción).
        query = """
            SELECT m.id, m.filepath_absoluto, m.timestamp_utc, mm.value AS segments_json
            FROM media m
            JOIN media_metadata mm ON mm.media_id = m.id AND mm.key = 'whisper_segments'
        """
    else:
        # Solo medios transcritos que aun no tienen keypoints de transcripcion
        query = """
            SELECT m.id, m.filepath_absoluto, m.timestamp_utc, mm.value AS segments_json
            FROM media m
            JOIN media_metadata mm ON mm.media_id = m.id AND mm.key = 'whisper_segments'
            WHERE m.id NOT IN (
                SELECT DISTINCT media_id FROM media_keypoints
                WHERE key = 'transcription'
            )
        """

    rows = conn.execute(query).fetchall()

    if not rows:
        log.info("  No hay transcripciones pendientes para keypoints.")
        return

    inserted = 0
    errors = 0

    # En replace/update: limpiar SOLO los keypoints de transcripcion previos
    # (NO tocar contexto_*, escena, keyword: viven en media_keypoints también)
    if mode in ("replace", "update"):
        conn.execute("DELETE FROM media_keypoints WHERE key = 'transcription'")
        # No commit — se comitea al final con los inserts

    for mid, fpath, ts_utc, segments_json in tqdm(
        rows, desc="  Keypoints", unit="arch", ncols=80
    ):
        if not ts_utc:
            log.warning("  media id=%s no tiene timestamp_utc, skip.", mid)
            stats["warnings"] += 1
            continue

        try:
            dt_base = datetime.fromisoformat(ts_utc.replace("Z", "+00:00"))
            segmentos = json.loads(segments_json)
            batch = []
            for seg in segmentos:
                offset = seg.get("inicio", 0)
                texto = seg.get("texto", "").strip()
                if not texto:
                    continue
                ts_abs = (dt_base + timedelta(seconds=offset)).isoformat()
                batch.append((mid, offset, ts_abs, "transcription", texto, "whisper"))

            conn.executemany(
                "INSERT INTO media_keypoints (media_id, timestamp_offset_secs, timestamp_absolute, key, value, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                batch,
            )
            inserted += len(batch)
        except Exception as e:
            log.warning("  ⚠ Error generando keypoints para media id=%s: %s", mid, e)
            errors += 1

    conn.commit()
    log.info("  ✅ Keypoints insertados: %d  |  Errores: %d", inserted, errors)
    stats["keypoints_ok"] = inserted
    stats["keypoints_err"] = errors


def run_timestamps(conn, db_path, mode, stats):
    """
    Infiere timestamps faltantes agrupando por clúster (misma carpeta, mismo día)
    y ordenando por nombre de archivo. Interpola entre medios con timestamp conocido.
    """
    log.info("Paso: timestamps — Infiriendo timestamps faltantes")

    if mode in ("replace", "update"):
        # Marcar los inferidos para reprocesarlos
        conn.execute("""
            UPDATE media SET
                timestamp_original = NULL,
                timestamp_utc = NULL,
                timezone_note = NULL
            WHERE timezone_note LIKE 'inferido:%'
        """)
        conn.commit()

    # Medios sin timestamp_utc o con fallback
    rows = conn.execute("""
        SELECT id, filepath_absoluto, filepath_relativo, carpeta,
               timestamp_utc, timezone_note
        FROM media
        WHERE timestamp_utc IS NULL
           OR timezone_note LIKE '%fallback%'
           OR timezone_note LIKE '%no se pudo%'
        ORDER BY carpeta, filepath_relativo
    """).fetchall()

    if not rows:
        log.info("  No hay medios con timestamp mejorable.")
        return

    # Agrupar por carpeta
    from collections import defaultdict
    clusters = defaultdict(list)
    for row in rows:
        clusters[row[3]].append(row)  # row[3] = carpeta

    inferidos = 0
    sin_referencia = 0

    for carpeta, miembros in clusters.items():
        if not carpeta:
            continue

        # Ordenar por filepath_relativo (orden lexicográfico ≈ orden de captura)
        miembros.sort(key=lambda r: r[2])

        # Separar los que tienen timestamp real vs los que no
        conocidos = [(i, r) for i, r in enumerate(miembros) if r[4] and "fallback" not in (r[5] or "") and "no se pudo" not in (r[5] or "")]
        # conocidos: (indice_en_miembros, row)

        if len(conocidos) < 2:
            # Con menos de 2 referencias no podemos interpolar bien
            sin_referencia += len(miembros)
            continue

        # Interpolar: entre cada par de conocidos, distribuir los desconocidos
        for idx in range(len(conocidos) - 1):
            i1, r1 = conocidos[idx]
            i2, r2 = conocidos[idx + 1]

            def _as_aware_utc(v: str) -> datetime:
                """Parsea timestamp_utc y lo fuerza a aware UTC.

                El timestamp_utc de la DB está normalizado a UTC, pero por
                robustez se normaliza la 'Z' y, si quedó naive, se asume UTC
                (nunca la zona del sistema operativo). Sin esto, el
                astimezone(-3) de abajo usaría la zona del SO en vez de ART.
                """
                dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt

            t1 = _as_aware_utc(r1[4])
            t2 = _as_aware_utc(r2[4])
            gap = i2 - i1  # cuántos índices hay entre conocidos
            if gap <= 1:
                continue  # consecutivos, no hay nada que interpolar

            dt_desconocidos = (t2 - t1) / gap
            for j in range(1, gap):
                row = miembros[i1 + j]
                mid = row[0]
                t_inferido = t1 + dt_desconocidos * j
                t_orig = t_inferido.astimezone(timezone(timedelta(hours=-3)))
                conn.execute(
                    "UPDATE media SET timestamp_original=?, timestamp_utc=?, "
                    "timezone_note=? WHERE id=?",
                    (
                        t_orig.isoformat(),
                        t_inferido.isoformat(),
                        f"inferido: interpolado entre {r1[4]} y {r2[4]}",
                        mid,
                    ),
                )
                inferidos += 1

    conn.commit()
    log.info("  ✅ Timestamps inferidos: %d  |  Sin referencia suficiente: %d",
              inferidos, sin_referencia)
    stats["timestamps_ok"] = inferidos
    stats["timestamps_no_ref"] = sin_referencia


def run_gps(conn, db_path, mode, stats):
    """
    Infiere GPS desde medios cercanos en el tiempo que sí tienen coordenadas.
    Agrupa por fecha y asigna coordenadas interpoladas según el orden temporal.
    """
    log.info("Paso: gps — Infiriendo GPS desde medios cercanos")

    if mode in ("replace", "update"):
        # Marcar inferidos como NULL para reprocesarlos
        conn.execute("""
            UPDATE media SET latitude = NULL, longitude = NULL, altitude = NULL,
                             geolocation_source = NULL
            WHERE geolocation_source = 'inferido_tiempo'
        """)
        conn.commit()

    # Medios sin GPS pero con timestamp
    rows = conn.execute("""
        SELECT id, timestamp_utc, filepath_relativo
        FROM media
        WHERE latitude IS NULL AND timestamp_utc IS NOT NULL
        ORDER BY timestamp_utc
    """).fetchall()

    if not rows:
        log.info("  No hay medios sin GPS con timestamp.")
        return

    inferidos = 0
    sin_ref = 0

    for row in rows:
        mid, ts_utc, _ = row
        # Buscar el medio con GPS más cercano antes y después en el tiempo
        anterior = conn.execute("""
            SELECT latitude, longitude, timestamp_utc FROM media
            WHERE latitude IS NOT NULL AND timestamp_utc <= ?
            ORDER BY timestamp_utc DESC LIMIT 1
        """, (ts_utc,)).fetchone()

        siguiente = conn.execute("""
            SELECT latitude, longitude, timestamp_utc FROM media
            WHERE latitude IS NOT NULL AND timestamp_utc >= ?
            ORDER BY timestamp_utc ASC LIMIT 1
        """, (ts_utc,)).fetchone()

        if anterior and siguiente:
            # Interpolar linealmente entre las dos coordenadas
            lat1, lon1, t1 = anterior
            lat2, lon2, t2 = siguiente
            dt1 = datetime.fromisoformat(t1.replace("Z", "+00:00"))
            dt2 = datetime.fromisoformat(t2.replace("Z", "+00:00"))
            dt_target = datetime.fromisoformat(ts_utc.replace("Z", "+00:00"))

            if dt2 == dt1:
                lat, lon = lat1, lon1
            else:
                frac = (dt_target - dt1).total_seconds() / (dt2 - dt1).total_seconds()
                lat = lat1 + (lat2 - lat1) * frac
                lon = lon1 + (lon2 - lon1) * frac

            conn.execute(
                "UPDATE media SET latitude=?, longitude=?, geolocation_source=? WHERE id=?",
                (lat, lon, "inferido_tiempo", mid),
            )
            inferidos += 1
        elif anterior:
            # Solo hay referencia anterior
            conn.execute(
                "UPDATE media SET latitude=?, longitude=?, geolocation_source=? WHERE id=?",
                (anterior[0], anterior[1], "inferido_tiempo", mid),
            )
            inferidos += 1
        elif siguiente:
            conn.execute(
                "UPDATE media SET latitude=?, longitude=?, geolocation_source=? WHERE id=?",
                (siguiente[0], siguiente[1], "inferido_tiempo", mid),
            )
            inferidos += 1
        else:
            sin_ref += 1

    conn.commit()
    log.info("  ✅ GPS inferidos: %d  |  Sin referencia: %d", inferidos, sin_ref)
    stats["gps_ok"] = inferidos
    stats["gps_no_ref"] = sin_ref


# ==============================================================================
# Video metadata con ExifTool (backfill para videos ya ingestados)
# ==============================================================================

def check_video_metadata(conn) -> dict:
    """Cuenta videos sin metadatos de ExifTool (marca/modelo)."""
    total = conn.execute(
        "SELECT COUNT(*) FROM media WHERE type='video'"
    ).fetchone()[0]
    pendientes = conn.execute("""
        SELECT COUNT(*) FROM media m
        WHERE m.type='video'
          AND NOT EXISTS (
              SELECT 1 FROM media_metadata mm
              WHERE mm.media_id = m.id AND mm.key = 'xml_devicemanufacturer'
          )
    """).fetchone()[0]
    return {"total": total, "pendientes": pendientes, "hecho": total - pendientes}


def run_video_metadata(conn, db_path, mode, stats):
    """
    Backfill: corre ExifTool en videos ya ingestados para extraer
    marca/modelo de cámara, detección 360, y demás metadatos.
    """
    log.info("Paso: video_metadata — Extrayendo metadatos de videos con ExifTool")

    if mode == "replace":
        query = "SELECT id, filepath_absoluto FROM media WHERE type='video'"
    elif mode == "update":
        query = "SELECT id, filepath_absoluto FROM media WHERE type='video'"
    else:  # skip
        query = """
            SELECT m.id, m.filepath_absoluto FROM media m
            WHERE m.type='video'
              AND NOT EXISTS (
                  SELECT 1 FROM media_metadata mm
                  WHERE mm.media_id = m.id AND mm.key = 'xml_devicemanufacturer'
              )
        """

    rows = conn.execute(query).fetchall()
    if not rows:
        log.info("  No hay videos pendientes.")
        return

    # Buscar exiftool
    candidates = [
        "C:\\Program Files\\digiKam\\exiftool.exe",
        "C:\\Program Files\\exiftool.exe",
        "exiftool",
    ]
    exiftool_path = None
    import subprocess
    for c in candidates:
        if c == "exiftool":
            try:
                subprocess.run([c, "-ver"], capture_output=True, timeout=5)
                exiftool_path = c
                break
            except:
                continue
        elif os.path.isfile(c):
            exiftool_path = c
            break

    if not exiftool_path:
        log.warning("  exiftool no encontrado. No se pueden extraer metadatos de video.")
        stats["warnings"] += 1
        return

    from scripts.ingest import run_exiftool, detect_360

    ok = 0
    errors = 0
    for mid, fpath in tqdm(rows, desc="  Videos", unit="vid", ncols=80):
        if not os.path.isfile(fpath):
            log.warning("  Archivo no encontrado: %s", fpath)
            stats["warnings"] += 1
            continue
        try:
            meta = run_exiftool(exiftool_path, fpath)
            if not meta:
                continue

            # En replace/update: limpiar metadatos previos de este video
            if mode in ("replace", "update"):
                conn.execute(
                    "DELETE FROM media_metadata WHERE media_id = ? AND (key LIKE 'xml_%' OR key LIKE 'xmp_%' OR key = 'video_spherical_projection')",
                    (mid,),
                )
                conn.execute("UPDATE media SET subtype = NULL WHERE id = ?", (mid,))

            # Guardar metadatos en media_metadata
            for key, value in meta.items():
                if value is None:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO media_metadata (media_id, key, value) VALUES (?, ?, ?)",
                    (mid, key, str(value)),
                )

            # Detectar 360 y actualizar subtype
            if detect_360(meta):
                conn.execute(
                    "UPDATE media SET subtype = '360' WHERE id = ?",
                    (mid,),
                )

            # Si se detectó marca/modelo, poblar author si está vacío
            maker = meta.get("xml_devicemanufacturer") or meta.get("sony_device_manufacturer", "")
            model = meta.get("xml_devicemodelname") or meta.get("sony_device_modelName", "")
            if maker or model:
                cur = conn.execute("SELECT author FROM media WHERE id = ?", (mid,))
                existing = cur.fetchone()
                if not existing or not existing[0]:
                    author = f"{maker} {model}".strip()
                    if author:
                        conn.execute(
                            "UPDATE media SET author = ?, author_source = ? WHERE id = ?",
                            (author, "exif", mid),
                        )

            ok += 1
        except Exception as e:
            log.warning("  ⚠ Error en video id=%s: %s", mid, e)
            errors += 1

    conn.commit()
    log.info("  ✅ Videos procesados: %d  |  Errores: %d", ok, errors)
    stats["video_metadata_ok"] = ok
    stats["video_metadata_err"] = errors


# ==============================================================================
# Registro de pasos
# ==============================================================================

REGISTRY = {
    "colors": {
        "description": "Extraer colores dominantes de imágenes",
        "dependencies": [],
        "check": check_colors,
        "run": run_colors,
    },
    "keywords": {
        "description": "Etiquetar imágenes con IA (Ollama)",
        "dependencies": [],
        "check": check_keywords,
        "run": run_keywords,
    },
    "descriptions": {
        "description": "Describir imágenes con IA (Ollama)",
        "dependencies": [],
        "check": check_descriptions,
        "run": run_descriptions,
    },
    "combinado": {
        "description": "Keywords + descripción en UNA llamada (1 visión + 1 traducción)",
        "dependencies": [],
        "check": check_combinado,
        "run": run_combinado,
    },
    "transcribe": {
        "description": "Transcribir audios/videos con faster-whisper (VAD + confianza)",
        "dependencies": [],
        "check": check_transcribe,
        "run": run_transcribe,
    },
    "keypoints": {
        "description": "Poblar media_keypoints desde transcripciones",
        "dependencies": ["transcribe"],
        "check": check_keypoints,
        "run": run_keypoints,
    },
    "timestamps": {
        "description": "Inferir timestamps faltantes por clúster + orden",
        "dependencies": [],
        "check": check_timestamps,
        "run": run_timestamps,
    },
    "gps": {
        "description": "Inferir GPS desde medios cercanos en el tiempo",
        "dependencies": [],
        "check": check_gps,
        "run": run_gps,
    },
    "video_metadata": {
        "description": "Extraer metadatos de cámara y 360 con ExifTool en videos",
        "dependencies": [],
        "check": check_video_metadata,
        "run": run_video_metadata,
    },
}

DEP_ORDER = ["colors", "keywords", "descriptions", "combinado", "transcribe", "keypoints",
             "timestamps", "gps", "video_metadata"]

PASOS_IA = {"keywords", "descriptions", "combinado"}
"""Pasos que requieren Ollama (vision) y usan --workers para concurrencia.
Los pasos locales (colors, keypoints, timestamps, gps, video_metadata) y
transcribe (faster-whisper local) NO usan Ollama ni workers.
"""

# Contexto global compartido con las funciones run_* (evita cambiar la firma de todas)
CONTEXTO: dict = {
    "mostrar": True,
    "workers": 1,
    # Timeout (segundos) de ventana SIN progreso en los pools de visión: si
    # ningún futuro se completa en ese lapso se cancela el pool y se guarda lo
    # procesado (patrón wait/FIRST_COMPLETED en _procesar_vision/run_combinado).
    # El timeout real por request vive en ollama_client.py (180s por HTTP).
    "timeout_future": 300,
}


def listar_pasos():
    """Muestra los pasos disponibles con su estado."""
    print("Pasos disponibles:\n")
    for name in DEP_ORDER:
        meta = REGISTRY[name]
        deps = meta["dependencies"]
        dep_str = f" (requiere: {', '.join(deps)})" if deps else ""
        print(f"  {name:15s}  {meta['description']}{dep_str}")
    print()
    print("Modos:")
    print("  skip     Saltar medios que ya tienen el dato procesado")
    print("  update   Re-ejecutar el paso (actualiza lo existente)")
    print("  replace  Borrar todo lo generado y regenerar desde cero")


def check_dependencias(pasos_seleccionados: list[str]) -> list[str]:
    """Verifica dependencias y las agrega si faltan."""
    result = set(pasos_seleccionados)
    for paso in pasos_seleccionados:
        deps = REGISTRY[paso]["dependencies"]
        for d in deps:
            if d not in result:
                log.warning("  %s requiere %s — se agrega automáticamente.", paso, d)
                result.add(d)
    # Devolver en orden de dependencia
    return [p for p in DEP_ORDER if p in result]


# ==============================================================================
# Main
# ==============================================================================

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Mejora la base de datos de Flujos con pasos de post-procesamiento",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python scripts/improve_db.py                              # todos los pasos (skip)
  python scripts/improve_db.py --steps colors,keywords      # solo esos
  python scripts/improve_db.py --steps keypoints --mode replace  # regenerar keypoints
  python scripts/improve_db.py --list                       # listar pasos
  python scripts/improve_db.py --db db/flujos.db            # DB personalizada
  python scripts/improve_db.py --steps keywords,descriptions            # muestra en vivo por default
  python scripts/improve_db.py --steps keywords,descriptions --no-mostrar  # silencioso
        """,
    )
    parser.add_argument(
        "--steps",
        help="Pasos a ejecutar separados por coma (default: todos)",
    )
    parser.add_argument(
        "--mode",
        default="skip",
        choices=["skip", "update", "replace"],
        help="Modo de ejecución (default: skip)",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Ruta a la base de datos (default: ./db/flujos.db)",
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="Listar pasos disponibles",
    )
    parser.add_argument(
        "--no-mostrar",
        action="store_false",
        dest="mostrar",
        help="NO mostrar en vivo keywords/descripciones imagen por imagen (default: mostrar)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Cantidad de requests concurrentes a Ollama (default: 1 — ¡2+ puede colgar/desestabilizar el modelo!)",
    )

    args = parser.parse_args(argv)

    if args.list:
        listar_pasos()
        return

    # Resolver DB
    db_path = resolver_db(args.db)
    if not os.path.isfile(db_path):
        log.error("Base de datos no encontrada: %s", db_path)
        sys.exit(1)

    # Determinar pasos
    if args.steps:
        pasos = [s.strip() for s in args.steps.split(",") if s.strip()]
        invalidos = [p for p in pasos if p not in REGISTRY]
        if invalidos:
            log.error("Pasos inválidos: %s", ", ".join(invalidos))
            log.error("Usá --list para ver los pasos disponibles.")
            sys.exit(1)
    else:
        pasos = list(DEP_ORDER)

    # Resolver dependencias
    pasos = check_dependencias(pasos)
    log.info("Pasos a ejecutar: %s", ", ".join(pasos))
    log.info("Modo: %s", args.mode)
    CONTEXTO["mostrar"] = args.mostrar
    if args.workers < 1:
        log.error("--workers debe ser >= 1")
        sys.exit(1)
    CONTEXTO["workers"] = args.workers
    pasos_con_ia = [p for p in pasos if p in PASOS_IA]
    if pasos_con_ia:
        log.info("Workers: %d (requests concurrentes a Ollama — pasos: %s)",
                 args.workers, ", ".join(pasos_con_ia))

    conn = abrir(db_path)

    # Verificar que la tabla media existe
    try:
        conn.execute("SELECT COUNT(*) FROM media").fetchone()
    except sqlite3.OperationalError as e:
        log.error("Error: la DB no tiene la tabla 'media'. ¿Ejecutaste ingest primero?")
        log.error("  %s", e)
        conn.close()
        sys.exit(1)

    # Reportar trabajo pendiente
    print()
    log.info("=== ESTADO PREVIO ===")
    for paso in pasos:
        meta = REGISTRY[paso]
        try:
            chk = meta["check"](conn)
            pct = (chk["hecho"] / chk["total"] * 100) if chk["total"] else 100
            print(f"  {paso:15s}  {chk['pendientes']:>5d} pendientes  ({chk['hecho']:>5d}/{chk['total']:>5d} = {pct:5.1f}%)")
        except Exception as e:
            print(f"  {paso:15s}  no se pudo verificar: {e}")
    print()

    # Ejecutar pasos
    # Envuelto en manejar_interrupcion: si el usuario corta con Ctrl+C, se
    # commitean los pendientes de la conexión y se sale con mensaje claro
    # (sys.exit(130)), sin traceback. Los pasos ya commitean por checkpoint.
    from scripts.ai_media.checkpoint import manejar_interrupcion

    stats = {"warnings": 0, "errors": 0}
    with manejar_interrupcion(conn=conn, etiqueta="improve_db"):
        for paso in pasos:
            print()
            meta = REGISTRY[paso]
            try:
                meta["run"](conn, db_path, args.mode, stats)
            except Exception as e:
                log.error("  ❌ Error en paso '%s': %s", paso, e)
                stats["errors"] += 1

    # Resumen final
    print()
    log.info("=" * 50)
    log.info("  IMPROVE DB COMPLETADO")
    log.info("=" * 50)
    # Sumar errores de pasos completos (stats["errors"]) + errores individuales
    # de cada paso (stats["<paso>_err"]) — antes solo se contaban los primeros,
    # por lo que el resumen decía "Errores: 0" aunque fallaran cientos de archivos.
    errores_individuales = sum(
        v for k, v in stats.items()
        if k.endswith("_err") and isinstance(v, int)
    )
    log.info("  Advertencias:  %d", stats.get("warnings", 0))
    log.info("  Errores:       %d", stats.get("errors", 0))
    if errores_individuales:
        log.info("    (de los cuales %d son errores por archivo/medio)", errores_individuales)
    else:
        log.info("    (sin errores individuales por archivo/medio)")

    conn.close()


if __name__ == "__main__":
    main()
