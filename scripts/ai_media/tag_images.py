"""
Etiquetado de imágenes con IA — extrae keywords + descripción y las guarda
en DB y/o sidecar.

Dos modos de operación:

1. **Post-ingesta** (--db): conecta a la DB existente, busca imágenes sin
   etiquetar, extrae keywords + descripción con minicpm-v4.6 y guarda en
   media_metadata.

2. **Pre-ingesta / autónomo** (--folder): procesa imágenes de una carpeta
   y genera sidecars .tags.json sin tocar la DB. El ingest puede levantar
   esos sidecars después.

Formato del sidecar .tags.json:
    {
        "file_hash": "abc123def456",
        "tags": ["bicicleta", "ruta", "atardecer"],
        "descripcion": "Una bicicleta de montaña en un camino de tierra...",
        "modelo": "minicpm-v4.6:latest",
        "fecha": "2026-07-14T10:30:00"
    }

Uso:
    # Post-ingesta: etiquetar imágenes en DB que aún no tienen tags
    python scripts/ai_media/tag_images.py --db db/flujos.db

    # Post-ingesta: solo 20 imágenes, con modelo específico
    python scripts/ai_media/tag_images.py --db db/flujos.db --limit 20 --modelo qwen2.5vl:3b

    # Post-ingesta: generar también sidecars
    python scripts/ai_media/tag_images.py --db db/flujos.db --sidecar

    # Autónomo: etiquetar carpeta completa, crear sidecars
    python scripts/ai_media/tag_images.py --folder D:/Fotos --sidecar

    # Solo ver qué se haría
    python scripts/ai_media/tag_images.py --db db/flujos.db --dry-run
"""

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# Permitir importar scripts/ como paquete
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.ai_media.image_analysis import (
    extraer_keywords,
    describir_imagen,
    analizar_imagen_completo,
)

logger = logging.getLogger(__name__)

# Extensiones de imagen soportadas
EXT_IMAGEN = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp", ".heic", ".heif"}

# Claves en media_metadata
KEY_AI_TAGS = "ia_keywords"
KEY_AI_DESCRIPCION = "ia_description"

# Modos de etiquetado
MODE_COMBINADO = "combinado"      # keywords + descripción en UNA llamada (default)
MODE_KEYWORDS = "keywords"        # solo keywords (rápido)
MODE_DESCRIPCION = "descripcion"   # solo descripción

# Modelos de visión recomendados (default efectivo: prompts.yaml vision.*)
MODELOS_RECOMENDADOS = ["minicpm-v4.6:latest", "qwen2.5vl:latest", "qwen2.5vl:3b",
                        "llama3.2-vision:latest", "gemma4:e4b"]


# ═══════════════════════════════════════════════════════════════
#  UTILIDADES
# ═══════════════════════════════════════════════════════════════

def _hash_rapido(ruta: str) -> str:
    """
    Fingerprint rápido tipo ingest: tamaño + fecha modificación.
    Para detectar cambios en la imagen y decidir si un sidecar es válido.
    NO es el SHA-256 de media.file_hash (esa es la huella del contenido
    completo calculada en la ingesta); este es solo un marcador local
    de "¿cambió el archivo?".
    """
    import hashlib
    stat = Path(ruta).stat()
    h = hashlib.md5()
    h.update(str(stat.st_size).encode())
    h.update(str(stat.st_mtime).encode())
    return h.hexdigest()[:12]


def _fingerprint_valido(sidecar_data: dict, ruta_imagen: str) -> bool:
    """
    Compara el fingerprint del sidecar contra la imagen actual.
    Soporta el campo nuevo 'fingerprint' y el viejo 'file_hash'
    (renombrado en Ago 2026 para no confundirlo con el SHA-256 de la DB).
    """
    fingerprint = sidecar_data.get("fingerprint") or sidecar_data.get("file_hash")
    if not fingerprint:
        return False
    return fingerprint == _hash_rapido(ruta_imagen)


def _es_imagen(ruta: str) -> bool:
    """Verifica si un archivo es imagen por extensión."""
    return Path(ruta).suffix.lower() in EXT_IMAGEN


# ═══════════════════════════════════════════════════════════════
#  SIDECAR .tags.json
# ═══════════════════════════════════════════════════════════════

def _ruta_sidecar(ruta_imagen: str) -> str:
    """Devuelve la ruta del sidecar .tags.json para una imagen."""
    return f"{ruta_imagen}.tags.json"


def _sidecar_existe_valido(ruta_imagen: str) -> Optional[dict[str, Any]]:
    """
    Verifica si existe un sidecar válido para la imagen.

    El sidecar es válido si:
      - El archivo .tags.json existe
      - El file_hash coincide con el de la imagen actual

    Returns:
        Dict con tags y descripcion si el sidecar es válido, None si no.
    """
    sidecar = _ruta_sidecar(ruta_imagen)
    if not Path(sidecar).exists():
        return None

    try:
        with open(sidecar, "r", encoding="utf-8") as f:
            data = json.load(f)

        hash_actual = _hash_rapido(ruta_imagen)
        if _fingerprint_valido(data, ruta_imagen):
            return {
                "tags": data.get("tags", []),
                "descripcion": data.get("descripcion", ""),
            }
    except Exception:
        pass

    return None


def _escribir_sidecar(ruta_imagen: str, resultado: dict[str, Any], modelo: str):
    """Escribe el sidecar .tags.json junto a la imagen."""
    sidecar = _ruta_sidecar(ruta_imagen)
    data = {
        "fingerprint": _hash_rapido(ruta_imagen),
        "tags": resultado["tags"],
        "descripcion": resultado["descripcion"],
        "modelo": modelo,
        "fecha": datetime.now().isoformat(),
    }
    try:
        with open(sidecar, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info("  -> Sidecar escrito: %s", Path(sidecar).name)
    except Exception as e:
        logger.warning("  -> Error escribiendo sidecar %s: %s", sidecar, e)


# ═══════════════════════════════════════════════════════════════
#  BASE DE DATOS
# ═══════════════════════════════════════════════════════════════

def conectar_db(ruta_db: str) -> sqlite3.Connection:
    """Conecta a la DB y verifica que tenga las tablas esperadas."""
    if not Path(ruta_db).exists():
        raise FileNotFoundError(f"No se encuentra la DB: {ruta_db}")

    conn = sqlite3.connect(ruta_db)
    conn.row_factory = sqlite3.Row

    # Verificar que exista la tabla media
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='media'"
    )
    if not cursor.fetchone():
        raise RuntimeError(
            f"La DB {ruta_db} no contiene la tabla 'media'. "
            "¿Es la base de datos correcta?"
        )

    return conn


def obtener_imagenes_sin_procesar(
    conn: sqlite3.Connection, modo: str, limite: Optional[int] = None
) -> list[dict]:
    """
    Busca imágenes en la DB que aún no tienen procesado el campo
    correspondiente al modo indicado.

    Args:
        conn: Conexión a la DB.
        modo: "combinado", "keywords" o "descripcion".
        limite: Máximo de imágenes a retornar.

    Returns:
        Lista de dicts con id, filepath_absoluto, filename_original.
    """
    if modo == MODE_COMBINADO:
        # Faltan AMBOS: ni ai_tags ni ai_description
        query = """
            SELECT m.id, m.filepath_absoluto, m.filename_original
            FROM media m
            WHERE m.type = 'image'
            AND m.id NOT IN (
                SELECT DISTINCT mm1.media_id
                FROM media_metadata mm1
                WHERE mm1.key = ?
            )
            AND m.id NOT IN (
                SELECT DISTINCT mm2.media_id
                FROM media_metadata mm2
                WHERE mm2.key = ?
            )
            ORDER BY m.id
        """
        params = [KEY_AI_TAGS, KEY_AI_DESCRIPCION]

    elif modo == MODE_DESCRIPCION:
        # Faltan solo descripciones (pueden tener tags)
        query = """
            SELECT m.id, m.filepath_absoluto, m.filename_original
            FROM media m
            WHERE m.type = 'image'
            AND m.id NOT IN (
                SELECT mm.media_id
                FROM media_metadata mm
                WHERE mm.key = ?
            )
            ORDER BY m.id
        """
        params = [KEY_AI_DESCRIPCION]

    else:  # MODE_KEYWORDS
        # Faltan solo tags (pueden tener descripción)
        query = """
            SELECT m.id, m.filepath_absoluto, m.filename_original
            FROM media m
            WHERE m.type = 'image'
            AND m.id NOT IN (
                SELECT mm.media_id
                FROM media_metadata mm
                WHERE mm.key = ?
            )
            ORDER BY m.id
        """
        params = [KEY_AI_TAGS]

    if limite:
        query += " LIMIT ?"
        params.append(limite)

    cursor = conn.execute(query, params)
    filas = cursor.fetchall()

    # Verificar que los archivos realmente existan en disco
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
            logger.warning(
                "  -> Imagen no encontrada en disco, skip: %s", ruta
            )

    return resultado


def guardar_en_db(conn: sqlite3.Connection, media_id: int, resultado: dict[str, Any], modo: str = MODE_COMBINADO):
    """
    Guarda tags y/o descripción de una imagen en media_metadata,
    según el modo de etiquetado.

    Args:
        conn: Conexión a la DB.
        media_id: ID del medio en la tabla media.
        resultado: Dict con "tags" y "descripcion".
        modo: "combinado", "keywords" o "descripcion".
              Solo guarda los campos que corresponden al modo.
    """
    try:
        if modo in (MODE_COMBINADO, MODE_KEYWORDS):
            # Guardar tags (pueden ser vacíos si es descripcion-only, por eso check)
            if resultado.get("tags"):
                conn.execute(
                    "INSERT OR REPLACE INTO media_metadata (media_id, key, value) "
                    "VALUES (?, ?, ?)",
                    (media_id, KEY_AI_TAGS,
                     ", ".join(str(t) for t in resultado["tags"])),
                )

        if modo in (MODE_COMBINADO, MODE_DESCRIPCION):
            # Guardar descripción (puede ser vacía si es keywords-only)
            if resultado.get("descripcion"):
                conn.execute(
                    "INSERT OR REPLACE INTO media_metadata (media_id, key, value) "
                    "VALUES (?, ?, ?)",
                    (media_id, KEY_AI_DESCRIPCION, resultado["descripcion"]),
                )

        conn.commit()
    except Exception as e:
        logger.error("  -> Error guardando en DB (id=%d): %s", media_id, e)


# ═══════════════════════════════════════════════════════════════
#  ETIQUETADO IA
# ═══════════════════════════════════════════════════════════════

def etiquetar_imagen(
    ruta: str,
    modelo: str | None = None,
    usar_proxy: bool = True,
    modo: str = MODE_COMBINADO,
) -> dict[str, Any]:
    """
    Extrae tags y/o descripción de una imagen usando modelos de visión.

    Args:
        ruta: Ruta a la imagen.
        modelo: Modelo de visión (None = prompts.yaml vision.* según modo).
        usar_proxy: Si True, redimensiona la imagen antes de enviar a la IA.
        modo: "combinado" -> una sola llamada (default, recomendado),
              "keywords"  -> solo tags (rápido),
              "descripcion" -> solo descripción.

    Returns:
        Dict con "tags" (list[str]) y "descripcion" (str).
        En modo keywords, "descripcion" será "".
        En modo descripcion, "tags" será [].

    Raises:
        FileNotFoundError: Si la imagen no existe.
        ValueError: Si falla la extracción.
    """
    if modo == MODE_COMBINADO:
        # Una sola llamada a la IA para ambos (modelo/temp de prompts.yaml)
        resultado = analizar_imagen_completo(
            ruta,
            modelo=modelo,
            usar_proxy=usar_proxy,
        )
        return {
            "tags": resultado.get("keywords", []),
            "descripcion": resultado.get("description", "").strip().strip('"'),
        }

    elif modo == MODE_KEYWORDS:
        # Solo keywords (rápido, una llamada)
        keywords = extraer_keywords(
            ruta,
            modelo=modelo,
            usar_proxy=usar_proxy,
        )
        return {
            "tags": keywords,
            "descripcion": "",
        }

    elif modo == MODE_DESCRIPCION:
        # Solo descripción (una llamada)
        descripcion = describir_imagen(
            ruta,
            modelo=modelo,
            usar_proxy=usar_proxy,
        )
        return {
            "tags": [],
            "descripcion": descripcion.strip().strip('"'),
        }

    else:
        raise ValueError(f"Modo desconocido: {modo}. Usar: combinado, keywords, descripcion")


# ═══════════════════════════════════════════════════════════════
#  FLUJO PRINCIPAL
# ═══════════════════════════════════════════════════════════════

def procesar_imagen(
    ruta: str,
    modelo: str,
    usar_proxy: bool,
    sidecar: bool,
    dry_run: bool,
    modo: str = MODE_COMBINADO,
) -> Optional[dict[str, Any]]:
    """
    Procesa una imagen: extrae tags y descripción, opcionalmente guarda sidecar.

    Args:
        ruta: Ruta a la imagen.
        modelo: Modelo de visión.
        usar_proxy: Si True, usa proxy redimensionado.
        sidecar: Si True, genera sidecar .tags.json.
        dry_run: Si True, solo muestra qué haría.
        modo: "combinado", "keywords", "descripcion".

    Returns:
        Dict con "tags" y "descripcion", o None si falló.
    """
    if dry_run:
        etiqueta_modo = {
            MODE_COMBINADO: "etiquetaría (keywords + descripción)",
            MODE_KEYWORDS: "extraería keywords",
            MODE_DESCRIPCION: "describiría",
        }
        logger.info("  [DRY RUN] Se %s: %s", etiqueta_modo.get(modo, "procesaría"), Path(ruta).name)
        return None

    try:
        resultado = etiquetar_imagen(ruta, modelo=modelo, usar_proxy=usar_proxy, modo=modo)

        tags_str = ", ".join(resultado["tags"])
        logger.info("  Tags (%d): %s", len(resultado["tags"]), tags_str)
        logger.info("  Descripción: %s", resultado["descripcion"][:120])

        # Sidecar
        if sidecar:
            _escribir_sidecar(ruta, resultado, modelo)

        return resultado

    except Exception as e:
        logger.error("  Error etiquetando %s: %s", Path(ruta).name, e)
        return None


def procesar_desde_db(
    ruta_db: str,
    modelo: str | None = None,
    limite: Optional[int] = None,
    sidecar: bool = False,
    usar_proxy: bool = True,
    dry_run: bool = False,
    modo: str = MODE_COMBINADO,
):
    """
    Modo post-ingesta: etiqueta imágenes de la DB.
    """
    logger.info("Conectando a DB: %s", ruta_db)
    conn = conectar_db(ruta_db)

    imagenes = obtener_imagenes_sin_procesar(conn, modo=modo, limite=limite)

    if not imagenes:
        logger.info("No hay imágenes sin etiquetar en la DB.")
        conn.close()
        return

    logger.info(
        "Imágenes a procesar: %d%s",
        len(imagenes),
        " (dry run)" if dry_run else "",
    )

    ok = 0
    fail = 0

    for i, img in enumerate(imagenes, 1):
        logger.info(
            "[%d/%d] %s", i, len(imagenes), img["nombre"]
        )

        if dry_run:
            etiqueta_modo = {
                MODE_COMBINADO: "etiquetaría (keywords + descripción)",
                MODE_KEYWORDS: "extraería keywords de",
                MODE_DESCRIPCION: "describiría",
            }
            logger.info("  [DRY RUN] Se %s: %s", etiqueta_modo.get(modo, "procesaría"), img["nombre"])
            ok += 1
            continue

        resultado = procesar_imagen(
            img["ruta"],
            modelo=modelo,
            usar_proxy=usar_proxy,
            sidecar=sidecar,
            dry_run=False,
            modo=modo,
        )

        if resultado:
            guardar_en_db(conn, img["id"], resultado, modo=modo)
            ok += 1
        else:
            fail += 1

    conn.close()

    if modo == MODE_KEYWORDS:
        resumen = "keywords extraídas"
    elif modo == MODE_DESCRIPCION:
        resumen = "descripciones generadas"
    else:
        resumen = "etiquetadas"

    logger.info(
        "=== RESUMEN: %d %s, %d fallos (de %d) ===",
        ok, resumen, fail, len(imagenes),
    )


def procesar_desde_carpeta(
    carpeta: str,
    modelo: str | None = None,
    sidecar: bool = True,
    usar_proxy: bool = True,
    dry_run: bool = False,
    modo: str = MODE_COMBINADO,
):
    """
    Modo autónomo: etiqueta imágenes de una carpeta y genera sidecars.
    """
    carpeta = Path(carpeta)
    if not carpeta.exists():
        raise FileNotFoundError(f"La carpeta no existe: {carpeta}")

    # Buscar imágenes
    rutas = sorted(
        str(p) for p in carpeta.rglob("*")
        if _es_imagen(str(p)) and "excluir" not in p.parts
    )

    if not rutas:
        logger.info("No se encontraron imágenes en %s", carpeta)
        return

    logger.info(
        "Imágenes encontradas: %d%s",
        len(rutas),
        " (dry run)" if dry_run else "",
    )

    ok = 0
    fail = 0
    ya_etiquetadas = 0

    for i, ruta in enumerate(rutas, 1):
        nombre = Path(ruta).name
        logger.info("[%d/%d] %s", i, len(rutas), nombre)

        # Verificar si ya tiene sidecar válido
        if sidecar and not dry_run:
            existente = _sidecar_existe_valido(ruta)
            if existente is not None:
                logger.info(
                    "  -> Ya tiene sidecar válido (%d tags). Skip.",
                    len(existente["tags"]),
                )
                ya_etiquetadas += 1
                continue

        if dry_run:
            logger.info("  [DRY RUN] Se etiquetaría: %s", nombre)
            ok += 1
            continue

        resultado = procesar_imagen(
            ruta,
            modelo=modelo,
            usar_proxy=usar_proxy,
            sidecar=sidecar,
            dry_run=False,
            modo=modo,
        )

        if resultado:
            ok += 1
        else:
            fail += 1

    if modo == MODE_KEYWORDS:
        resumen_nuevas = "nuevas con keywords"
    elif modo == MODE_DESCRIPCION:
        resumen_nuevas = "nuevas con descripción"
    else:
        resumen_nuevas = "nuevas"

    logger.info(
        "=== RESUMEN: %d %s, %d ya tenían sidecar, %d fallos (de %d) ===",
        ok, resumen_nuevas, ya_etiquetadas, fail, len(rutas),
    )


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════

def listar_modelos():
    """Muestra los modelos instalados en Ollama."""
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


def main():
    parser = argparse.ArgumentParser(
        description="Etiquetar imágenes con IA (modelos de visión Ollama).\n\n"
                    "Dos modos:\n"
                    "  --db ruta    : etiqueta imágenes de la DB (post-ingesta)\n"
                    "  --folder ruta: etiqueta imágenes de una carpeta (genera sidecars)\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Modo
    modo = parser.add_argument_group("Modo de operación")
    modo.add_argument("--db", help="Ruta a la base de datos SQLite (modo post-ingesta)")
    modo.add_argument("--folder", help="Ruta a carpeta con imágenes (modo autónomo)")

    # Opciones generales
    parser.add_argument("--modelo", default=None,
                        help="Modelo de visión Ollama (default: prompts.yaml vision.*). "
                             "Usar --list-models para ver los instalados.")
    parser.add_argument("--no-proxy", action="store_true",
                        help="No usar proxies (deshabilita redimensionado automático)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Solo mostrar qué se haría sin ejecutar")
    parser.add_argument("--list-models", action="store_true",
                        help="Mostrar modelos Ollama instalados y salir")

    # Opciones modo DB
    parser.add_argument("--limit", type=int, default=None,
                        help="Limitar cantidad de imágenes a procesar (solo modo --db)")
    parser.add_argument("--sidecar", action="store_true",
                        help="Generar también sidecar .tags.json (solo modo --db)")

    # Modo de etiquetado
    modo_group = parser.add_argument_group("Modo de etiquetado")
    modo_excl = modo_group.add_mutually_exclusive_group()
    modo_excl.add_argument("--keywords-only", action="store_true",
                           help="Solo extraer keywords (más rápido)")
    modo_excl.add_argument("--description-only", action="store_true",
                           help="Solo generar descripción")

    args = parser.parse_args()

    # --list-models es prioridad
    if args.list_models:
        listar_modelos()
        return

    # Configurar logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Validar que se especificó un modo
    if not args.db and not args.folder:
        parser.error("Debe especificar --db o --folder")

    usar_proxy = not args.no_proxy

    # Determinar modo de etiquetado
    if args.keywords_only:
        modo = MODE_KEYWORDS
    elif args.description_only:
        modo = MODE_DESCRIPCION
    else:
        modo = MODE_COMBINADO

    logger.info("Modo de etiquetado: %s", modo)

    try:
        if args.db:
            procesar_desde_db(
                ruta_db=args.db,
                modelo=args.modelo,
                limite=args.limit,
                sidecar=args.sidecar,
                usar_proxy=usar_proxy,
                dry_run=args.dry_run,
                modo=modo,
            )

        if args.folder:
            # En modo folder, los sidecars se generan siempre (es el propósito del modo)
            procesar_desde_carpeta(
                carpeta=args.folder,
                modelo=args.modelo,
                sidecar=True,
                usar_proxy=usar_proxy,
                dry_run=args.dry_run,
                modo=modo,
            )

    except KeyboardInterrupt:
        logger.info("\nProceso interrumpido por el usuario.")
        sys.exit(1)

    except Exception as e:
        logger.error("Error general: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
