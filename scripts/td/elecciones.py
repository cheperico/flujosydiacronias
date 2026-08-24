#!/usr/bin/env python3
"""
elecciones.py — Nubes de elecciones para la instalación (DB → TD).

Framework data-driven y extensible para las "nubes de metadatos seleccionables"
(diseño_instalacion §1). Cada grupo de elecciones (horas, municipios, colores,
tags, días, clima, ideas...) se modela como una entrada del registro `GRUPOS`.
Agregar un grupo nuevo = definir su consulta y añadirlo al registro; el envío
OSC, el CLI y la UI TD funcionan sin tocar nada más.

Registro de grupos (GRUPOS):
    {
        "id":       "horas",               # identificador (también suffix OSC)
        "titulo":   "Horas",               # texto para la UI TD
        "address":  "/flujos/elecciones/horas",   # address OSC completo
        "query":    callable(conn) -> [(valor, freq), ...],
    }

Cada consulta devuelve pares (valor, frecuencia). El envío normaliza el peso
a 0..1 (freq / max_freq) y manda por OSC:

    /flujos/elecciones/<id> "<titulo>" <n> <valor1> <peso1> <valor2> <peso2> ...

Uso:
    python scripts/td/elecciones.py                        # envía todos los grupos
    python scripts/td/elecciones.py --grupo horas          # envía solo horas
    python scripts/td/elecciones.py --grupo horas,municipios --dry-run
"""

import argparse
import logging
import sqlite3
import sys
import unicodedata
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

# Permitir ejecución standalone: agregar raíz del proyecto al path
if __name__ == "__main__" and __package__ is None:
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pythonosc import udp_client  # noqa: E402
from db.util import abrir, resolver_db  # noqa: E402

OSC_HOST = "127.0.0.1"
OSC_PUERTO_TD = 9000


# ─────────────────────────────────────────────────────────────────────────────
# Ordenación alfabética (case-insensitive, sin acentos)
# ─────────────────────────────────────────────────────────────────────────────


def _clave_alfabetica(valor: str) -> str:
    """Normaliza un valor para ordenación alfabética: sin acentos, minúsculas."""
    nfkd = unicodedata.normalize("NFD", valor or "")
    return "".join(c for c in nfkd if unicodedata.combining(c) == 0).lower().strip()


def _ordenar_alfabetico(pares: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Ordena pares (valor, frecuencia) alfabéticamente por valor (estable)."""
    return sorted(pares, key=lambda vf: _clave_alfabetica(vf[0]))


# ─────────────────────────────────────────────────────────────────────────────
# Consultas por grupo
# ─────────────────────────────────────────────────────────────────────────────


def _consulta_horas(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """Medios por hora de día (0..23), usando substr del timestamp_utc."""
    filas = conn.execute("""
        SELECT CAST(substr(timestamp_utc, 12, 2) AS INTEGER) AS h, COUNT(*) AS n
        FROM media
        WHERE timestamp_utc IS NOT NULL
        GROUP BY h
    """).fetchall()
    presentes = {int(fila[0]): int(fila[1]) for fila in filas}
    return [(f"{h:02d}:00", presentes.get(h, 0)) for h in range(24)]


def _consulta_municipios(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """Municipios con medios, en orden alfabético (case-insensitive, sin acentos)."""
    filas = conn.execute("""
        SELECT municipio, COUNT(*) AS n
        FROM media
        WHERE municipio IS NOT NULL AND municipio != ''
        GROUP BY municipio
    """).fetchall()
    pares = [(str(fila[0]), int(fila[1])) for fila in filas]
    return _ordenar_alfabetico(pares)


def _consulta_colores(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """Colores básicos dominantes (slot 1, todos), en orden alfabético."""
    filas = conn.execute("""
        SELECT color_1_name_basic AS color, COUNT(*) AS n
        FROM media
        WHERE color_1_name_basic IS NOT NULL
        GROUP BY color_1_name_basic
    """).fetchall()
    pares = [(str(fila[0]), int(fila[1])) for fila in filas]
    return _ordenar_alfabetico(pares)


CLAVES_TAGS: tuple[str, ...] = (
    "ia_keywords",
    "ia_keywords_transcripcion",
    "ia_keywords_texto",
    "ia_keywords_sonido",
    "ia_keywords_video",
)


def _consulta_tags(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """Keywords de todas las fuentes (plano o JSON) contadas individualmente.

    Fuentes: ia_keywords (visión), ia_keywords_transcripcion (habla),
    ia_keywords_texto (.md), ia_keywords_sonido (sonidos), ia_keywords_video
    (video, camino fotos).

    Se conserva el cuarto más significativo por frecuencia (top 25%), que
    elimina el ruido de keywords que aparecen una sola vez. El cuarto está
    capado a MAX_TAGS para que el mensaje OSC completo quepa en el parser
    de TouchDesigner (~250 args). Dentro del conjunto seleccionado, el orden
    es alfabético.
    """
    marcadores = ",".join("?" * len(CLAVES_TAGS))
    filas = conn.execute(
        f"SELECT value FROM media_metadata WHERE key IN ({marcadores})",
        CLAVES_TAGS,
    ).fetchall()
    contador: dict[str, int] = {}
    for fila in filas:
        texto = str(fila[0]).strip()
        if not texto:
            continue
        # Soporta JSON array (formato histórico) y texto plano separado por comas
        partes = _partes_keywords(texto)
        for p in partes:
            p = p.strip().lower()
            if len(p) <= 2:
                continue
            if any(ign in p for ign in KEYWORDS_A_IGNORAR):
                continue
            if p in KEYWORDS_SENSIBLES:
                continue
            contador[p] = contador.get(p, 0) + 1
    top = sorted(contador.items(), key=lambda x: -x[1])
    # Cuarto más significativo: ceil(len / 4), mínimo 1 (cociente entero: (n+3)//4)
    n_cuarto = max(1, (len(top) + 3) // 4)
    seleccion = [(str(v), int(f)) for v, f in top[:min(n_cuarto, MAX_TAGS)]]
    return _ordenar_alfabetico(seleccion)


KEYWORDS_A_IGNORAR = [
    'elige una', 'genero', 'fotografico', 'es un(a)', 'la imagen',
    'una de las siguientes', 'deben describir', 'ejemplo:', 'separas con comas',
    'el aguacate', "esponja ribiosa", "sa_20001", "roberto", "federico",
    "el aguaje", "elante", "ella", "documento", "objetivo", "objeto",
    "otras)", "otras.", "gushing river",
]

# Contenido sensible de la instalación: se conserva en la DB pero NUNCA se
# muestra en la nube de tags (muerte). Match exacto tras normalizar a
# minúsculas. No confundir con muerte vegetal benigna (árbol muerto, etc.).
KEYWORDS_SENSIBLES = {"cadáver", "perro muerto", "cuerpo muerto", "muerto"}

# Límite seguro de items: TouchDesigner muestra exactamente 200 tags en pantalla.
# 200 items = 402 args OSC (2 + 2×200), que llegan completos al parser de TD.
MAX_TAGS = 200


def _partes_keywords(texto: str) -> list[str]:
    """Divide un valor de ia_keywords en partes (soporta texto plano o JSON)."""
    import json
    texto = texto.strip()
    if not texto:
        return []
    if texto.startswith("["):
        try:
            datos = json.loads(texto)
            if isinstance(datos, list):
                return [str(p).strip().strip("'\"") for p in datos]
        except (json.JSONDecodeError, TypeError):
            pass
    return [p.strip().strip("'\"") for p in texto.split(",") if p.strip()]


_ORDEN_DIAS: dict[str, int] = {
    "lunes": 0, "martes": 1, "miércoles": 2, "jueves": 3,
    "viernes": 4, "sábado": 5, "domingo": 6,
}


def _consulta_dias(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """Días de la semana presentes, en orden natural (lunes→domingo);
    valores desconocidos al final.
    """
    filas = conn.execute("""
        SELECT value, COUNT(*) AS n
        FROM media_metadata
        WHERE key='dia_semana'
        GROUP BY value
    """).fetchall()
    pares = [(str(fila[0]), int(fila[1])) for fila in filas]
    return sorted(pares, key=lambda vf: (
        _ORDEN_DIAS.get(vf[0].lower(), 99), _clave_alfabetica(vf[0])
    ))


def _consulta_clima(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """Etiquetas de clima (weather_label), en orden alfabético."""
    filas = conn.execute("""
        SELECT value, COUNT(*) AS n
        FROM media_metadata
        WHERE key='weather_label'
        GROUP BY value
    """).fetchall()
    pares = [(str(fila[0]), int(fila[1])) for fila in filas]
    return _ordenar_alfabetico(pares)


# ─────────────────────────────────────────────────────────────────────────────
# Registro de grupos (data-driven, extensible)
#
# Para agregar un grupo nuevo:
#   1. Escribir una función _consulta_<grupo>(conn) -> [(valor, freq), ...]
#   2. Añadir el dict a GRUPOS con su id, titulo y address.
# El envío, CLI y la UI TD lo toman automáticamente.
# ─────────────────────────────────────────────────────────────────────────────

GRUPOS: list[dict[str, Any]] = [
    {
        "id": "horas",
        "titulo": "Horas",
        "address": "/flujos/elecciones/horas",
        "query": _consulta_horas,
    },
    {
        "id": "municipios",
        "titulo": "Municipios",
        "address": "/flujos/elecciones/municipios",
        "query": _consulta_municipios,
    },
    {
        "id": "colores",
        "titulo": "Colores",
        "address": "/flujos/elecciones/colores",
        "query": _consulta_colores,
    },
    {
        "id": "tags",
        "titulo": "Tags",
        "address": "/flujos/elecciones/tags",
        "query": _consulta_tags,
    },
    {
        "id": "dias",
        "titulo": "Días",
        "address": "/flujos/elecciones/dias",
        "query": _consulta_dias,
    },
    {
        "id": "clima",
        "titulo": "Clima",
        "address": "/flujos/elecciones/clima",
        "query": _consulta_clima,
    },
]

# Aliases: id -> grupo
GRUPOS_POR_ID = {g["id"]: g for g in GRUPOS}


# ─────────────────────────────────────────────────────────────────────────────
# Evaluación y envío
# ─────────────────────────────────────────────────────────────────────────────


def obtener_items_grupo(conn: sqlite3.Connection, grupo: dict) -> list[dict]:
    """
    Evalúa la consulta de un grupo y normaliza los pesos a 0..1.

    El orden de los items depende de cada grupo: horas en orden numérico 0..23,
    días en orden natural (lunes→domingo), y los demás grupos en orden alfabético.

    Returns:
        Lista de {"valor": str, "freq": int, "peso": float}.
    """
    query_fn: Callable = grupo["query"]
    pares = query_fn(conn)
    if not pares:
        return []
    max_freq = max(f for _, f in pares) or 1
    items = []
    for valor, freq in pares:
        items.append({
            "valor": str(valor),
            "freq": int(freq),
            "peso": round(freq / max_freq, 3),
        })
    return items


def enviar_grupo(cli: udp_client.SimpleUDPClient, grupo: dict,
                 items: list[dict], verbose: bool = False) -> None:
    """
    Envía un grupo por OSC. Formato:
        address "<titulo>" <n> <valor1> <peso1> <valor2> <peso2> ...
    """
    args: list[Any] = [grupo["titulo"], len(items)]
    for item in items:
        args.append(item["valor"])
        args.append(item["peso"])
    cli.send_message(grupo["address"], args)
    if verbose:
        log.info("  OSC → %s (%d items): %s",
                 grupo["address"], len(items),
                 ", ".join(f"{i['valor']}={i['freq']}" for i in items[:6]))
    else:
        log.info("  → %s: %d items", grupo["address"], len(items))


def enviar_grupos(db_path: str, ids_grupos: list[str],
                  host: str = OSC_HOST, port: int = OSC_PUERTO_TD,
                  dry_run: bool = False, verbose: bool = False) -> None:
    """
    Consulta la DB y envía a TD los grupos pedidos.

    Args:
        db_path: Ruta a la DB.
        ids_grupos: Lista de ids de grupos a enviar (todos si vacía).
        host/port: Destino OSC (TD).
        dry_run: Solo muestra lo que enviaría, sin OSC.
    """
    conn = abrir(db_path)
    try:
        if not ids_grupos:
            ids_grupos = [g["id"] for g in GRUPOS]
        cli = None
        if not dry_run:
            cli = udp_client.SimpleUDPClient(host, port)
        for gid in ids_grupos:
            grupo = GRUPOS_POR_ID.get(gid)
            if grupo is None:
                log.warning("  Grupo desconocido: %s", gid)
                continue
            items = obtener_items_grupo(conn, grupo)
            if dry_run:
                log.info("  [DRY] %s (%s): %d items", grupo["address"],
                         grupo["titulo"], len(items))
                for i in items[:5]:
                    log.info("         %s = %d (%.2f)", i["valor"], i["freq"], i["peso"])
                continue
            enviar_grupo(cli, grupo, items, verbose=verbose)
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Envía las nubes de elecciones (metadatos seleccionables) "
                    "desde la DB a TouchDesigner vía OSC.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Ejemplos:
  python scripts/td/elecciones.py                      # envía todos los grupos
  python scripts/td/elecciones.py --grupo horas        # solo horas
  python scripts/td/elecciones.py --grupo horas,tags --dry-run
  python scripts/td/elecciones.py --host 192.168.1.50 --port 9000
        """,
    )
    parser.add_argument("--grupo", default=None,
                        help="Grupos a enviar separados por coma (default: todos). "
                             "Disponibles: " + ", ".join(GRUPOS_POR_ID))
    parser.add_argument("--db", default=None, help="Ruta a la DB (default: db/flujos.db).")
    parser.add_argument("--host", default=OSC_HOST, help=f"Host TD (default: {OSC_HOST}).")
    parser.add_argument("--port", type=int, default=OSC_PUERTO_TD,
                        help=f"Puerto OSC TD (default: {OSC_PUERTO_TD}).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Previsualizar sin enviar.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Log detallado.")
    args = parser.parse_args(argv)

    # Consolas Windows (cp1252) no entienden ── ; forzar UTF-8 en stdout
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(message)s")

    db_path = resolver_db(args.db)
    ids = []
    if args.grupo:
        ids = [s.strip() for s in args.grupo.split(",") if s.strip()]

    print("\n  ── Elecciones ──────────────────────────────")
    print(f"  DB:    {db_path}")
    print(f"  TD:    {args.host}:{args.port}")
    print(f"  Modo:  {'DRY-RUN' if args.dry_run else 'enviar'}")
    if ids:
        print(f"  Grupos: {', '.join(ids)}")
    else:
        print(f"  Grupos: todos ({len(GRUPOS)})")
    print()

    enviar_grupos(db_path, ids, host=args.host, port=args.port,
                  dry_run=args.dry_run, verbose=args.verbose)
    if not args.dry_run:
        print("\n  ✅ Enviados a TD.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
