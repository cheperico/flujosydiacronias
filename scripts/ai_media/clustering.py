"""
Módulo de clustering semántico para agrupar imágenes dentro de una tanda temporal.

Estrategias:
  - tags: agrupa por palabras clave extraídas por IA
  - embeddings: agrupa por similitud semántica de descripciones (nomic-embed-text)

Modelo de visión:
  Esta tarea NO necesita descripciones de alta calidad (solo un texto lo
  suficientemente semántico para generar embeddings de agrupación). Por eso
  se usa moondream:latest (~0.8s/img) en vez de minicpm (~3-13s/img), que
  queda reservado para el pipeline de DB. La descripción del clustering NO
  se reutiliza en la DB (no se guarda).
"""

import logging

from scripts.ai_media.image_analysis import _parsear_keywords
from scripts.ai_media.proxy import obtener_proxy

logger = logging.getLogger(__name__)

# Modelo de visión para clustering: moondream es ~15x más rápido que minicpm
# y suficiente para la tarea de agrupar (descripción breve → embedding).
# ⚠️ moondream responde MAL a prompts en español → prompts EN.
MODELO_CLUSTERING_DEFAULT = "moondream:latest"

# Prompts EN (moondream está entrenado en inglés)
PROMPT_CLUSTER_DESC = (
    "Briefly describe what is seen in this image in one sentence "
    "of at most 15 words. Avoid judgments, only describe content."
)

PROMPT_CLUSTER_TAGS = (
    "Reply with ONLY 3 comma-separated keywords describing the main "
    "content of this image. Example: 'sunset, plaza, bicycles'"
)


def agrupar_por_tags(
    grupo: list[str],
    modelo_vision: str = MODELO_CLUSTERING_DEFAULT,
    compartir_min: int = 1,
    usar_proxy: bool = True,
) -> list[list[str]]:
    """
    Agrupa imágenes dentro de un grupo temporal compartiendo al menos N tags.

    1. Para cada imagen, pide al modelo de visión 3 palabras clave.
    2. Agrupa greedy: la primera imagen define el grupo, se agregan las que
       compartan al menos `compartir_min` tags.

    Args:
        grupo: Lista de rutas de imágenes (mismo grupo temporal).
        modelo_vision: Modelo de visión para extraer tags.
                       Default: moondream (rápido, suficiente para agrupar).
        compartir_min: Mínimo de tags compartidos para estar en el mismo grupo.
        usar_proxy: Si True, redimensiona a 800px antes de enviar a la IA
                    (mucho más rápido, menos tokens de visión).

    Returns:
        Lista de sub-grupos.
    """
    from scripts.ai_media.ollama_client import OllamaVision

    if len(grupo) <= 1:
        return [grupo]

    cliente = OllamaVision(modelo=modelo_vision)

    prompt_tags = PROMPT_CLUSTER_TAGS

    tags_por_ruta = {}
    for ruta in grupo:
        try:
            ruta_ia = obtener_proxy(ruta, usar_proxy=usar_proxy)
            respuesta = cliente.analizar_imagen(ruta_ia, prompt=prompt_tags, temperatura=0.1)
            tags = [t.strip().lower() for t in _parsear_keywords(respuesta)][:3]
            tags_por_ruta[ruta] = set(tags)
            logger.debug("Tags de %s: %s", ruta, tags)
        except Exception as e:
            logger.warning("Error extrayendo tags de %s: %s", ruta, e)
            tags_por_ruta[ruta] = set()

    # Agrupamiento greedy: la primera imagen del grupo define el cluster
    sub_grupos = []
    asignadas = set()

    for ruta in grupo:
        if ruta in asignadas:
            continue

        grupo_sim = [ruta]
        asignadas.add(ruta)
        tags_ref = tags_por_ruta.get(ruta, set())

        for otra in grupo:
            if otra in asignadas:
                continue
            tags_otra = tags_por_ruta.get(otra, set())
            if tags_ref and tags_otra and len(tags_ref & tags_otra) >= compartir_min:
                grupo_sim.append(otra)
                asignadas.add(otra)

        sub_grupos.append(grupo_sim)

    # Estadísticas
    multi = sum(1 for g in sub_grupos if len(g) > 1)
    logger.info(
        "  Tags: %d grupos (%d multi-imagen) en grupo temporal de %d imágenes",
        len(sub_grupos), multi, len(grupo)
    )

    return sub_grupos


def agrupar_por_embeddings(
    grupo: list[str],
    modelo_vision: str = MODELO_CLUSTERING_DEFAULT,
    modelo_embed: str = "nomic-embed-text",
    umbral_similitud: float = 0.7,
    usar_proxy: bool = True,
) -> list[list[str]]:
    """
    Agrupa imágenes por similitud semántica usando embeddings de descripciones.

    1. Para cada imagen, obtiene una breve descripción con el modelo de visión.
    2. Convierte cada descripción a embedding (nomic-embed-text).
    3. Agrupa por cosine similarity (umbral configurable).

    Args:
        grupo: Lista de rutas de imágenes.
        modelo_vision: Modelo de visión para describir.
                       Default: moondream (rápido, suficiente para agrupar).
        modelo_embed: Modelo de embeddings (nomic-embed-text recomendado).
        umbral_similitud: Umbral de cosine similarity (0-1) para considerar mismo grupo.
        usar_proxy: Si True, redimensiona a 800px antes de enviar a la IA.

    Returns:
        Lista de sub-grupos.
    """
    from scripts.ai_media.ollama_client import OllamaVision
    try:
        from ollama import embeddings
    except ImportError:
        logger.error("Módulo 'ollama' no instalado. Instalar con: pip install ollama")
        return [grupo]
    try:
        import numpy as np
    except ImportError:
        logger.error("Módulo 'numpy' no instalado. Instalar con: pip install numpy")
        return [grupo]

    if len(grupo) <= 1:
        return [grupo]

    cliente = OllamaVision(modelo=modelo_vision)

    prompt_desc = PROMPT_CLUSTER_DESC

    # 1. Obtener descripciones
    desc_por_ruta = {}
    for ruta in grupo:
        try:
            ruta_ia = obtener_proxy(ruta, usar_proxy=usar_proxy)
            desc = cliente.analizar_imagen(ruta_ia, prompt=prompt_desc, temperatura=0.1)
            desc_por_ruta[ruta] = desc.strip()
            logger.debug("Descripción de %s: %s", ruta, desc[:50])
        except Exception as e:
            logger.warning("Error describiendo %s: %s", ruta, e)
            desc_por_ruta[ruta] = ""

    # 2. Generar embeddings
    emb_por_ruta = {}
    for ruta, desc in desc_por_ruta.items():
        if desc:
            try:
                resp = embeddings(model=modelo_embed, prompt=desc)
                # Compat: ollama<0.3 devuelve dict, >=0.3 devuelve objeto
                if isinstance(resp, dict):
                    vec = resp.get("embedding")
                else:
                    vec = getattr(resp, "embedding", None)
                    if vec is None and isinstance(resp, dict):
                        vec = resp.get("embedding")
                if vec is None:
                    raise ValueError("Respuesta embeddings sin campo 'embedding'")
                emb_por_ruta[ruta] = np.array(vec, dtype=np.float32)
            except Exception as e:
                logger.warning("Error generando embedding para %s: %s", ruta, e)

    if not emb_por_ruta:
        logger.warning("  No se pudieron generar embeddings, se conserva el grupo original")
        return [grupo]

    # 3. Agrupar por cosine similarity
    sub_grupos = []
    asignadas = set()

    for ruta in grupo:
        if ruta in asignadas:
            continue

        grupo_sim = [ruta]
        asignadas.add(ruta)

        if ruta not in emb_por_ruta:
            sub_grupos.append(grupo_sim)
            continue

        emb_ref = emb_por_ruta[ruta]

        for otra in grupo:
            if otra in asignadas or otra not in emb_por_ruta:
                continue
            emb_otra = emb_por_ruta[otra]
            denom = float(np.linalg.norm(emb_ref) * np.linalg.norm(emb_otra))
            if denom == 0:
                continue
            sim = float(np.dot(emb_ref, emb_otra) / denom)
            if sim >= umbral_similitud:
                grupo_sim.append(otra)
                asignadas.add(otra)

        sub_grupos.append(grupo_sim)

    # Estadísticas
    multi = sum(1 for g in sub_grupos if len(g) > 1)
    logger.info(
        "  Embeddings: %d grupos (%d multi-imagen) en grupo temporal de %d imágenes",
        len(sub_grupos), multi, len(grupo)
    )

    return sub_grupos
