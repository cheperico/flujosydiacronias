#!/usr/bin/env python3
"""
limpiar_descripciones.py — Limpia descripciones con eco del prompt (meta-intros).

El modelo de visión (minicpm) a veces abre la descripción con una
meta-introducción regurgitada del prompt ("To describe the image, we observe...",
"Here's a long description of the image:", "Para describir la imagen, ...")
en vez de describir directamente.

Este script recorta esos prefijos de forma DETERMINISTA (sin IA ni red):

  - ia_description_en (EN) → usa limpiar_meta_intro de image_analysis.py
    (fuente de verdad de los prefijos EN, PREFIJOS_META_EN).
  - ia_description (ES)    → usa los prefijos ES locales (PREFIJOS_META_ES)
    porque las descripciones finales en español arrastran el eco traducido.

Nunca se pierde contenido: si el recorte dejaría el texto vacío o con menos
de 15 caracteres, se conserva el original. Solo se recorta del INICIO; las
aperturas legítimas ("The image shows...", "La imagen muestra...") no se tocan.
Nota: los conteos de aperturas legítimas pueden AUMENTAR tras la limpieza
(recortar un meta-intro revela la apertura que había debajo); el invariante
real se verifica con violaciones_negativos: ningún registro que YA empezaba
con una apertura legítima es modificado.

Uso:
    python scripts/limpiar_descripciones.py                      # limpiar (con backup automático)
    python scripts/limpiar_descripciones.py --dry-run            # solo auditar/previsualizar
    python scripts/limpiar_descripciones.py --solo-es            # solo descripciones ES
    python scripts/limpiar_descripciones.py --solo-en            # solo descripciones EN
    python scripts/limpiar_descripciones.py --no-backup          # sin backup automático
    python flujos.py limpiar-descripciones --dry-run             # vía flujos.py

Modos:
    skip    → procesa las filas que matchean un prefijo (default; operación idempotente)
    update  → igual que skip (no hay nada que sobrescribir: solo se recorta el inicio)
    replace → igual que skip (no hay datos previos que limpiar)
"""

import argparse
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

# Permitir importar scripts/ como paquete (igual que limpiar_tandas.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.util import abrir, resolver_db
from scripts.ai_media.checkpoint import Checkpoint, manejar_interrupcion
from scripts.ai_media.image_analysis import PREFIJOS_META_EN, limpiar_meta_intro

CLAVE_DESC_EN = "ia_description_en"
CLAVE_DESC_ES = "ia_description"

# ── Prefijos de meta-intro en español (eco traducido por translategemma) ─────
# limpiar_meta_intro (image_analysis.py) recorta solo EN; las descripciones
# finales en español arrastran el eco traducido y necesitan esta lista local.
PREFIJOS_META_ES: tuple[str, ...] = (
    "para describir la imagen en detalle, analizamos sus componentes clave:",
    "para describir la imagen en detalle, analizamos sus componentes principales:",
    "para describir la imagen en detalle, primero observamos los elementos visuales principales:",
    "para describir la imagen en detalle, observamos los elementos visuales principales:",
    "para describir la imagen, primero observamos los elementos visuales principales:",
    "para describir la imagen, observamos los elementos visuales principales:",
    "para describir la imagen, primero observamos",
    "para describir la imagen, observamos",
    "para describir la imagen en detalle, observamos",
    "para describir la imagen en detalle, analizamos",
    "para describir la imagen,",
    "para describir la imagen en detalle,",
    "para describir la imagen:",
    "para describir la imagen en detalle:",
    "aquí hay una descripción larga de la imagen:",
    "aquí hay una descripción detallada de la imagen:",
    "aquí hay una descripción de la imagen:",
    "aquí está una descripción larga de la imagen:",
    "aquí está una descripción detallada de la imagen:",
    "he aquí una descripción de la imagen:",
    "esta es una descripción larga de la imagen:",
    "esta es una descripción detallada de la imagen:",
    "esta es una descripción de la imagen:",
    "esta es mi descripción de la imagen:",
    # Variantes sin "de la imagen" (reales observadas; van después de las
    # variantes completas para que el prefijo largo gane primero)
    "aquí hay una descripción larga:",
    "aquí hay una descripción detallada:",
    "aquí hay una descripción:",
    "aquí está una descripción larga:",
    "aquí está una descripción detallada:",
    "aquí está una descripción:",
    "aquí está la descripción:",
    "aquí tienes una descripción detallada:",
    "aquí tienes una descripción larga:",
    "aquí tienes una descripción:",
    # "Basándonos en la imagen proporcionada, ..." (lo que antes quedaba como
    # residuo mid-text ahora se vuelve limpiable como prefijo)
    "basándonos en la imagen proporcionada, aquí hay una descripción detallada:",
    "basándonos en la imagen proporcionada, aquí hay una descripción larga:",
    "basándonos en la imagen proporcionada, aquí hay una descripción:",
    "basado en la imagen que se proporciona, aquí hay una descripción detallada:",
    "mi descripción de la imagen:",
    "la siguiente es una descripción de la imagen:",
    "voy a describir la imagen:",
    "déjame describir la imagen:",
    "dejame describir la imagen:",
    "descripción de la imagen:",
    "descripción:",
    "aquí tienes una descripción de la imagen:",
    "vamos a analizar sus elementos clave:",
    "vamos a analizar sus elementos:",
    "vamos a analizar la descripción de la imagen:",
)

# Continuaciones meta que siguen a un prefijo (solo se recortan si hubo match
# primario; nunca standalone para no tocar "Observamos un ciclista..." legítimo).
_CONTINUACIONES_META_ES: tuple[str, ...] = (
    "primero observamos", "observamos", "analizamos", "vemos", "notamos",
    "primero analizamos", "primero vemos",
)
_ACK_ES: tuple[str, ...] = ("claro,", "por supuesto,", "aquí tienes:", "aquí tiene:", "aquí te va:", "perfecto,")
MAX_PASADAS = 3
CARACTERES_RECORTE = " \t\n\r:;,.-–—\"'’"

# Aperturas legítimas de descripciones que NO deben tocarse (negativos).
OPENER_LEGITIMOS: tuple[str, ...] = (
    "the image shows", "in this image", "this image depicts",
    "la imagen muestra", "en la imagen", "esta imagen",
)


def limpiar_intro(
    texto: str,
    prefijos: tuple[str, ...],
    continuaciones: tuple[str, ...],
    ack: tuple[str, ...],
) -> tuple[str, bool]:
    """Aplica la limpieza de meta-intros con listas parametrizadas.

    Misma lógica que limpiar_meta_intro (image_analysis.py) pero recibe las
    listas de prefijos, continuaciones y acknowledgments por parámetro, para
    poder limpiar textos en distintos idiomas (EN/ES).

    Args:
        texto: Descripción a limpiar.
        prefijos: Prefijos de meta-intro a recortar del inicio.
        continuaciones: Continuaciones meta que siguen a un prefijo.
        ack: Acknowledgments iniciales ("claro,", "sure,"...).

    Returns:
        Tupla (nuevo, cambiado). Si el resultado quedaría vacío o con menos
        de 15 caracteres, devuelve (texto_original, False): nunca se vacía
        un registro.
    """
    t = texto.strip().strip('"').strip()
    if not t:
        return texto, False
    bajo = t.lower().replace("’", "'")
    # Paso 0: acknowledgments ("Claro, aquí tienes...")
    for a in ack:
        if bajo.startswith(a):
            t = t[len(a):].lstrip(CARACTERES_RECORTE).strip()
            bajo = t.lower().replace("’", "'")
            break
    cambiado = False
    for _ in range(MAX_PASADAS):
        match = next((p for p in prefijos if bajo.startswith(p)), None)
        if not match:
            break
        t = t[len(match):].lstrip(CARACTERES_RECORTE).strip()
        bajo = t.lower().replace("’", "'")
        # Paso secundario: continuación meta ("observamos", "analizamos", ...)
        for cont in continuaciones:
            if bajo.startswith(cont):
                t = t[len(cont):].lstrip(CARACTERES_RECORTE).strip()
                bajo = t.lower().replace("’", "'")
                break
        cambiado = True
    if not cambiado:
        return texto, False
    if not t or len(t) < 15:
        log.warning(
            "  ⚠ Meta-intro detectado pero el resto quedaría demasiado corto "
            "(%d caracteres); se conserva el texto original.",
            len(t),
        )
        return texto, False
    return t, True


def _limpiar_valor(valor: str, clave: str) -> tuple[str, bool]:
    """Aplica la limpieza de meta-intro adecuada según la clave.

    EN → limpiar_meta_intro (fuente de verdad: incluye acknowledgments y
    continuaciones EN internas de image_analysis.py).
    ES → limpiar_intro con los prefijos locales PREFIJOS_META_ES.

    Returns:
        Tupla (nuevo, cambiado).
    """
    if clave == CLAVE_DESC_EN:
        nuevo = limpiar_meta_intro(valor)
        return nuevo, nuevo != valor
    return limpiar_intro(valor, PREFIJOS_META_ES, _CONTINUACIONES_META_ES, _ACK_ES)


def _reconstruible(viejo: str, nuevo: str) -> bool:
    """Verifica no-pérdida: el nuevo debe reconstruirse desde el viejo
    recortando únicamente un prefijo + puntuación al frente.

    Como la limpieza solo recorta del INICIO (con la misma normalización de
    extremos que aplica el limpiador: strip + comillas), el nuevo debe ser
    el final del viejo normalizado. Si no lo es, hubo pérdida de contenido.

    Args:
        viejo: Valor original en la DB.
        nuevo: Valor tras la limpieza.

    Returns:
        True si la transformación es reconstruible (segura de escribir).
    """
    if viejo == nuevo:
        return True
    if not nuevo:
        return False
    return viejo.strip().strip('"').strip().endswith(nuevo)


def _valores_por_clave(conn, clave: str) -> list[tuple[int, str]]:
    """Devuelve (media_id, value) de todas las filas de una clave de descripción."""
    return conn.execute(
        "SELECT media_id, value FROM media_metadata "
        "WHERE key = ? AND value IS NOT NULL",
        (clave,),
    ).fetchall()


def _empieza_con_prefijo(valor: str, prefijos: tuple[str, ...]) -> bool:
    """True si el valor empieza (con la misma normalización que la limpieza:
    strip + comillas) con alguno de los prefijos."""
    bajo = valor.strip().strip('"').strip().lower().replace("’", "'")
    return any(bajo.startswith(p) for p in prefijos)


def _contar_negativos(conn) -> dict[str, int]:
    """Cuenta descripciones que empiezan con aperturas legítimas (negativos).

    Estas aperturas NO deben tocarse; el conteo debe ser idéntico antes y
    después de la limpieza (se verifica en main).
    """
    resultado: dict[str, int] = {}
    for opener in OPENER_LEGITIMOS:
        resultado[opener] = conn.execute(
            "SELECT COUNT(*) FROM media_metadata "
            "WHERE key IN (?, ?) AND value IS NOT NULL "
            "AND LOWER(TRIM(value)) LIKE ?",
            (CLAVE_DESC_EN, CLAVE_DESC_ES, opener + "%"),
        ).fetchone()[0]
    return resultado


def auditar(conn) -> dict:
    """Audita el estado de las descripciones EN/ES en la DB (no escribe).

    Reporta: conteo por clave, filas con prefijo conocido, residuos en medio
    del texto (deben ser 0), cruzado EN↔ES, aperturas legítimas (negativos)
    y los substrings iniciales de ia_description_en para confirmar la lista
    de prefijos contra datos reales.

    Args:
        conn: Conexión SQLite.

    Returns:
        Dict con todas las estadísticas (para el resumen final).
    """
    stats: dict = {}

    # 1. Conteo por clave
    conteo = dict(conn.execute(
        "SELECT key, COUNT(*) FROM media_metadata WHERE key IN (?, ?) GROUP BY key",
        (CLAVE_DESC_EN, CLAVE_DESC_ES),
    ).fetchall())
    stats["conteo_por_clave"] = {
        CLAVE_DESC_EN: conteo.get(CLAVE_DESC_EN, 0),
        CLAVE_DESC_ES: conteo.get(CLAVE_DESC_ES, 0),
    }

    # 2. Substrings iniciales de ia_description_en (confirmar prefijos)
    filas = conn.execute(
        "SELECT DISTINCT substr(value, 1, 80) FROM media_metadata "
        "WHERE key = ? AND value IS NOT NULL ORDER BY 1 LIMIT 200",
        (CLAVE_DESC_EN,),
    ).fetchall()
    substrings = [r[0] for r in filas]
    stats["substrings_en"] = substrings
    stats["substrings_en_con_prefijo"] = sum(
        1 for s in substrings if _empieza_con_prefijo(s, PREFIJOS_META_EN)
    )

    # 3. Filas cuyo valor empieza con prefijo conocido (por clave)
    en_valores = _valores_por_clave(conn, CLAVE_DESC_EN)
    es_valores = _valores_por_clave(conn, CLAVE_DESC_ES)
    en_con_prefijo = [
        mid for mid, v in en_valores if _empieza_con_prefijo(v, PREFIJOS_META_EN)
    ]
    es_con_prefijo = [
        mid for mid, v in es_valores if _empieza_con_prefijo(v, PREFIJOS_META_ES)
    ]
    stats["en_con_prefijo"] = len(en_con_prefijo)
    stats["es_con_prefijo"] = len(es_con_prefijo)

    # 4. Residuos en medio del texto (deben ser 0 tras la limpieza)
    consultas_residuo = {
        "'to describe the image' (EN, pos>1)": (
            "SELECT COUNT(*) FROM media_metadata WHERE key = ? "
            "AND instr(LOWER(value), 'to describe the image') > 1",
            (CLAVE_DESC_EN,),
        ),
        "'para describir la imagen' (ES, pos>1)": (
            "SELECT COUNT(*) FROM media_metadata WHERE key = ? "
            "AND instr(LOWER(value), 'para describir la imagen') > 1",
            (CLAVE_DESC_ES,),
        ),
        "'long description text here' (cualquier pos)": (
            "SELECT COUNT(*) FROM media_metadata "
            "WHERE instr(LOWER(value), 'long description text here') > 0",
            (),
        ),
        "'aquí hay una descripción' (ES, pos>1)": (
            "SELECT COUNT(*) FROM media_metadata WHERE key = ? "
            "AND instr(LOWER(value), 'aquí hay una descripción') > 1",
            (CLAVE_DESC_ES,),
        ),
        "'aqui hay una descripcion' sin acentos (ES, pos>1)": (
            "SELECT COUNT(*) FROM media_metadata WHERE key = ? "
            "AND instr(LOWER(value), 'aqui hay una descripcion') > 1",
            (CLAVE_DESC_ES,),
        ),
    }
    stats["residuos"] = {}
    for nombre, (sql, params) in consultas_residuo.items():
        stats["residuos"][nombre] = conn.execute(sql, params).fetchone()[0]

    # 5. Cruzado EN↔ES
    conj_en = set(en_con_prefijo)
    conj_es = set(es_con_prefijo)
    # (i) EN contaminada sin ES contaminada → el traductor reescribió la intro (~9)
    stats["solo_en"] = len(conj_en - conj_es)
    # (ii) ES contaminada con EN limpia → artefacto del traductor (esperado 0)
    stats["solo_es"] = len(conj_es - conj_en)

    # 6. Negativos: aperturas legítimas que NO deben tocarse (antes)
    stats["negativos"] = _contar_negativos(conn)

    # ── Reporte ──
    print("\n=== Auditoría de descripciones (meta-intros) ===\n")
    print("  Conteo por clave:")
    for clave in (CLAVE_DESC_EN, CLAVE_DESC_ES):
        print(f"    {clave:<16} {stats['conteo_por_clave'][clave]}")
    print(f"  Con prefijo conocido EN: {stats['en_con_prefijo']} (esperado ~136)")
    print(f"  Con prefijo conocido ES: {stats['es_con_prefijo']} (esperado ~127)")
    print("  Residuos en medio del texto (deben ser 0):")
    for nombre, cantidad in stats["residuos"].items():
        print(f"    {nombre:<50} {cantidad}")
    print("  Cruzado EN↔ES:")
    print(f"    EN contaminada sin ES contaminada : {stats['solo_en']} (esperado ~9)")
    print(f"    ES contaminada con EN limpia      : {stats['solo_es']} (esperado 0)")
    print("  Aperturas legítimas (negativos, no tocar) — antes:")
    for opener, cantidad in stats["negativos"].items():
        print(f"    {opener!r:<30} {cantidad}")
    nuevos = [s for s in substrings if not _empieza_con_prefijo(s, PREFIJOS_META_EN)]
    print(f"  Substrings iniciales de ia_description_en: {len(substrings)} distintos; "
          f"{len(substrings) - len(nuevos)} empiezan con prefijo conocido.")
    print("    Posibles variantes nuevas (primeras 12):")
    for s in nuevos[:12]:
        print(f"      • {s}")

    return stats


def limpiar(conn, dry_run: bool, claves: list[str]) -> dict:
    """Aplica la limpieza de meta-intros sobre las claves indicadas.

    Args:
        conn: Conexión SQLite.
        dry_run: Si True, solo previsualiza (no escribe).
        claves: Lista de claves a procesar (CLAVE_DESC_EN / CLAVE_DESC_ES).

    Returns:
        Dict con {"revisados", "modificados", "muestras",
                  "violaciones_negativos"}.
    """
    if not claves:
        return {"revisados": 0, "modificados": 0, "muestras": [],
                "violaciones_negativos": []}
    marcadores = ",".join("?" * len(claves))
    filas = conn.execute(
        "SELECT media_id, key, value FROM media_metadata "
        f"WHERE key IN ({marcadores}) AND value IS NOT NULL",
        claves,
    ).fetchall()

    revisados = 0
    modificados = 0
    muestras: list[dict] = []
    violaciones_negativos: list[tuple[int, str]] = []
    cp = Checkpoint(conn, cada=50, etiqueta="limpiar_descripciones")
    with manejar_interrupcion(conn=conn, etiqueta="limpiar_descripciones"):
        for media_id, clave, valor in filas:
            nuevo, cambiado = _limpiar_valor(valor, clave)
            if cambiado and _reconstruible(valor, nuevo):
                # Invariante: ningún registro que YA empezaba con una apertura
                # legítima puede ser modificado (violaciones_negativos).
                if _empieza_con_prefijo(valor, OPENER_LEGITIMOS):
                    violaciones_negativos.append((media_id, clave))
                if len(muestras) < 10:
                    muestras.append({
                        "media_id": media_id,
                        "clave": clave,
                        "antes": valor,
                        "despues": nuevo,
                    })
                if not dry_run:
                    conn.execute(
                        "UPDATE media_metadata SET value = ? "
                        "WHERE media_id = ? AND key = ?",
                        (nuevo, media_id, clave),
                    )
                modificados += 1
            elif cambiado:
                # Cambio descartado por el assert de no-pérdida
                log.warning(
                    "  ⚠ No-reconstruible (no se escribe) media %s clave %s",
                    media_id, clave,
                )
            revisados += 1
            cp.contar()
    cp.finalizar()

    if muestras:
        print(f"\n  Muestras antes/después (máx 10 de {modificados}):")
        for m in muestras:
            print(f"    media {m['media_id']} [{m['clave']}]")
            print(f"      antes   : {m['antes'][:120]!r}")
            print(f"      después : {m['despues'][:120]!r}")

    return {
        "revisados": revisados,
        "modificados": modificados,
        "muestras": muestras,
        "violaciones_negativos": violaciones_negativos,
    }


def _crear_backup(db_path: str) -> str:
    """Copia la DB a db/backups/<timestamp>_limpiar_descripciones.db.

    Args:
        db_path: Ruta absoluta a la DB a respaldar.

    Returns:
        Ruta al archivo de backup creado.
    """
    ruta = Path(db_path).resolve()
    dir_backup = ruta.parent / "backups"
    dir_backup.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = dir_backup / f"{timestamp}_limpiar_descripciones.db"
    shutil.copy2(ruta, destino)
    return str(destino)


def main(argv: list[str] | None = None) -> int:
    """Entry point para la limpieza de descripciones desde flujos.py o CLI.

    Args:
        argv: Lista de argumentos (sin el nombre del script).
               Si es None, usa sys.argv[1:].

    Returns:
        Código de salida (0 = ok, 1 = error).
    """
    parser = argparse.ArgumentParser(
        description="Limpia meta-intros regurgitados por el modelo en las "
                    "descripciones (ia_description_en / ia_description).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", default=None,
                        help="Ruta a la base de datos (default: db/flujos.db)")
    parser.add_argument("--mode", default="skip", choices=["skip", "update", "replace"],
                        help="Convención del proyecto. La operación es idempotente: "
                             "los tres modos procesan las filas que matchean un prefijo "
                             "(default: skip)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Solo auditar y previsualizar, sin escribir")
    parser.add_argument("--solo-en", action="store_true",
                        help="Procesar solo ia_description_en")
    parser.add_argument("--solo-es", action="store_true",
                        help="Procesar solo ia_description")
    parser.add_argument("--verbose", action="store_true",
                        help="Log detallado")
    parser.add_argument("--no-backup", action="store_true",
                        help="No crear backup automático antes de escribir")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Consola Windows: permitir caracteres UTF-8 sin UnicodeEncodeError
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    db_path = resolver_db(args.db)
    if not os.path.isfile(db_path):
        log.error("No existe la DB: %s", db_path)
        return 1

    claves: list[str] = [CLAVE_DESC_EN, CLAVE_DESC_ES]
    if args.solo_en:
        claves = [CLAVE_DESC_EN]
    elif args.solo_es:
        claves = [CLAVE_DESC_ES]

    if args.mode == "replace":
        log.info("  [replace] Operación idempotente: no hay datos previos que limpiar.")
    elif args.mode == "update":
        log.info("  [update] Idéntico a skip: solo se recorta el inicio, no se sobrescribe nada.")

    log.info("Limpieza de descripciones (mode=%s, claves=%s, dry_run=%s)",
             args.mode, ",".join(claves), args.dry_run)

    conn = abrir(db_path)
    try:
        resumen_auditoria = auditar(conn)

        # Primera pasada en seco: contar cuánto se modificaría (sin escribir)
        resumen = limpiar(conn, dry_run=True, claves=claves)

        ruta_backup: str | None = None
        if not args.dry_run and resumen["modificados"] > 0:
            if not args.no_backup:
                ruta_backup = _crear_backup(db_path)
                log.info("  Backup creado antes de escribir: %s", ruta_backup)
            # Segunda pasada real (escribe)
            resumen = limpiar(conn, dry_run=False, claves=claves)
        elif not args.dry_run:
            log.info("  Nada para limpiar: no se crea backup ni se escribe.")

        # ── Aperturas legítimas (negativos): informe + invariante real ──
        # Los conteos ANTES son informativos: tras la limpieza pueden AUMENTAR
        # legítimamente porque recortar un meta-intro REVELA una apertura
        # legítima debajo ("Aquí hay una descripción...\n\nLa imagen muestra..."
        # → "La imagen muestra..."). El invariante real es que ningún registro
        # que YA empezaba con una apertura legítima haya sido modificado.
        print("\n  Aperturas legítimas antes de la limpieza (información):")
        for opener, cantidad in resumen_auditoria["negativos"].items():
            print(f"    {opener!r:<30} {cantidad}")
        violaciones = resumen["violaciones_negativos"]
        if not violaciones:
            print("  Invariante: ningún registro con apertura legítima fue modificado — OK")
        else:
            for media_id, clave in violaciones:
                log.error(
                    "  ⚠ Violación de apertura legítima: media %s clave %s fue modificado",
                    media_id, clave,
                )

        # ── Resumen final ──
        residuo_en = resumen_auditoria["residuos"].get("'to describe the image' (EN, pos>1)", 0)
        residuo_es = resumen_auditoria["residuos"].get("'para describir la imagen' (ES, pos>1)", 0)
        log.info(
            "Resumen: claves=%s revisados=%d modificados=%d residuos_en=%d "
            "residuos_es=%d violaciones_negativos=%d backup=%s",
            ",".join(claves),
            resumen["revisados"],
            resumen["modificados"],
            residuo_en,
            residuo_es,
            len(resumen["violaciones_negativos"]),
            ruta_backup or "—",
        )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
