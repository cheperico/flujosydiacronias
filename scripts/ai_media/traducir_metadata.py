#!/usr/bin/env python3
"""
traducir_metadata.py — Traduce keywords y descripciones IA de EN → ES sobre la DB.

El pipeline genera keywords/descripciones con minicpm en INGLÉS y las guarda en
claves temporales (`ia_keywords_en`, `ia_description_en`). Este script lee esas
claves, traduce y escribe los resultados definitivos en español
(`ia_keywords`, `ia_description`).

Motor de traducción (--motor):
  - google (default): glosario + deep_translator.GoogleTranslator (gratis, sin
    API key). Las keywords se resuelven con el glosario (glosario_keywords.json)
    y solo las palabras desconocidas van al motor; las descripciones van al
    motor + reemplazos rioplatenses. NO usa Ollama.
  - argos: igual pero con Argos Translate (offline, descarga el paquete en→es
    en el primer uso).
  - glosario: solo léxico (keywords con glosario; descripciones con motor
    Google por defecto de glosario). NO usa Ollama.
  - ollama: pipeline legacy con translategemma (requiere el servidor Ollama).

El glosario se amplía con palabras desconocidas traducidas por el motor
(origen=auto) salvo que se pase --no-agregar-glosario.

Por qué sobre la DB:
  - La traducción es texto puro contra ~15-20s de visión.
  - No re-procesa imágenes: se puede re-ejecutar con --mode skip cuantas veces
    haga falta sin costo.

Flujo completo:
  1. Visión:  improve_db --steps keywords,descriptions  → escribe *_en
  2. Traducción: este script → escribe ia_keywords/ia_description (ES)
  3. Refinamiento: refinar_keywords.py --mode update

Uso:
    python scripts/ai_media/traducir_metadata.py                          # ambos pasos (google)
    python scripts/ai_media/traducir_metadata.py --paso keywords          # solo keywords
    python scripts/ai_media/traducir_metadata.py --paso descriptions
    python scripts/ai_media/traducir_metadata.py --dry-run                # previsualizar
    python scripts/ai_media/traducir_metadata.py --mode update            # re-traduce todo
    python scripts/ai_media/traducir_metadata.py --motor argos            # offline
    python scripts/ai_media/traducir_metadata.py --motor ollama --modelo translategemma

Modos:
    skip    → solo registros que tienen EN y aún NO tienen ES (default)
    update  → re-traduce TODOS los registros que tienen EN (sobrescribe)
    replace → limpia el ES existente, luego traduce todo de nuevo

Nota: el género fotográfico fue descartado (Ago 2026). Las keywords se traducen
tal cual; no se fuerza ningún género comodín (refinar_keywords.py ya no inserta
"otras").
"""

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import time

try:
    from scripts.ai_media.prompts import get_config, get_prompt
except ImportError:
    # Ejecución directa como script (python scripts/ai_media/x.py):
    # cargar prompts.py por ruta de archivo, sin pasar por el paquete
    # `scripts` (su __init__ arrastra dependencias pesadas y el nombre
    # puede venir cacheado de otro lado en sys.modules).
    import importlib.util
    _PROMPTS_PATH = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "prompts.py")
    _spec = importlib.util.spec_from_file_location("flujos_prompts", _PROMPTS_PATH)
    _modulo_prompts = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_modulo_prompts)
    get_config = _modulo_prompts.get_config
    get_prompt = _modulo_prompts.get_prompt

log = logging.getLogger(__name__)

# ── Modelo de texto para traducción ──────────────────────────────────────────
# Default efectivo en prompts.yaml (traduccion.*). Solo se usa con
# --motor ollama (legacy); el default del pipeline es NO-AI (sin prompt).
MODELO_TRADUCCION_DEFAULT = "translategemma"

# ── Claves en DB ─────────────────────────────────────────────────────────────
CLAVE_KW_EN = "ia_keywords_en"
CLAVE_DESC_EN = "ia_description_en"
CLAVE_KW_ES = "ia_keywords"
CLAVE_DESC_ES = "ia_description"

# ── Prompts de traducción (editables en prompts.yaml, grupo "traduccion") ───
# Solo pipeline legacy (--motor ollama). Se resuelven en vivo en
# traducir_llamada() (argumento explícito > YAML > DEFAULTS en prompts.py).


# ── Helpers ──────────────────────────────────────────────────────────────────


def leer_valor_db(valor: str | None) -> list[str]:
    """Convierte el valor de ia_keywords_* en DB a lista (JSON o string)."""
    if not valor:
        return []
    try:
        lista = json.loads(valor)
        if isinstance(lista, list):
            return [str(x) for x in lista]
    except (json.JSONDecodeError, TypeError):
        pass
    partes = [p.strip().strip("'\"").rstrip(".,;") for p in valor.split(",") if p.strip()]
    return partes


def reparar_json(texto: str) -> dict | None:
    """Parsea el JSON de respuesta con recorte de basura y cierre de brackets."""
    texto = texto.strip()
    ini = texto.find("{")
    fin = texto.rfind("}")
    if ini != -1 and fin != -1 and fin > ini:
        texto = texto[ini:fin + 1]
    intentos = [texto, texto.replace("'", '"')]
    for base in intentos:
        try:
            datos = json.loads(base)
            if isinstance(datos, dict):
                return datos
        except (json.JSONDecodeError, TypeError):
            pass
    # Cerrar brackets faltantes (JSON truncado al final)
    for base in intentos:
        for _ in range(8):
            abren = base.count("[") + base.count("{")
            cierran = base.count("]") + base.count("}")
            if abren == cierran:
                break
            if base.count("[") > base.count("]"):
                base = base.rstrip() + "]"
            elif base.count("{") > base.count("}"):
                base = base.rstrip() + "}"
            try:
                datos = json.loads(base)
                if isinstance(datos, dict):
                    return datos
            except (json.JSONDecodeError, TypeError):
                continue
    return None


def traducir_llamada(
    cliente,
    kw_en: list[str],
    desc_en: str,
    paso: str,
    modelo: str | None,
) -> tuple[list[str], str, str]:
    """
    Hace UNA llamada al modelo de texto y devuelve (keywords_es, descripcion_es, prompt_usado).

    Args:
        cliente: ollama.Client
        kw_en: keywords en inglés (lista)
        desc_en: descripción en inglés (string)
        paso: 'keywords' | 'descriptions' | 'ambos'
        modelo: modelo de texto (None = prompts.yaml traduccion.*)

    Returns:
        (keywords_es, descripcion_es, prompt) — uno de los dos puede ser None.
    """
    kw_str = ", ".join(kw_en) if kw_en else ""
    desc_str = desc_en.strip() if desc_en else ""

    if paso == "keywords" or (paso == "ambos" and not desc_str):
        cfg = get_config("traduccion.keywords")
        prompt = get_prompt("traduccion.keywords", kw=kw_str)
        respuesta = cliente.chat(
            model=modelo or cfg["modelo"],
            messages=[{"role": "user", "content": prompt}],
            options={"num_ctx": 1024, "temperature": cfg["temperatura"]},
        ).message.content.strip()
        # La respuesta es "palabras, separadas, por, comas"
        partes = [p.strip().strip("'\"") for p in respuesta.split(",") if p.strip()]
        partes = [p for p in partes if p]
        return (partes or None, None, "keywords")

    if paso == "descriptions" or (paso == "ambos" and not kw_str):
        cfg = get_config("traduccion.descripcion")
        prompt = get_prompt("traduccion.descripcion", desc=desc_str)
        respuesta = cliente.chat(
            model=modelo or cfg["modelo"],
            messages=[{"role": "user", "content": prompt}],
            options={"num_ctx": 2048, "temperature": cfg["temperatura"]},
        ).message.content.strip()
        return (None, respuesta or None, "descriptions")

    # ambos: una llamada JSON
    cfg = get_config("traduccion.ambos")
    prompt = get_prompt("traduccion.ambos", kw=kw_str, desc=desc_str)
    respuesta = cliente.chat(
        model=modelo or cfg["modelo"],
        messages=[{"role": "user", "content": prompt}],
        options={"num_ctx": 2048, "temperature": cfg["temperatura"]},
    ).message.content.strip()

    datos = reparar_json(respuesta)
    if datos is None:
        log.warning("  No se pudo parsear JSON de traducción. Respuesta: %s", respuesta[:200])
        return (None, None, "ambos-json-error")

    keywords_es = None
    descripcion_es = None
    kws = datos.get("keywords")
    if isinstance(kws, str):
        partes = [p.strip().strip("'\"") for p in kws.split(",") if p.strip()]
        if partes:
            keywords_es = partes
    elif isinstance(kws, list):
        keywords_es = [str(x) for x in kws] or None
    desc = datos.get("description")
    if isinstance(desc, str) and desc.strip():
        descripcion_es = desc.strip()

    return (keywords_es, descripcion_es, "ambos-json")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Traduce keywords/descripciones IA de EN a ES sobre la DB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", default=None, help="Ruta a la base de datos (default: db/flujos.db)")
    parser.add_argument("--paso", default="ambos", choices=["keywords", "descriptions", "ambos"],
                        help="Qué traducir (default: ambos)")
    parser.add_argument("--modelo", default=None,
                        help="Modelo de texto para traducción ollama (default: prompts.yaml traduccion.*)")
    parser.add_argument("--motor", default="google",
                        choices=["glosario", "google", "argos", "ollama"],
                        help="Motor de traducción: google (default, sin Ollama) | "
                             "argos (offline) | glosario (solo léxico) | ollama (legacy con translategemma)")
    parser.add_argument("--glosario", default=None,
                        help="Ruta al glosario JSON (default: glosario_keywords.json en la raíz del proyecto)")
    parser.add_argument("--no-agregar-glosario", action="store_true",
                        help="NO ampliar el glosario con palabras nuevas (default: se amplía con origen=auto)")
    parser.add_argument("--mode", default="skip", choices=["skip", "update", "replace"],
                        help="skip: solo EN sin ES (default) | update: re-traduce todos | replace: limpia y traduce")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limitar a N registros (para pruebas)")
    parser.add_argument("--dry-run", action="store_true", help="Previsualizar sin escribir")
    parser.add_argument("--verbose", action="store_true", help="Log detallado")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Resolver DB
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from db.util import resolver_db

    db_path = resolver_db(args.db)
    if not os.path.isfile(db_path):
        log.error("No existe la DB: %s", db_path)
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Construir query según modo y paso
    condiciones: list[str] = []
    if args.paso in ("keywords", "ambos"):
        condiciones.append(f"(SELECT value FROM media_metadata mm WHERE mm.media_id = m.id AND mm.key = '{CLAVE_KW_EN}') IS NOT NULL")
    if args.paso in ("descriptions", "ambos"):
        condiciones.append(f"(SELECT value FROM media_metadata mm WHERE mm.media_id = m.id AND mm.key = '{CLAVE_DESC_EN}') IS NOT NULL")
    if args.mode == "skip":
        if args.paso in ("keywords", "ambos"):
            condiciones.append(f"(SELECT value FROM media_metadata mm WHERE mm.media_id = m.id AND mm.key = '{CLAVE_KW_ES}') IS NULL")
        if args.paso in ("descriptions", "ambos"):
            condiciones.append(f"(SELECT value FROM media_metadata mm WHERE mm.media_id = m.id AND mm.key = '{CLAVE_DESC_ES}') IS NULL")

    where = " AND ".join(condiciones) if condiciones else "1=1"
    query = f"""
        SELECT m.id,
               (SELECT value FROM media_metadata mm WHERE mm.media_id = m.id AND mm.key = '{CLAVE_KW_EN}') AS kw_en,
               (SELECT value FROM media_metadata mm WHERE mm.media_id = m.id AND mm.key = '{CLAVE_DESC_EN}') AS desc_en,
               (SELECT value FROM media_metadata mm WHERE mm.media_id = m.id AND mm.key = '{CLAVE_KW_ES}') AS kw_es,
               (SELECT value FROM media_metadata mm WHERE mm.media_id = m.id AND mm.key = '{CLAVE_DESC_ES}') AS desc_es
        FROM media m
        WHERE {where}
        ORDER BY m.id
    """
    rows = conn.execute(query).fetchall()
    if args.limit:
        rows = rows[:args.limit]

    if not rows:
        print("  No hay registros para traducir.")
        conn.close()
        return

    log.info("  Registros a traducir: %d (paso=%s, mode=%s)", len(rows), args.paso, args.mode)

    # Limpiar en modo replace
    if args.mode == "replace":
        ids = [r["id"] for r in rows]
        if ids:
            conn.execute(
                f"DELETE FROM media_metadata WHERE media_id IN ({','.join('?' * len(ids))}) AND key IN (?, ?)",
                [*ids, CLAVE_KW_ES, CLAVE_DESC_ES])
            conn.commit()
            log.info("  Limpiado ES existente de %d registros", len(ids))

    if args.dry_run:
        print("\n  [DRY-RUN] Registros a traducir (máx 5):")
        for r in rows[:5]:
            kw_en = leer_valor_db(r["kw_en"])
            desc_en = r["desc_en"] or ""
            print(f"\n  media {r['id']}:")
            print(f"    KW_EN: {kw_en}")
            print(f"    DESC_EN: {desc_en[:120]}...")
        print(f"\n  Total: {len(rows)}")
        conn.close()
        return

    # Envolver el trabajo real con manejo de interrupción: al cortar con
    # Ctrl+C se commitean los pendientes (el guardado ya es por ítem cada 25)
    # y se sale con mensaje claro (manejar_interrupcion), sin traceback.
    from scripts.ai_media.checkpoint import manejar_interrupcion
    with manejar_interrupcion(conn=conn, etiqueta="traducir_metadata"):
        _ejecutar(conn, args, rows)


def _ejecutar(conn, args, rows) -> None:
    """
    Traduce los registros EN → ES sobre la DB (paso real del script).

    Separa el trabajo real de main() para poder envolverlo en
    manejar_interrupcion sin re-indentar el cuerpo. El guardado por ítem
    (cada 25 registros) ya existía y no se modifica.
    """
    if args.motor == "ollama":
        _ejecutar_ollama(conn, args, rows)
    else:
        _ejecutar_glosario(conn, args, rows)


def _ejecutar_ollama(conn, args, rows) -> None:
    """
    Pipeline legacy: traduce con Ollama (translategemma) vía traducir_llamada.

    Es EXACTAMENTE el comportamiento histórico del script. Solo se usa con
    --motor ollama.
    """
    # Importar ollama y asegurar que el servidor esté corriendo
    try:
        import ollama
        from scripts.ai_media.ollama_client import asegurar_ollama
    except ImportError as e:
        log.error("No se pudo importar ollama: %s", e)
        conn.close()
        sys.exit(1)

    if not asegurar_ollama():
        log.error("Ollama no está disponible. Abortando traducción.")
        conn.close()
        sys.exit(1)

    cliente = ollama.Client(timeout=300)

    ok = 0
    errors = 0
    t_inicio = time.perf_counter()
    for i, r in enumerate(rows, 1):
        mid = r["id"]
        kw_en = leer_valor_db(r["kw_en"])
        desc_en = r["desc_en"] or ""

        try:
            keywords_es, descripcion_es, prompt_usado = traducir_llamada(
                cliente, kw_en, desc_en, args.paso, args.modelo)
        except Exception as e:
            log.warning("  ⚠ Error traduciendo media %s: %s", mid, e)
            errors += 1
            continue

        cambios = []
        if args.paso in ("keywords", "ambos") and keywords_es:
            conn.execute(
                "INSERT OR REPLACE INTO media_metadata (media_id, key, value) VALUES (?, ?, ?)",
                (mid, CLAVE_KW_ES, ", ".join(keywords_es)))
            cambios.append(f"kw={keywords_es}")
        if args.paso in ("descriptions", "ambos") and descripcion_es:
            conn.execute(
                "INSERT OR REPLACE INTO media_metadata (media_id, key, value) VALUES (?, ?, ?)",
                (mid, CLAVE_DESC_ES, descripcion_es))
            cambios.append(f"desc={descripcion_es[:80]}...")

        if cambios:
            ok += 1
            if args.verbose:
                log.info("  [media %s] %s", mid, " | ".join(cambios))
            if i % 25 == 0:
                conn.commit()
                log.info("  Progreso: %d/%d (%d ok, %d err)", i, len(rows), ok, errors)
        else:
            log.warning("  ⚠ Sin resultado para media %s (paso=%s, prompt=%s)", mid, args.paso, prompt_usado)
            errors += 1

    conn.commit()
    total = time.perf_counter() - t_inicio
    log.info("  ✅ Traducción completa: %d ok | %d errores | %.1fs (%.2fs/img)",
             ok, errors, total, total / max(1, ok + errors))
    conn.close()


def _ejecutar_glosario(conn, args, rows) -> None:
    """
    Pipeline NO-AI: traduce con glosario + motor clásico (sin Ollama).

    - keywords: glosario.traducir_keywords(); las palabras desconocidas se
      traducen con el motor (cache por corrida: una vez por palabra) y, salvo
      --no-agregar-glosario, se agregan al glosario con origen=auto (guardado
      periódico y al final).
    - descriptions: glosario.traducir_descripcion() (motor + reemplazos
      rioplatenses).
    """
    from scripts.ai_media.glosario import Glosario, crear_motor, traducir_con_motor

    glosario = Glosario(args.glosario)
    glosario.cargar()
    motor = crear_motor(args.motor)  # None para 'glosario'
    if motor is not None:
        glosario.motor = motor
    agregar_glosario = not args.no_agregar_glosario

    cache_desconocidas: dict[str, str] = {}
    agregadas = 0
    ok = 0
    errors = 0
    t_inicio = time.perf_counter()
    for i, r in enumerate(rows, 1):
        mid = r["id"]
        kw_en = leer_valor_db(r["kw_en"])
        desc_en = r["desc_en"] or ""

        cambios = []
        try:
            if args.paso in ("keywords", "ambos") and kw_en:
                traducidas, desconocidas = glosario.traducir_keywords(kw_en)
                if motor is not None and desconocidas:
                    # Traducir cada palabra desconocida UNA vez por corrida
                    for palabra in desconocidas:
                        if palabra not in cache_desconocidas:
                            cache_desconocidas[palabra] = traducir_con_motor(motor, palabra)
                            if cache_desconocidas[palabra] and agregar_glosario:
                                glosario.agregar_entradas(
                                    {palabra: cache_desconocidas[palabra]}, origen="auto")
                                agregadas += 1
                    # Reemplazar las desconocidas ya traducidas dentro de cada keyword
                    for palabra, trad in cache_desconocidas.items():
                        if not trad:
                            continue
                        traducidas = [
                            re.sub(rf"\b{re.escape(palabra)}\b", trad, t,
                                   flags=re.IGNORECASE)
                            for t in traducidas
                        ]
                if traducidas:
                    conn.execute(
                        "INSERT OR REPLACE INTO media_metadata (media_id, key, value) VALUES (?, ?, ?)",
                        (mid, CLAVE_KW_ES, ", ".join(traducidas)))
                    cambios.append(f"kw={traducidas}")

            if args.paso in ("descriptions", "ambos") and desc_en:
                desc_es = glosario.traducir_descripcion(desc_en)
                if desc_es:
                    conn.execute(
                        "INSERT OR REPLACE INTO media_metadata (media_id, key, value) VALUES (?, ?, ?)",
                        (mid, CLAVE_DESC_ES, desc_es))
                    cambios.append(f"desc={desc_es[:80]}...")
        except Exception as e:
            log.warning("  ⚠ Error traduciendo media %s: %s", mid, e)
            errors += 1
            continue

        if cambios:
            ok += 1
            if args.verbose:
                log.info("  [media %s] %s", mid, " | ".join(cambios))
        else:
            log.warning("  ⚠ Sin resultado para media %s (paso=%s)", mid, args.paso)
            errors += 1

        if i % 25 == 0:
            conn.commit()
            if agregadas > 0:
                glosario.guardar()
            log.info("  Progreso: %d/%d (%d ok, %d err)", i, len(rows), ok, errors)

    conn.commit()
    if agregadas > 0:
        glosario.guardar()
    total = time.perf_counter() - t_inicio
    log.info("  ✅ Traducción completa: %d ok | %d errores | %.1fs (%.2fs/img)",
             ok, errors, total, total / max(1, ok + errors))
    conn.close()


if __name__ == "__main__":
    main()
