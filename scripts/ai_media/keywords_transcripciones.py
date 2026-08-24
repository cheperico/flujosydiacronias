#!/usr/bin/env python3
"""
keywords_transcripciones.py — Extrae keywords del SENTIDO desde transcripciones
de audio/video o desde los textos ingresados (.md).

La lógica de extracción (prompt, parse, limpieza, modos skip/update/replace,
checkpoint) es agnóstica de la fuente. El origen se elige con `--origen` y
parametriza 4 cosas: (1) la fuente de texto, (2) la query de medios, (3) la
clave de salida en `media_metadata` y (4) el encabezado del prompt.

  - `transcripcion` (default): lee `whisper_segments` (JSON array de segmentos
    `{"inicio": ..., "fin": ..., "texto": ...}`) de audios/videos, combina los
    textos en orden por `inicio` y guarda en `ia_keywords_transcripcion`.
  - `texto`: lee directo el valor de `texto_completo` de los medios
    `type='text'` y guarda en `ia_keywords_texto`.

En ambos casos llama a un modelo de texto de Ollama (gemma3:latest) para extraer
las keywords del SENTIDO en español: EXACTAMENTE 5 (temas, lugares, actividades,
personas, emociones, clima... no solo palabras literales), entregadas como un
objeto JSON {"tags": ["a", "b", "c", "d", "e"]} que se parsea y se guarda
coma-separado en la DB. Las claves de salida NO se mezclan con `ia_keywords`,
que es de visión para imágenes.

Uso:
    python scripts/ai_media/keywords_transcripciones.py                               # transcripciones, solo pendientes (default)
    python scripts/ai_media/keywords_transcripciones.py --origen texto            # desde textos .md (media type='text')
    python scripts/ai_media/keywords_transcripciones.py --mode update             # re-procesa todos los del origen
    python scripts/ai_media/keywords_transcripciones.py --mode replace            # limpia la clave del origen y regenera
    python scripts/ai_media/keywords_transcripciones.py --dry-run --origen texto  # previsualiza los textos sin escribir
    python scripts/ai_media/keywords_transcripciones.py --dry-run --origen texto --probar-ollama  # además llama al modelo
    python scripts/ai_media/keywords_transcripciones.py --limit 5                 # procesa solo 5 registros
    python scripts/ai_media/keywords_transcripciones.py --modelo gemma3:latest

Modos:
    skip    → solo los del origen que aún NO tienen su clave de salida (default)
    update  → re-procesa TODOS los del origen (sobrescribe)
    replace → limpia la clave del origen existente y regenera

Nota: si el texto (combinado o directo) es vacío o tiene menos de MIN_TEXTO_LEN
(40) caracteres, el registro se salta y se loguea (no hay contenido útil).
"""

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import time

log = logging.getLogger(__name__)

# ── Claves en DB ─────────────────────────────────────────────────────────────
CLAVE_SEGMENTOS = "whisper_segments"           # clave de entrada (transcripción)
CLAVE_SALIDA_TRANSCRIPCION = "ia_keywords_transcripcion"  # salida (keywords transcripción)
CLAVE_TEXTO_COMPLETO = "texto_completo"        # clave de entrada (textos .md)
CLAVE_SALIDA_TEXTO = "ia_keywords_texto"       # salida (keywords textos)

# ── Modelo de texto para extracción de keywords ──────────────────────────────
# gemma3:latest ganó el A/B (93 llamadas, Ago 2026) contra qwen2.5:3b con el
# prompt endurecido P2 (ver PROMPT_KEYWORDS_TRANSCRIPCION / PROMPT_KEYWORDS_TEXTO).
MODELO_TEXTO_DEFAULT = "gemma3:latest"

# ── Umbrales ─────────────────────────────────────────────────────────────────
MIN_TEXTO_LEN = 40            # menos de N caracteres → no hay contenido útil
MAX_TEXTO_CHARS = 6000        # truncar el texto que se envía al modelo
MAX_KEYWORDS = 5              # cap defensivo: el prompt pide exactamente 5; recortar si devuelve más
TIMEOUT_SEG = 120             # timeout de la llamada a Ollama

# ── Prompts de extracción (mismas reglas; cambia la cabecera) ────────────────
# Prompt FUSIONADO (Ene 2026): las 7 reglas endurecidas P2 (ganadoras del A/B
# contra qwen2.5:3b) + contrato de salida JSON con EXACTAMENTE 5 keywords
# (gemma saturaba el viejo "entre 5 y 8" siempre en 8, fragmentando la nube).
# El terminador ("Transcripción:\n" / "Texto:\n") y la estructura de
# concatenación NO cambian: `extraer_keywords_*` concatena prompt + texto
# fuente, por lo que el parseo queda intacto.
PROMPT_KEYWORDS_TRANSCRIPCION = (
    "Analizá la transcripción y extraé las keywords del SENTIDO de lo que se dice "
    "(de qué trata realmente, no de las palabras sueltas).\n"
    "Reglas OBLIGATORIAS:\n"
    "1. Formato: SOLO un objeto JSON válido con exactamente 5 keywords en ESPAÑOL: "
    "{\"tags\": [\"a\", \"b\", \"c\", \"d\", \"e\"]}. Sin texto adicional. El ejemplo es solo "
    "formato; sus tags NO pertenecen a este texto.\n"
    "2. Las keywords salen del SIGNIFICADO: temas, lugares, actividades, personas, emociones, "
    "clima, objetos, transporte, comida, sensaciones.\n"
    "3. PROHIBIDO palabras vacías o muletillas: bien, buen, buena, bueno, finalmente, falta, "
    "tranquilo, cuidado, solo, siempre, después, ya, cosa, algo, 'luz' (salvo tema central).\n"
    "4. NO copies errores de transcripción: si una palabra es artefacto de voz, ignorala.\n"
    "5. Sé FIEL: no agregues interpretaciones que el texto no sostenga (si se ayudaron → "
    "'solidaridad', nunca 'sociedad individualista').\n"
    "6. Escribí bien las compuestas: respetá género y número.\n"
    "7. Preferí palabras de contenido concreto antes que adverbios o adjetivos genéricos.\n\n"
    "Transcripción:\n"
)

PROMPT_KEYWORDS_TEXTO = (
    "Analizá este **texto** y extraé las keywords del SENTIDO de lo que se dice "
    "(de qué trata realmente, no de las palabras sueltas).\n"
    "Reglas OBLIGATORIAS:\n"
    "1. Formato: SOLO un objeto JSON válido con exactamente 5 keywords en ESPAÑOL: "
    "{\"tags\": [\"a\", \"b\", \"c\", \"d\", \"e\"]}. Sin texto adicional. El ejemplo es solo "
    "formato; sus tags NO pertenecen a este texto.\n"
    "2. Las keywords salen del SIGNIFICADO: temas, lugares, actividades, personas, emociones, "
    "clima, objetos, transporte, comida, sensaciones.\n"
    "3. PROHIBIDO palabras vacías o muletillas: bien, buen, buena, bueno, finalmente, falta, "
    "tranquilo, cuidado, solo, siempre, después, ya, cosa, algo, 'luz' (salvo tema central).\n"
    "4. NO copies errores de transcripción: si una palabra es artefacto de voz, ignorala.\n"
    "5. Sé FIEL: no agregues interpretaciones que el texto no sostenga (si se ayudaron → "
    "'solidaridad', nunca 'sociedad individualista').\n"
    "6. Escribí bien las compuestas: respetá género y número.\n"
    "7. Preferí palabras de contenido concreto antes que adverbios o adjetivos genéricos.\n\n"
    "Texto:\n"
)

# Muletillas / ruido común del habla humano que el modelo podría dejar pasar
MULETILLAS = {
    "mmm", "mm", "eh", "este", "bueno", "digamos", "o sea", "sabés", "sabes",
    "viste", "mirá", "mirá vos", "básicamente", "obviamente", "tipo",
    "como que", "no sé", "y", "o", "que", "a", "en", "de", "la", "el", "un",
}

# Patrones basura que a veces regurgita el modelo (restos del prompt)
PATRONES_BASURA = [
    r"^keywords?\s*[:：]",
    r"^lista\s*[:：]",
    r"^las\s+palabras\s+clave",
    r"^aquí",
    r"^aqu\s*í",
    r"^transcripci[oó]n",
    r"^\d+[.)]\s*",           # numeración "1. perro"
    r"^[\"'.*-]+",
]


# ── Configuración por origen ─────────────────────────────────────────────────
# Cada origen es una familia de keywords: fuente de texto, clave de salida,
# tipos de medio involucrados y prompt. La lógica de procesado es la misma.
ORIGEN_TRANSCRIPCION = "transcripcion"
ORIGEN_TEXTO = "texto"

ORIGENES: dict[str, dict] = {
    ORIGEN_TRANSCRIPCION: {
        "clave_entrada": CLAVE_SEGMENTOS,
        "clave_salida": CLAVE_SALIDA_TRANSCRIPCION,
        "tipos": ("audio", "video"),
        "prompt": PROMPT_KEYWORDS_TRANSCRIPCION,
        "etiqueta": "transcripciones",
    },
    ORIGEN_TEXTO: {
        "clave_entrada": CLAVE_TEXTO_COMPLETO,
        "clave_salida": CLAVE_SALIDA_TEXTO,
        "tipos": ("text",),
        "prompt": PROMPT_KEYWORDS_TEXTO,
        "etiqueta": "textos",
    },
}


def _config_origen(origen: str) -> dict:
    """
    Valida el origen elegido y devuelve su configuración (claves, tipos,
    prompt). Eleva ValueError si el origen no existe.
    """
    if origen not in ORIGENES:
        raise ValueError(
            f"Origen inválido: {origen!r}. Válidos: {', '.join(ORIGENES)}"
        )
    return ORIGENES[origen]


# ── Helpers ──────────────────────────────────────────────────────────────────


def combinar_texto_segmentos(segmentos_json: str) -> str:
    """
    Parsea el JSON de `whisper_segments` y concatena los textos en orden por `inicio`.

    Args:
        segmentos_json: Valor de media_metadata (JSON array de dicts).

    Returns:
        Texto único con todos los segmentos unidos por espacio ('' si no hay).
    """
    if not segmentos_json:
        return ""
    try:
        segmentos = json.loads(segmentos_json)
    except (json.JSONDecodeError, TypeError):
        log.warning("  No se pudo parsear whisper_segments (JSON inválido).")
        return ""
    if not isinstance(segmentos, list):
        log.warning("  whisper_segments no es una lista.")
        return ""

    # Ordenar por inicio (float/int) y concatenar en ese orden
    textos: list[tuple[float, str]] = []
    for seg in segmentos:
        if not isinstance(seg, dict):
            continue
        texto = str(seg.get("texto", "") or "").strip()
        if not texto:
            continue
        try:
            inicio = float(seg.get("inicio", 0) or 0)
        except (TypeError, ValueError):
            inicio = 0.0
        textos.append((inicio, texto))

    textos.sort(key=lambda t: t[0])
    return " ".join(t for _, t in textos).strip()


def _texto_desde_fila(fila, origen: str) -> str:
    """
    Devuelve el texto fuente a extrear según el origen.

    - transcript: concatena los segmentos JSON de `whisper_segments`.
    - texto: el valor directo de la clave `texto_completo`.
    """
    valor = fila["texto_origen"]
    if origen == ORIGEN_TEXTO:
        return (valor or "").strip()
    return combinar_texto_segmentos(valor)


def _limpiar_keyword(palabra: str) -> str:
    """Limpia una keyword individual (espacios, puntuación, numeración)."""
    p = palabra.strip().lower()
    for pat in PATRONES_BASURA:
        p = re.sub(pat, "", p)
    p = p.strip(" ,.;:—_\"'()[]{}").strip()
    return p


def _es_basura(palabra: str) -> bool:
    """Determina si una keyword es ruido (muletilla, demasiado corta, etc.)."""
    if not palabra:
        return True
    if len(palabra) < 2:
        return True
    if palabra in MULETILLAS:
        return True
    # Palabras sin vocales → casi seguro ruido
    if not re.search(r"[aeiouáéíóú]", palabra):
        return True
    return False


def _parsear_keywords(respuesta: str) -> list[str]:
    """
    Convierte la respuesta del modelo en lista de keywords limpias.

    Formato esperado (prompt fusionado): objeto JSON con clave "tags":
      {"tags": ["perro", "ruta", "sol", "campo", "viento"]}

    Fallbacks por robustez (respuestas fuera del contrato):
      - Objeto JSON con claves alternativas ("keywords", "palabras_clave").
      - Array JSON plano: ["perro", "ruta"].
      - Bloque de código markdown (```json ... ```).
      - JSON casi-válido con restos alrededor (ej. "}:" tras la llave):
        rescata el array de "tags" por regex.
      - Texto coma-separado: "perro, ruta, sol".
      - Texto con numeración: "1. perro 2. ruta".

    Cap defensivo: si el modelo devuelve más de MAX_KEYWORDS (5), se conservan
    las primeras 5.

    Args:
        respuesta: Texto crudo devuelto por Ollama.

    Returns:
        Lista de keywords limpias (sin ruido), máximo MAX_KEYWORDS.
    """
    if not respuesta:
        return []
    texto = respuesta.strip()

    # 0. Quitar vallas markdown (```json ... ```) si el modelo las agrega
    coincidencia = re.search(r"```(?:json)?\s*(.*?)\s*```", texto, flags=re.DOTALL)
    if coincidencia:
        texto = coincidencia.group(1).strip()

    # 1. Intentar parsear como JSON
    partes: list[str] = []
    try:
        datos = json.loads(texto)
        if isinstance(datos, list):
            partes = [str(x) for x in datos]
        elif isinstance(datos, dict):
            kws = (datos.get("tags") or datos.get("keywords")
                   or datos.get("palabras_clave") or [])
            partes = [str(x) for x in kws] if isinstance(kws, list) else [str(kws)]
        else:
            partes = []
    except (json.JSONDecodeError, TypeError):
        # 1b. JSON malformado pero con el contrato nuevo visible (ej. el modelo
        # dejó restos como "}:" tras la llave de cierre): rescatar el array de
        # "tags" por regex antes de caer al fallback por comas.
        coincidencia_tags = re.search(r'"tags"\s*:\s*\[(.*?)\]', texto, flags=re.DOTALL)
        if coincidencia_tags:
            partes = re.split(r"[,\n]+", coincidencia_tags.group(1))

    # 2. Fallback: separar por comas / saltos
    if not partes:
        partes = re.split(r"[,;\n]+", texto)

    resultado = []
    for p in partes:
        limpia = _limpiar_keyword(p)
        if limpia and not _es_basura(limpia) and limpia not in resultado:
            resultado.append(limpia)
    return resultado[:MAX_KEYWORDS]


def extraer_keywords_transcripcion(
    cliente,
    texto: str,
    modelo: str,
    prompt: str | None = None,
) -> list[str]:
    """
    Llama al modelo de texto y devuelve las keywords del texto fuente.

    Args:
        cliente: ollama.Client
        texto: Texto fuente (transcripción combinada o texto_completo directo).
        modelo: Nombre del modelo de texto (ej: gemma3:latest).
        prompt: Template del prompt. Si no se pasa, se usa el de transcripciones.

    Returns:
        Lista de keywords en español.
    """
    if prompt is None:
        prompt = PROMPT_KEYWORDS_TRANSCRIPCION

    # Truncar textos muy largos (protección de contexto)
    if len(texto) > MAX_TEXTO_CHARS:
        log.debug("  Texto truncado a %d caracteres (original: %d)", MAX_TEXTO_CHARS, len(texto))
        texto = texto[:MAX_TEXTO_CHARS]

    # Concatenación directa (evita problemas si el texto contiene llaves)
    prompt_final = prompt + texto
    respuesta = cliente.chat(
        model=modelo,
        messages=[{"role": "user", "content": prompt_final}],
        options={"num_ctx": 4096, "temperature": 0.2},
    ).message.content.strip()

    return _parsear_keywords(respuesta)[:MAX_KEYWORDS]


# ── Queries según modo y origen ──────────────────────────────────────────────


def _query_segun_modo(mode: str, origen: str) -> tuple[str, list]:
    """
    Devuelve (query, params) para listar los medios a procesar según el modo
    y el origen de texto.

    transcripcion → audios/videos con `whisper_segments`
    texto         → media type='text' con `texto_completo`

    skip    → solo registros SIN la clave de salida del origen
    update  → todos los de la fuente (sobrescribe)
    replace → todos los de la fuente (el clean va aparte)
    """
    cfg = _config_origen(origen)
    clave_entrada = cfg["clave_entrada"]
    clave_salida = cfg["clave_salida"]

    if origen == ORIGEN_TEXTO:
        base = """
            SELECT m.id, m.filename_original, m.type, mm.value AS texto_origen
            FROM media m
            JOIN media_metadata mm
              ON mm.media_id = m.id AND mm.key = ?
            WHERE m.type = ?
        """
        params_base = [clave_entrada, cfg["tipos"][0]]
    else:
        base = """
            SELECT m.id, m.filename_original, m.type, mm.value AS texto_origen
            FROM media m
            JOIN media_metadata mm
              ON mm.media_id = m.id AND mm.key = ?
            WHERE m.type IN ('audio', 'video')
        """
        params_base = [clave_entrada]

    if mode == "skip":
        query = base + """
            AND NOT EXISTS (
                SELECT 1 FROM media_metadata out_
                WHERE out_.media_id = m.id AND out_.key = ?
            )
            ORDER BY m.id
        """
        return query, params_base + [clave_salida]
    return base + " ORDER BY m.id", params_base


# ── Main ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Extrae keywords del SENTIDO de transcripciones (whisper_segments) "
                    "o de textos ingresados (texto_completo) y las guarda en "
                    "media_metadata con clave 'ia_keywords_transcripcion' o 'ia_keywords_texto'.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", default=None, help="Ruta a la base de datos (default: db/flujos.db)")
    parser.add_argument("--origen", default=ORIGEN_TRANSCRIPCION, choices=list(ORIGENES),
                        help="Fuente del texto: 'transcripcion' (default, audios/videos con "
                             "whisper_segments) o 'texto' (media type='text' con texto_completo).")
    parser.add_argument("--mode", default="skip", choices=["skip", "update", "replace"],
                        help="skip: solo sin keywords (default) | update: todos | replace: limpia y regenera")
    parser.add_argument("--modelo", default=MODELO_TEXTO_DEFAULT,
                        help=f"Modelo de texto para extracción (default: {MODELO_TEXTO_DEFAULT})")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limitar a N registros (para pruebas)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Previsualizar sin escribir (con --probar-ollama además llama al modelo)")
    parser.add_argument("--probar-ollama", action="store_true",
                        help="Solo con --dry-run: hace la llamada real a Ollama y muestra las keywords propuestas")
    parser.add_argument("--verbose", action="store_true", help="Log detallado")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.probar_ollama and not args.dry_run:
        log.warning("  --probar-ollama solo tiene efecto con --dry-run. Se ignora.")

    # Resolver DB (permite ejecución standalone desde cualquier directorio)
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from db.util import abrir, resolver_db

    db_path = resolver_db(args.db)
    if not os.path.isfile(db_path):
        log.error("No existe la DB: %s", db_path)
        sys.exit(1)

    cfg = _config_origen(args.origen)

    conn = abrir(db_path)
    conn.row_factory = sqlite3.Row

    # ── Modo replace: limpiar la clave de salida del origen antes de regenerar ──
    if args.mode == "replace":
        conn.execute(
            "DELETE FROM media_metadata WHERE key = ?", (cfg["clave_salida"],)
        )
        conn.commit()
        log.info("  [replace] Limpiado %s de la DB.", cfg["clave_salida"])

    query, params = _query_segun_modo(args.mode, args.origen)
    rows = conn.execute(query, params).fetchall()
    if args.limit:
        rows = rows[:args.limit]

    if not rows:
        print(f"  No hay registros con {cfg['clave_entrada']} (origen={args.origen}) para procesar.")
        conn.close()
        return

    log.info("  Registros con %s: %d (origen=%s, mode=%s, modelo=%s)",
             cfg["clave_entrada"], len(rows), args.origen, args.mode, args.modelo)

    # ── Dry-run (sin escribir) ──
    if args.dry_run:
        print(f"\n  [DRY-RUN] Registros a procesar (origen={args.origen}, máx 5):")
        for r in rows[:5]:
            texto = _texto_desde_fila(r, args.origen)
            estado = "OK" if len(texto) >= MIN_TEXTO_LEN else "SKIP (texto corto)"
            print(f"\n  media {r['id']} [{r['type']}] {r['filename_original']}")
            print(f"    estado: {estado} | longitud: {len(texto)} chars")
            print(f"    preview: {texto[:150]}...")

            if args.probar_ollama and len(texto) >= MIN_TEXTO_LEN:
                try:
                    import ollama
                    from scripts.ai_media.ollama_client import asegurar_ollama
                    if asegurar_ollama():
                        cliente = ollama.Client(timeout=TIMEOUT_SEG)
                        keywords = extraer_keywords_transcripcion(
                            cliente, texto, args.modelo, prompt=cfg["prompt"])
                        print(f"    keywords propuestas: {', '.join(keywords) if keywords else '—'}")
                    else:
                        print("    ⚠ Ollama no disponible, no se probó la llamada.")
                except Exception as e:
                    print(f"    ⚠ Error llamando a Ollama: {e}")

        print(f"\n  Total: {len(rows)}")
        conn.close()
        return

    # ── Modo real: procesar (envuelto en manejar_interrupcion) ──
    # Al cortar con Ctrl+C se commitean los pendientes (el guardado es por
    # ítem cada 25) y se sale con mensaje claro, sin traceback.
    from scripts.ai_media.checkpoint import manejar_interrupcion
    with manejar_interrupcion(conn=conn, etiqueta="keywords_transcripciones"):
        _ejecutar(conn, args, rows)


def _ejecutar(conn, args, rows) -> None:
    """
    Extrae las keywords de los registros del origen y las escribe en la DB.

    Separado de main() para poder envolverlo en manejar_interrupcion sin
    re-indentar el cuerpo (mismo nivel de indentación). El guardado por ítem
    (cada 25) es el mismo que tenía el script.
    """
    cfg = _config_origen(args.origen)

    # ── Modo real: importar ollama y asegurar que el servidor esté corriendo ──
    try:
        import ollama
        from scripts.ai_media.ollama_client import asegurar_ollama
    except ImportError as e:
        log.error("No se pudo importar ollama: %s", e)
        conn.close()
        sys.exit(1)

    if not asegurar_ollama():
        log.error("Ollama no está disponible. Abortando.")
        conn.close()
        sys.exit(1)

    cliente = ollama.Client(timeout=TIMEOUT_SEG)

    ok = 0
    errors = 0
    vacios = 0
    t_inicio = time.perf_counter()
    for i, r in enumerate(rows, 1):
        mid = r["id"]
        texto = _texto_desde_fila(r, args.origen)

        if len(texto) < MIN_TEXTO_LEN:
            log.info("  [media %s] texto demasiado corto (%d chars), skip.", mid, len(texto))
            vacios += 1
            continue

        try:
            keywords = extraer_keywords_transcripcion(
                cliente, texto, args.modelo, prompt=cfg["prompt"])
        except Exception as e:
            log.warning("  ⚠ Error extrayendo keywords de media %s: %s", mid, e)
            errors += 1
            continue

        if not keywords:
            log.warning("  ⚠ Sin keywords devueltas para media %s.", mid)
            errors += 1
            continue

        conn.execute(
            "INSERT OR REPLACE INTO media_metadata (media_id, key, value) VALUES (?, ?, ?)",
            (mid, cfg["clave_salida"], ", ".join(keywords)),
        )
        ok += 1
        if args.verbose:
            log.info("  [media %s] keywords: %s", mid, ", ".join(keywords))
        if i % 25 == 0:
            conn.commit()
            log.info("  Progreso: %d/%d (%d ok, %d err, %d vacíos)", i, len(rows), ok, errors, vacios)

    conn.commit()
    total = time.perf_counter() - t_inicio
    log.info("  ✅ Keywords %s: %d ok | %d errores | %d vacíos | %.1fs (%.2fs/media)",
             args.origen, ok, errors, vacios, total, total / max(1, len(rows)))
    conn.close()


if __name__ == "__main__":
    main()