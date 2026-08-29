#!/usr/bin/env python3
"""
consolidar_medios.py - Consolida medios de múltiples raíces en una estructura
unificada y actualiza la DB (Opción A: preparar traslado a otra computadora).

Contexto: la DB tiene medios dispersos en varias raíces absolutas (export de
Telegram, carpetas de testeo, etc.) mientras que `ingest_root` apunta a una
sola. Para llevar todo a otra PC hace falta consolidar primero.

Este script:
  1. Detecta TODAS las raíces absolutas donde viven los medios de la DB.
  2. Para cada raíz, calcula una subcarpeta destino dentro de `--new-root`.
  3. Mueve (o copia) los archivos y actualiza la DB con las nuevas rutas.
  4. Actualiza `config.ingest_root` al nuevo root.

Uso:
    # Previsualizar el plan de consolidación (sin pedir destino)
    python scripts/consolidar_medios.py --dry-run

    # Copiar todo como BACKUP (pregunta destino + si la DB apunta a originales o a los nuevos)
    python scripts/consolidar_medios.py

    # Copiar y apuntar DB a los nuevos archivos (sin preguntar)
    python scripts/consolidar_medios.py --new-root G:/Flujos/Medios --mode copiar --update-db

    # Mover archivos (borra originales) y actualizar DB
    python scripts/consolidar_medios.py --new-root G:/Flujos/Medios --mode mover

Comportamiento:
  - Sin --new-root: se pide el destino interactivamente al ejecutar.
  - Modo copiar: deja los originales intactos. Si no se pasa --update-db,
    pregunta si la DB sigue apuntando a los originales (backup puro) o
    pasa a apuntar a los nuevos archivos copiados.
  - Modo mover: reubica los archivos y actualiza la DB (no hay backup).
  - La estructura de subcarpetas de cada raíz se preserva
    (ej: telegram/photos/..., telegram/voice_messages/...).
"""

import argparse
import logging
import os
import re
import sys

# Asegurar raíz del proyecto en sys.path para ejecución standalone
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.util import abrir, resolver_db
from scripts.mover_media import (
    normalizar_ruta,
    obtener_medios,
    ejecutar_movimiento,
    ejecutar_copia,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("consolidar_medios")

# Patrones de raíz conocidos — maneja \ y / (Windows/Posix)
_PATRON_RAIZ = re.compile(r"[/\\](Testeo_\d+|ChatExport_[\d\-]+)(?=[/\\]|$)", re.IGNORECASE)


def _detectar_raiz(abs_path: str) -> str:
    r"""
    Devuelve la raíz "origen" de un path absoluto:
      - Si contiene patrón conocido (Testeo_N, ChatExport_fecha) incluye ese nivel.
      - Si no, usa 4 niveles (C:\ + 3 carpetas) para no fragmentar.
    """
    norm = os.path.normpath(abs_path)
    m = _PATRON_RAIZ.search(norm)
    if m:
        return norm[: m.end()]
    parts = re.split(r"[\\/]+", norm)
    n = min(4, len(parts))
    if parts and parts[0].endswith(":"):
        # Windows drive: C: + Users\Federico\Desktop
        return parts[0] + os.sep + os.sep.join(parts[1:n])
    return os.sep.join(parts[:n]) if n > 1 else norm


def detectar_raices(conn) -> dict:
    """Devuelve {raiz: [filepath_absoluto, ...]} agrupando por raíz origen."""
    raices: dict = {}
    for _, abs_path, _ in obtener_medios(conn):
        raiz = _detectar_raiz(abs_path)
        raices.setdefault(raiz, set()).add(abs_path)
    return {k: sorted(v) for k, v in raices.items()}


def nombre_subcarpeta(raiz: str) -> str:
    """Deriva un nombre de subcarpeta legible desde la raíz absoluta."""
    base = os.path.basename(raiz.rstrip(os.sep))
    nombre = base.lower().replace(" ", "-")
    # Casos especiales: export de Telegram -> telegram
    if "chatexport" in nombre:
        return "telegram"
    return nombre or "medios"


# ── Main ─────────────────────────────────────────────────────────────────


def procesar(
    db_path: str,
    new_root: str,
    mode: str = "mover",
    dry_run: bool = False,
    update_db: bool = False,
):
    conn = abrir(db_path)

    row = conn.execute("SELECT value FROM config WHERE key = 'ingest_root'").fetchone()
    old_root = row[0] if row else None

    log.info("ingest_root actual (DB): %s", old_root)
    if new_root:
        log.info("Nueva raíz destino:      %s", new_root)
    else:
        log.info("Nueva raíz destino:      (se pedirá al ejecutar)")
    log.info("")

    raices = detectar_raices(conn)
    if not raices:
        log.error("No hay medios en la DB.")
        conn.close()
        return

    log.info("Se detectaron %d raíz(es) con medios:", len(raices))
    total_medios = 0
    planes = []
    for raiz, paths in sorted(raices.items()):
        sub = nombre_subcarpeta(raiz)
        if new_root:
            destino = os.path.join(new_root, sub)
        else:
            destino = f"<destino>/{sub}"
        total_medios += len(paths)
        planes.append((raiz, destino, len(paths)))
        log.info("  [%3d medios] %s", len(paths), raiz)
        log.info("               -> %s", destino)
        # Mostrar ejemplo de estructura preservada
        if paths:
            ejemplo = paths[0]
            ejemplo_rel = os.path.relpath(ejemplo, raiz)
            log.info("                ejemplo: %s", ejemplo_rel)
            log.info("                se copia como: %s", os.path.join(sub, ejemplo_rel))

    log.info("")
    log.info("Total de medios: %d", total_medios)
    log.info("")

    if new_root and old_root and os.path.normpath(old_root) == os.path.normpath(new_root):
        log.info("La nueva raíz coincide con ingest_root. Nada que hacer.")
        conn.close()
        return

    if dry_run:
        log.info("=== MODO DRY RUN — no se mueve nada ===")
        conn.close()
        return

    # Pedir destino interactivo si no se pasó
    if new_root is None:
        resp_destino = input("  ¿Nueva raíz unificada para los medios? (ej: G:/Flujos/Medios): ").strip()
        if not resp_destino:
            log.error("No se indicó destino. Cancelado.")
            conn.close()
            return
        new_root = normalizar_ruta(resp_destino)
        # Recalcular destinos con el nuevo root
        planes = []
        for raiz, paths in sorted(raices.items()):
            sub = nombre_subcarpeta(raiz)
            planes.append((raiz, os.path.join(new_root, sub), len(paths)))
        log.info("")
        log.info("Destino unificado: %s", new_root)
        for raiz, destino, n in planes:
            log.info("  [%3d medios] %s -> %s", n, raiz, destino)
        log.info("")

    # Confirmación interactiva antes de ejecutar
    log.info("Modo: %s", "MOVIENDO (borra originales)" if mode == "mover"
             else "COPIANDO (conserva originales)")
    if mode == "copiar" and not update_db:
        log.info("")
        log.info("La copia es un BACKUP de los originales.")
        log.info("  [1] La DB sigue apuntando a los ORIGINALES (backup puro)")
        log.info("  [2] La DB pasa a apuntar a los NUEVOS archivos copiados")
        resp_db = input("  ¿Qué DB usar? (1/2) [1]: ").strip()
        if resp_db == "2":
            update_db = True
        else:
            update_db = False
        log.info("")
    elif mode == "copiar" and update_db:
        log.info("La DB pasará a apuntar a los NUEVOS archivos copiados.")
    resp = input("  ¿Ejecutar? (s/n) [n]: ").strip().lower()
    if resp != "s":
        log.info("Cancelado.")
        conn.close()
        return

    # Backup automático antes de tocar FS/DB (si va a modificar DB)
    if mode == "mover" or update_db:
        try:
            conn.commit()
            conn.close()
            import sqlite3 as _sq
            from datetime import datetime as _dt
            _bdir = os.path.join(os.path.dirname(os.path.abspath(db_path)), "backups")
            os.makedirs(_bdir, exist_ok=True)
            _ts = _dt.now().strftime("%Y%m%d_%H%M%S")
            _bpath = os.path.join(_bdir, f"flujos_{_ts}__autobackup.db")
            _src = _sq.connect(db_path)
            try:
                _src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
            _dst = _sq.connect(_bpath)
            try:
                _src.backup(_dst)
                log.info("  ✓ Backup automático: %s", os.path.basename(_bpath))
            finally:
                _dst.close()
            _src.close()
            conn = abrir(db_path)
        except Exception as e:
            log.warning("  ⚠ No se pudo crear backup automático: %s", e)
            try:
                conn = abrir(db_path)
            except Exception:
                pass

    # Ejecutar por raíz
    stats_global = {
        "movidos": 0, "copiados": 0, "errores": 0,
        "skip": 0, "sidecars": 0, "colisiones": 0,
    }
    for raiz, destino, n in planes:
        log.info("Procesando raíz: %s", raiz)
        log.info("  -> %s", destino)
        if not os.path.isdir(raiz):
            log.warning("  La raíz no existe en disco, saltando: %s", raiz)
            stats_global["skip"] += n
            continue
        if mode == "mover":
            stats = ejecutar_movimiento(conn, raiz, destino)
            stats_global["movidos"] += stats["movidos"]
            stats_global["errores"] += stats["errores"]
            stats_global["skip"] += stats["skip"]
            stats_global["sidecars"] += stats["sidecars"]
            stats_global["colisiones"] += stats["colisiones"]
            log.info(
                "  Movidos: %d | Colisiones: %d | Sidecars: %d | Errores: %d | Saltados: %d",
                stats["movidos"], stats["colisiones"], stats["sidecars"],
                stats["errores"], stats["skip"],
            )
        else:  # copiar
            stats = ejecutar_copia(conn, raiz, destino, update_db=update_db)
            stats_global["copiados"] += stats["copiados"]
            stats_global["errores"] += stats["errores"]
            stats_global["skip"] += stats["skip"]
            stats_global["sidecars"] += stats["sidecars"]
            stats_global["colisiones"] += stats["colisiones"]
            log.info(
                "  Copiados: %d | Colisiones: %d | Sidecars: %d | Errores: %d | Saltados: %d",
                stats["copiados"], stats["colisiones"], stats["sidecars"],
                stats["errores"], stats["skip"],
            )

    # Actualizar ingest_root en config
    if mode == "mover" or update_db:
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES ('ingest_root', ?)",
            (normalizar_ruta(new_root),),
        )
        conn.commit()
        log.info("")
        log.info("ingest_root actualizado a: %s", normalizar_ruta(new_root))

    conn.close()
    log.info("")
    log.info(
        "=== RESUMEN: movidos=%d copiados=%d | errores=%d | skip=%d | sidecars=%d | colisiones=%d ===",
        stats_global["movidos"], stats_global["copiados"],
        stats_global["errores"], stats_global["skip"],
        stats_global["sidecars"], stats_global["colisiones"],
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Consolida medios de múltiples raíces en una estructura unificada "
        "y actualiza la DB. Útil para preparar traslado a otra computadora.",
        epilog="""
Ejemplos:
  python scripts/consolidar_medios.py --dry-run
  python scripts/consolidar_medios.py
  python scripts/consolidar_medios.py --new-root D:/Flujos/Medios --mode mover
  python scripts/consolidar_medios.py --mode copiar --update-db
""",
    )
    parser.add_argument("--new-root", default=None,
                        help="Nueva raíz unificada (si no se pasa, se pregunta interactivamente)")
    parser.add_argument("--mode", choices=["mover", "copiar"], default="copiar",
                        help="mover=mueve archivos (borra originales); copiar=deja originales "
                        "(default: copiar, más seguro)")
    parser.add_argument("--update-db", action="store_true",
                        help="En modo copiar: actualizar DB con nuevas rutas. Si no se pasa, "
                        "se pregunta interactivamente")
    parser.add_argument("--dry-run", action="store_true",
                        help="Solo mostrar el plan, sin mover nada")
    parser.add_argument("--db", default=None,
                        help="Ruta a la DB (default: db/flujos.db)")
    args = parser.parse_args(argv)

    db_path = resolver_db(args.db)
    if not os.path.isfile(db_path):
        log.error("Base de datos no encontrada: %s", db_path)
        sys.exit(1)

    new_root = normalizar_ruta(args.new_root) if args.new_root else None

    if not args.dry_run and new_root is None:
        new_root = input("  ¿Nueva raíz unificada para los medios? (ej: G:/Flujos/Medios): ").strip()
        if not new_root:
            log.error("No se indicó destino. Cancelado.")
            sys.exit(1)
        new_root = normalizar_ruta(new_root)

    procesar(
        db_path=db_path,
        new_root=new_root,
        mode=args.mode,
        dry_run=args.dry_run,
        update_db=args.update_db,
    )


if __name__ == "__main__":
    main()
