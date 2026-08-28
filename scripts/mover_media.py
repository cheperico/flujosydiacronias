#!/usr/bin/env python3
"""
mover_media.py - Mover o copiar archivos de medios y actualizar la DB.

Dos modalidades:
  mover  → mueve físicamente los archivos a nueva ubicación y actualiza
           las rutas en la DB automáticamente.
  copiar → copia los archivos a nueva ubicación y pregunta si se actualiza
           la DB con las nuevas rutas (para backup o nueva raíz de ingest).

Uso:
    # Mover archivos a nueva raíz (actualiza DB)
    python scripts/mover_media.py --new-root E:/NuevaCarpeta --mode mover

    # Mover en seco (previsualizar)
    python scripts/mover_media.py --new-root E:/NuevaCarpeta --mode mover --dry-run

    # Copiar archivos a nueva ubicación (pregunta si actualiza DB)
    python scripts/mover_media.py --new-root E:/Backup --mode copiar

    # Copiar y actualizar DB automáticamente (sin preguntar)
    python scripts/mover_media.py --new-root E:/Backup --mode copiar --update-db

    # Con DB personalizada
    python scripts/mover_media.py --new-root E:/NuevaCarpeta --mode mover --db otra.db
"""

import argparse
import logging
import os
import shutil
import sqlite3
import sys
from datetime import datetime

from db.util import abrir, resolver_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mover_media")

# Extensiones de sidecar conocidas (case-insensitive)
SIDECAR_EXTS = [".AAE", ".aae", ".json", ".xml", ".XMP", ".xmp"]


# ── Helpers ──────────────────────────────────────────────────────────────


def normalizar_ruta(p: str) -> str:
    """Normaliza separadores y elimina barra final."""
    p = os.path.normpath(p)
    if len(p) > 2 and p.endswith(os.sep):
        p = p[:-1]
    return p


def obtener_medios(conn) -> list[tuple[int, str, str]]:
    """
    Retorna lista de (id, filepath_absoluto, filepath_relativo) para todos los medios.
    """
    cur = conn.execute(
        "SELECT id, filepath_absoluto, filepath_relativo FROM media ORDER BY id"
    )
    return cur.fetchall()


def obtener_sidecars(conn) -> list[tuple[int, str]]:
    """Retorna lista de (id, sidecar_xml) para medios que tienen sidecar."""
    cur = conn.execute(
        "SELECT id, sidecar_xml FROM media WHERE sidecar_xml IS NOT NULL"
    )
    return cur.fetchall()


def calcular_nueva_ruta(abs_path: str, old_root: str, new_root: str) -> str:
    """Calcula la nueva ruta absoluta reemplazando old_root por new_root."""
    old_norm = normalizar_ruta(old_root)
    if abs_path.startswith(old_norm):
        return abs_path.replace(old_norm, new_root, 1)
    return abs_path


def calcular_nuevo_relativo(abs_path: str, new_root: str) -> str:
    """Calcula la nueva ruta relativa al new_root."""
    return os.path.relpath(abs_path, new_root)


# ── Operaciones con la DB ────────────────────────────────────────────────


def previsualizar_movimiento(
    conn, old_root: str, new_root: str
) -> list[dict]:
    """Muestra los cambios que se harían, sin escribir nada."""
    old_norm = normalizar_ruta(old_root)
    new_norm = normalizar_ruta(new_root)
    cambios = []

    medios = obtener_medios(conn)
    for mid, abs_path, rel_path in medios:
        if abs_path.startswith(old_norm):
            nueva_abs = calcular_nueva_ruta(abs_path, old_norm, new_norm)
            nuevo_rel = calcular_nuevo_relativo(nueva_abs, new_norm)
            cambios.append(
                {
                    "id": mid,
                    "accion": "mover",
                    "archivo": os.path.basename(abs_path),
                    "antes_abs": abs_path,
                    "nueva_abs": nueva_abs,
                    "antes_rel": rel_path,
                    "nuevo_rel": nuevo_rel,
                }
            )

    sidecars = obtener_sidecars(conn)
    for mid, sc_rel in sidecars:
        sc_abs = os.path.normpath(os.path.join(old_norm, sc_rel))
        if sc_abs.startswith(old_norm):
            nueva_sc_abs = sc_abs.replace(old_norm, new_norm, 1)
            nueva_sc_rel = os.path.relpath(nueva_sc_abs, new_norm)
            cambios.append(
                {
                    "id": mid,
                    "accion": "sidecar",
                    "archivo": os.path.basename(sc_abs),
                    "antes_abs": sc_abs,
                    "nueva_abs": nueva_sc_abs,
                    "antes_rel": sc_rel,
                    "nuevo_rel": nueva_sc_rel,
                }
            )

    return cambios


def ejecutar_movimiento(conn, old_root: str, new_root: str) -> dict:
    """
    Mueve archivos físicamente y actualiza la DB.
    Retorna estadísticas del movimiento.
    """
    old_norm = normalizar_ruta(old_root)
    new_norm = normalizar_ruta(new_root)
    stats = {
        "movidos": 0,
        "copiados": 0,
        "errores": 0,
        "skip": 0,
        "sidecars": 0,
        "colisiones": 0,
    }

    medios = obtener_medios(conn)
    for mid, abs_path, rel_path in medios:
        if not abs_path.startswith(old_norm):
            stats["skip"] += 1
            continue

        nueva_abs = calcular_nueva_ruta(abs_path, old_norm, new_norm)
        nuevo_rel = calcular_nuevo_relativo(nueva_abs, new_norm)

        # Verificar que el archivo existe
        if not os.path.isfile(abs_path):
            log.warning("  Archivo no encontrado, saltando: %s", abs_path)
            stats["skip"] += 1
            continue

        # Resolver colisión de nombre: si ya existe, agregar sufijo _1, _2, etc.
        destino_final = nueva_abs
        if os.path.exists(destino_final):
            base_name = os.path.splitext(os.path.basename(nueva_abs))[0]
            ext = os.path.splitext(nueva_abs)[1]
            dir_destino = os.path.dirname(nueva_abs)
            counter = 1
            while True:
                destino_final = os.path.join(
                    dir_destino, f"{base_name}_{counter}{ext}"
                )
                if not os.path.exists(destino_final):
                    break
                counter += 1
            log.warning(
                "  Colisión de nombre: %s -> %s",
                os.path.basename(nueva_abs),
                os.path.basename(destino_final),
            )
            stats["colisiones"] += 1

        # Crear directorio destino si no existe
        os.makedirs(os.path.dirname(destino_final), exist_ok=True)

        # Mover archivo
        try:
            shutil.move(abs_path, destino_final)
            stats["movidos"] += 1
        except OSError as e:
            log.warning("  Error moviendo %s: %s", abs_path, e)
            stats["errores"] += 1
            continue

        # Actualizar sidecar si existe (mismo directorio fuente, mismo nombre base)
        if rel_path:
            base = os.path.splitext(os.path.basename(abs_path))[0]
            dir_origen = os.path.dirname(abs_path)
            dir_destino_final = os.path.dirname(destino_final)
            for ext in SIDECAR_EXTS:
                sc_origen = os.path.join(dir_origen, base + ext)
                if os.path.isfile(sc_origen):
                    sc_destino = os.path.join(
                        dir_destino_final,
                        os.path.splitext(os.path.basename(destino_final))[0] + ext,
                    )
                    if not os.path.exists(sc_destino):
                        try:
                            shutil.move(sc_origen, sc_destino)
                            stats["sidecars"] += 1
                        except OSError:
                            pass

        # Actualizar DB con la ruta final (puede diferir de nueva_abs si hubo colisión)
        ruta_final_db = destino_final
        rel_final_db = calcular_nuevo_relativo(ruta_final_db, new_norm)
        carpeta_final = os.path.basename(os.path.dirname(ruta_final_db))
        if os.path.dirname(ruta_final_db) == new_norm:
            carpeta_final = None
        conn.execute(
            """
            UPDATE media
            SET filepath_absoluto = ?, filepath_relativo = ?, carpeta = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (ruta_final_db, rel_final_db, carpeta_final, mid),
        )

        # Actualizar sidecar_xml en DB (relativo al nuevo root)
        if rel_path:
            sc_rel_actual = conn.execute(
                "SELECT sidecar_xml FROM media WHERE id = ?", (mid,)
            ).fetchone()
            if sc_rel_actual and sc_rel_actual[0]:
                sc_abs_old = os.path.normpath(os.path.join(old_norm, sc_rel_actual[0]))
                if sc_abs_old.startswith(old_norm) and os.path.isfile(sc_abs_old):
                    sc_nueva_abs = sc_abs_old.replace(old_norm, new_norm, 1)
                    sc_nueva_rel = os.path.relpath(sc_nueva_abs, new_norm)
                    conn.execute(
                        "UPDATE media SET sidecar_xml = ?, updated_at = datetime('now') WHERE id = ?",
                        (sc_nueva_rel, mid),
                    )

    conn.commit()
    return stats


def ejecutar_copia(
    conn, old_root: str, new_root: str, update_db: bool = False
) -> dict:
    """
    Copia archivos a nueva ubicación. Si update_db=True, actualiza las rutas en DB.
    Si update_db=False, solo copia archivos (backup).
    """
    old_norm = normalizar_ruta(old_root)
    new_norm = normalizar_ruta(new_root)
    stats = {
        "copiados": 0,
        "errores": 0,
        "skip": 0,
        "sidecars": 0,
        "colisiones": 0,
    }

    medios = obtener_medios(conn)
    for mid, abs_path, rel_path in medios:
        if not abs_path.startswith(old_norm):
            stats["skip"] += 1
            continue

        nueva_abs = calcular_nueva_ruta(abs_path, old_norm, new_norm)
        nuevo_rel = calcular_nuevo_relativo(nueva_abs, new_norm)

        # Verificar que el archivo existe
        if not os.path.isfile(abs_path):
            log.warning("  Archivo no encontrado, saltando: %s", abs_path)
            stats["skip"] += 1
            continue

        # Resolver colisión de nombre: si ya existe, agregar sufijo _1, _2, etc.
        destino_final = nueva_abs
        if os.path.exists(destino_final):
            base_name = os.path.splitext(os.path.basename(nueva_abs))[0]
            ext = os.path.splitext(nueva_abs)[1]
            dir_destino = os.path.dirname(nueva_abs)
            counter = 1
            while True:
                destino_final = os.path.join(
                    dir_destino, f"{base_name}_{counter}{ext}"
                )
                if not os.path.exists(destino_final):
                    break
                counter += 1
            log.warning(
                "  Colisión de nombre: %s -> %s",
                os.path.basename(nueva_abs),
                os.path.basename(destino_final),
            )
            stats["colisiones"] += 1

        # Crear directorio destino si no existe
        os.makedirs(os.path.dirname(destino_final), exist_ok=True)

        # Copiar archivo
        try:
            shutil.copy2(abs_path, destino_final)
            stats["copiados"] += 1
        except OSError as e:
            log.warning("  Error copiando %s: %s", abs_path, e)
            stats["errores"] += 1
            continue

        # Copiar sidecar si existe (mismo directorio fuente)
        if rel_path:
            base = os.path.splitext(os.path.basename(abs_path))[0]
            dir_origen = os.path.dirname(abs_path)
            dir_destino_final = os.path.dirname(destino_final)
            for ext in SIDECAR_EXTS:
                sc_origen = os.path.join(dir_origen, base + ext)
                sc_destino = os.path.join(
                    dir_destino_final,
                    os.path.splitext(os.path.basename(destino_final))[0] + ext,
                )
                if os.path.isfile(sc_origen) and not os.path.exists(sc_destino):
                    try:
                        shutil.copy2(sc_origen, sc_destino)
                        stats["sidecars"] += 1
                    except OSError:
                        pass

        # Actualizar DB si se solicita
        if update_db:
            ruta_final_db = destino_final
            rel_final_db = calcular_nuevo_relativo(ruta_final_db, new_norm)
            carpeta_final = os.path.basename(os.path.dirname(ruta_final_db))
            if os.path.dirname(ruta_final_db) == new_norm:
                carpeta_final = None
            conn.execute(
                """
                UPDATE media
                SET filepath_absoluto = ?, filepath_relativo = ?, carpeta = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (ruta_final_db, rel_final_db, carpeta_final, mid),
            )

            # Actualizar sidecar_xml en DB
            if rel_path:
                sc_rel_actual = conn.execute(
                    "SELECT sidecar_xml FROM media WHERE id = ?", (mid,)
                ).fetchone()
                if sc_rel_actual and sc_rel_actual[0]:
                    sc_abs_old = os.path.normpath(os.path.join(old_norm, sc_rel_actual[0]))
                    if sc_abs_old.startswith(old_norm) and os.path.isfile(sc_abs_old):
                        sc_nueva_abs = sc_abs_old.replace(old_norm, new_norm, 1)
                        sc_nueva_rel = os.path.relpath(sc_nueva_abs, new_norm)
                        conn.execute(
                            "UPDATE media SET sidecar_xml = ?, updated_at = datetime('now') WHERE id = ?",
                            (sc_nueva_rel, mid),
                        )

    if update_db:
        conn.commit()

    return stats


# ── Procesamiento principal ──────────────────────────────────────────────


def procesar(
    db_path: str,
    new_root: str,
    mode: str = "mover",
    old_root: str | None = None,
    dry_run: bool = False,
    update_db: bool = False,
):
    """
    Pipeline principal: mueve o copia archivos de medios.

    Args:
        db_path: Ruta a la DB
        new_root: Nueva raíz de archivos
        mode: 'mover' o 'copiar'
        old_root: Raíz anterior (si no se lee de DB)
        dry_run: Si True, solo previsualiza
        update_db: Si True y mode='copiar', actualiza DB con nuevas rutas
    """
    conn = abrir(db_path)

    # Obtener old_root de la DB si no se proporcionó
    if not old_root:
        row = conn.execute(
            "SELECT value FROM config WHERE key = 'ingest_root'"
        ).fetchone()
        old_root = row[0] if row else None

    if not old_root:
        log.error(
            "No se encontró ingest_root en la DB. Use --old-root para indicarlo."
        )
        conn.close()
        return

    old_norm = normalizar_ruta(old_root)
    new_norm = normalizar_ruta(new_root)

    log.info("Raíz anterior: %s", old_norm)
    log.info("Raíz nueva:    %s", new_norm)
    log.info("Modo:          %s", mode)
    log.info("")

    if old_norm == new_norm:
        log.info("Las raíces son iguales. No hay nada que hacer.")
        conn.close()
        return

    if dry_run:
        log.info("=== MODO DRY RUN — No se moverá/copiará nada ===")
        cambios = previsualizar_movimiento(conn, old_norm, new_norm)
        if not cambios:
            log.info("No hay archivos que cambiarían.")
        else:
            for c in cambios:
                if c["accion"] == "sidecar":
                    log.info(
                        "  [sidecar] #%d %s", c["id"], c["archivo"]
                    )
                else:
                    log.info(
                        "  [#%d] %s", c["id"], c["archivo"]
                    )
                log.info("    %s", c["antes_abs"])
                log.info("    -> %s", c["nueva_abs"])
            log.info(
                "\n  Total: %d archivos a %s, %d sidecars",
                sum(1 for c in cambios if c["accion"] != "sidecar"),
                "mover" if mode == "mover" else "copiar",
                sum(1 for c in cambios if c["accion"] == "sidecar"),
            )
        conn.close()
        return

    if mode == "mover":
        log.info("=== MOVIENDO ARCHIVOS ===")
        log.info("")
        stats = ejecutar_movimiento(conn, old_norm, new_norm)
        log.info(
            "\n  Movidos: %d | Colisiones: %d | Sidecars: %d | Errores: %d | Saltados: %d",
            stats["movidos"],
            stats["colisiones"],
            stats["sidecars"],
            stats["errores"],
            stats["skip"],
        )
        log.info("  ingest_root actualizado a: %s", new_norm)

    elif mode == "copiar":
        # Para copiar, preguntar si actualizar DB salvo que se indique --update-db
        if not update_db:
            log.info("=== COPIANDO ARCHIVOS ===")
            log.info("")
            log.info("Modo copia (sólo copia archivos, no actualiza DB).")
            log.info("Para actualizar la DB con las nuevas rutas, use --update-db.")
            log.info("")
        else:
            log.info("=== COPIANDO ARCHIVOS (con actualización de DB) ===")
            log.info("")

        stats = ejecutar_copia(conn, old_norm, new_norm, update_db=update_db)
        log.info(
            "\n  Copiados: %d | Colisiones: %d | Sidecars: %d | Errores: %d | Saltados: %d",
            stats["copiados"],
            stats["colisiones"],
            stats["sidecars"],
            stats["errores"],
            stats["skip"],
        )

        if not update_db:
            log.info("  DB NO actualizada (modo copia sin --update-db).")

    conn.close()


# ── CLI ──────────────────────────────────────────────────────────────────


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Mover o copiar archivos de medios y actualizar la DB.",
        epilog="""
Ejemplos:
  python scripts/mover_media.py --new-root E:/NuevaCarpeta --mode mover
  python scripts/mover_media.py --new-root E:/Backup --mode copiar --update-db
  python scripts/mover_media.py --new-root E:/NuevaCarpeta --mode mover --dry-run
""",
    )
    parser.add_argument(
        "--new-root",
        required=True,
        help="Nueva raíz donde están (o estarán) los archivos",
    )
    parser.add_argument(
        "--old-root",
        default=None,
        help="Raíz anterior (se lee de DB por defecto)",
    )
    parser.add_argument(
        "--mode",
        choices=["mover", "copiar"],
        default="mover",
        help="mover=mueve archivos + actualiza DB; copiar=copia archivos "
        "(pregunta si actualiza DB, o usar --update-db)",
    )
    parser.add_argument(
        "--update-db",
        action="store_true",
        help="En modo copiar: actualiza las rutas en la DB automáticamente "
        "(sin preguntar)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Sólo previsualizar")
    parser.add_argument("--db", default=None, help="Ruta a la DB (default: db/flujos.db)")
    args = parser.parse_args(argv)

    db_path = resolver_db(args.db)

    if not os.path.isfile(db_path):
        log.error("Base de datos no encontrada: %s", db_path)
        sys.exit(1)

    new_root = normalizar_ruta(args.new_root)

    if not os.path.isdir(new_root):
        log.warning("La nueva raíz '%s' no existe o no es un directorio.", new_root)
        if args.mode == "mover":
            confirmar = input(
                f"  ¿Continuar de todos modos? (s/n) [n]: "
            ).strip()
            if confirmar.lower() != "s":
                log.info("Cancelado.")
                return

    procesar(
        db_path=db_path,
        new_root=new_root,
        mode=args.mode,
        old_root=args.old_root,
        dry_run=args.dry_run,
        update_db=args.update_db,
    )


if __name__ == "__main__":
    main()