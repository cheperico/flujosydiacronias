"""
Análisis de imágenes con modelos de visión de Ollama.

Funcionalidades:
  - Extraer palabras clave (keywords) de una imagen (5-7 palabras)
  - Describir una imagen en texto
  - Clasificar imágenes por contenido
  - Procesamiento por lote

Uso básico:
    from scripts.ai_media.image_analysis import extraer_keywords, describir_imagen

    keywords = extraer_keywords("foto.jpg")
    print(keywords)  # ["playa", "atardecer", "palmeras", "arena", "mar", "cielo", "nubes"]

    descripcion = describir_imagen("foto.jpg")
    print(descripcion)

Línea de comandos:
    python -m scripts.ai_media.image_analysis foto.jpg
    python -m scripts.ai_media.image_analysis foto1.jpg foto2.jpg --modelo moondream:latest
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

from scripts.ai_media.ollama_client import OllamaVision, asegurar_ollama
from scripts.ai_media.proxy import obtener_proxy

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
#  LISTA CONTROLADA DE GÉNEROS FOTOGRÁFICOS
# ──────────────────────────────────────────────
# ELIMINADA (Ago 2026): el género fotográfico quedó descartado porque no era
# fácil forzarlo en el prompt. Las keywords son libres y NO se inserta ningún
# género comodín ("otras") en post-procesamiento. Ver refinar_keywords.py.

# ──────────────────────────────────────────────
#  MODELO POR DEFECTO
# ──────────────────────────────────────────────
# Se puede cambiar según la máquina:
#   - minicpm-v4.6:latest → GANADOR de la comparativa (Ago 2026). Grilla fija
#                           ~340 tokens (la resolución NO infla el contexto),
#                           keywords conceptuales + descripciones largas.
#                           ~13-19s por imagen a 800px. 1.6 GB.
#   - qwen2.5vl:3b        → balance calidad/velocidad (3.2 GB). ~25-37s a 800px.
#   - moondream:latest    → rápido, liviano (1.7 GB) pero NO da listas
#                           estructuradas de keywords ni descripciones largas.
# ⚠️ PROMPTS EN INGLÉS (modelos vision multilingües responden mejor en su
#    idioma de entrenamiento). La traducción a español se hace después sobre
#    la DB (scripts/ai_media/traducir_metadata.py).
MODELO_VISION_DEFAULT = "minicpm-v4.6:latest"

# ──────────────────────────────────────────────
#  PROMPTS (en inglés, mínimos — validados Ago 2026)
# ──────────────────────────────────────────────
# minicpm responde mejor a prompts simples. Las keywords son libres (EN):
# NO se pide ni se valida género fotográfico (descartado Ago 2026).
# Prompts de descripción reescritos (2026-08-14) para evitar ecos de
# meta-intro: sin "Give me", con "start directly with the scene, without
# any preamble" (limpiar_meta_intro sigue como red de seguridad).

PROMPT_KEYWORDS = "Give me exactly 5 keywords for this image, comma-separated."

PROMPT_DESCRIBIR = (
    "Describe what you see in this image in detail. "
    "Start directly with the scene, without any preamble."
)

PROMPT_COMBINADO = (
    "Respond with ONLY JSON about THIS image with two fields:\n"
    '1. "keywords": exactly 5 keywords comma-separated, describing ONLY the '
    "content of THIS image.\n"
    '2. "description": a long description of THIS image, written directly '
    "without preamble or meta-commentary.\n"
    'Exact format: {"keywords": ["sofa", "bookshelf", "lamp", "carpet", "window"], '
    '"description": "Long description text here."}\n'
    "The JSON format above is just an example; its keywords are NOT part of "
    "this image. List keywords only from what YOU see in THIS image.\n"
    "Nothing else but the JSON."
)

PROMPT_CLASIFICAR = (
    "Clasificá esta imagen en una de estas categorías: "
    "naturaleza, urbano, retrato, abstracto, documento, evento, paisaje, arquitectura, "
    "objeto, arte, comida, tecnología, deporte, noche, macro, otras. "
"Respondé solo con el nombre de la categoría."
)

# ── Prefijos de meta-intro regurgitados por el modelo (EN) ────────────────
# minicpm a veces abre la descripción con una meta-introducción del prompt
# ("To describe the image, ...", "Here's a long description of the image: ...")
# en vez de describir directamente. Se recortan al inicio del texto.
PREFIJOS_META_EN: tuple[str, ...] = (
    # Familia A — narración del proceso
    "to describe the image in detail, we analyze its key components:",
    "to describe the image in detail, we analyze its main components:",
    "to describe the image in detail, we first observe the main visual elements:",
    "to describe the image in detail, we observe the main visual elements:",
    "to describe the image, we first observe the main visual elements:",
    "to describe the image, we observe the main visual elements:",
    "to describe the image, we first observe",
    "to describe the image, we observe",
    "to describe the image, we",
    "to describe the image in detail, we",
    "to describe the image,",
    "to describe the image in detail,",
    "to describe the image:",
    "to describe the image in detail:",
    # Familia B — "Here's a ... description of the image:"
    "here's a long description of the image:",
    "here is a long description of the image:",
    "here's a detailed description of the image:",
    "here is a detailed description of the image:",
    "here's a description of the image:",
    "here is a description of the image:",
    "here's my description of the image:",
    "here is my description of the image:",
    # Familia B2 — sin "of the image" (variantes reales observadas)
    "here's a long description:",
    "here is a long description:",
    "here's a detailed description:",
    "here is a detailed description:",
    "here's a description:",
    "here is a description:",
    # Familia C — "This is / My / The following is ..."
    "this is a description of the image:",
    "this is my description of the image:",
    "my description of the image:",
    "the following is a description of the image:",
    "the description of the image:",
    # Familia D — primera persona meta
    "i will describe the image:",
    "i will describe this image:",
    "let me describe this image:",
    "let me describe the image in detail:",
    "let me describe the image:",
    # Familia F — headers de formato
    "description of the image:",
    "description:",
    # Familia G — "Based on the image provided, ..." (variantes reales)
    "based on the image provided, here's a detailed description:",
    "based on the image provided, here is a detailed description:",
    "based on the image provided, here's a long description:",
    "based on the image provided, here is a long description:",
    "based on the image provided, here's a description:",
    "based on the image provided, here is a description:",
    "based on the provided image, here's a detailed description:",
    "based on the provided image, here is a detailed description:",
    "based on the provided image, here's a long description:",
    "based on the provided image, here is a long description:",
    # Familia H — caso puntual observado
    "certainly! let's break down the image description step by step to ensure it captures all the vivid elements.",
    # Familia I — variantes finales observadas (ronda post-cleanup)
    "based on the image provided, i can describe the following:",
    "let's break down the image description:",
    "looking at the image,",
)

# Continuaciones meta que siguen a un prefijo (solo se recortan si hubo match
# primario; nunca standalone para no tocar "We observe a cyclist..." legítimo).
_CONTINUACIONES_META_EN: tuple[str, ...] = (
    "we first observe", "we observe", "we analyze", "we notice", "we see",
    "i first observe", "i observe", "i analyze", "first, we observe", "first, i observe",
)
_ACK_EN: tuple[str, ...] = ("sure,", "of course,", "certainly,", "here you go:", "here you are:")
MAX_PASADAS_META = 3


def limpiar_meta_intro(texto: str) -> str:
    """Recorta meta-intros regurgitados al inicio de una descripción EN.

    Si no hay nada que recortar, o si recortar dejaría vacío, devuelve el
    texto original sin cambios (nunca se pierde contenido).
    """
    t = texto.strip().strip('"').strip()
    if not t:
        return texto
    bajo = t.lower().replace("’", "'")
    # Paso 0: acknowledgments ("Sure, here is...")
    for ack in _ACK_EN:
        if bajo.startswith(ack):
            t = t[len(ack):].lstrip(" \t\n\r:;,.-–—\"'’").strip()
            bajo = t.lower().replace("’", "'")
            break
    cambiado = False
    for _ in range(MAX_PASADAS_META):
        match = next((p for p in PREFIJOS_META_EN if bajo.startswith(p)), None)
        if not match:
            break
        t = t[len(match):].lstrip(" \t\n\r:;,.-–—\"'’").strip()
        bajo = t.lower().replace("’", "'")
        # Paso secundario: continuación meta ("we observe", "we analyze", ...)
        for cont in _CONTINUACIONES_META_EN:
            if bajo.startswith(cont):
                t = t[len(cont):].lstrip(" \t\n\r:;,.-–—\"'’").strip()
                bajo = t.lower().replace("’", "'")
                break
        cambiado = True
    if not cambiado:
        return texto
    return t if t else texto


def extraer_keywords(
    ruta_imagen: str,
    modelo: str = MODELO_VISION_DEFAULT,
    temperatura: float = 0.2,
    usar_proxy: bool = True,
) -> list[str]:
    """
    Analiza una imagen y devuelve 5-7 palabras clave en español.

    Args:
        ruta_imagen: Ruta al archivo de imagen.
        modelo: Modelo de visión a usar. Por defecto MODELO_VISION_DEFAULT.
        temperatura: Control de creatividad. Bajo para keywords predecibles.
        usar_proxy: Si True, usa proxy redimensionado a 800px para acelerar.

    Returns:
        Lista de palabras clave (strings).

    Raises:
        FileNotFoundError: Si la imagen no existe.
        ValueError: Si no se pudieron extraer keywords.
    """
    ruta_proxy = obtener_proxy(ruta_imagen, usar_proxy=usar_proxy)
    cliente = OllamaVision(modelo=modelo)

    respuesta = cliente.analizar_imagen(
        ruta_proxy,
        prompt=PROMPT_KEYWORDS,
        temperatura=temperatura,
    )

    # Parsear la respuesta: puede venir como "cosa1, cosa2, ..." o "1. cosa1 2. cosa2..."
    keywords = _parsear_keywords(respuesta)

    if not keywords:
        logger.warning(
            "No se pudieron extraer keywords de: %s. Respuesta: %s",
            ruta_imagen, respuesta
        )
        # Fallback: devolver la respuesta completa como única keyword
        return [respuesta.strip()]

    # NOTA (Ago 2026): no se valida género fotográfico — descartado. Las
    # keywords son libres (EN) y no se fuerza ningún comodín.

    logger.info("Keywords extraídas de %s: %s", Path(ruta_imagen).name, keywords)
    return keywords


def extraer_keywords_batch(
    rutas_imagenes: list[str],
    modelo: str = MODELO_VISION_DEFAULT,
    temperatura: float = 0.2,
    usar_proxy: bool = True,
) -> list[dict]:
    """
    Analiza múltiples imágenes y extrae keywords de cada una.

    Args:
        rutas_imagenes: Lista de rutas a imágenes.
        modelo: Modelo de visión.
        temperatura: Control de creatividad.
        usar_proxy: Si True, usa proxies redimensionados.

    Returns:
        Lista de dicts con {"ruta", "keywords", "error"}.
    """
    # Aplicar proxies y mantener mapeo ruta_original -> ruta_proxy
    if usar_proxy:
        rutas_proxy = [(r, obtener_proxy(r)) for r in rutas_imagenes]
    else:
        rutas_proxy = [(r, r) for r in rutas_imagenes]

    rutas_proxy_solo = [p for _, p in rutas_proxy]

    cliente = OllamaVision(modelo=modelo)
    resultados_vision = cliente.analizar_imagenes(
        rutas_proxy_solo,
        prompt=PROMPT_KEYWORDS,
        temperatura=temperatura,
    )

    resultados = []
    for (ruta_orig, _), item in zip(rutas_proxy, resultados_vision):
        if item["error"]:
            resultados.append({
                "ruta": ruta_orig,
                "keywords": [],
                "error": item["error"],
            })
        else:
            keywords = _parsear_keywords(item["respuesta"])
            resultados.append({
                "ruta": ruta_orig,
                "keywords": keywords,
                "error": None,
            })

    return resultados


def describir_imagen(
    ruta_imagen: str,
    modelo: str = MODELO_VISION_DEFAULT,
    temperatura: float = 0.3,
    usar_proxy: bool = True,
) -> str:
    """
    Genera una descripción en lenguaje natural de una imagen.

    Args:
        ruta_imagen: Ruta al archivo de imagen.
        modelo: Modelo de visión (por defecto moondream).
        temperatura: Control de creatividad.
        usar_proxy: Si True, usa proxy redimensionado.

    Returns:
        Descripción textual de la imagen.
    """
    ruta_proxy = obtener_proxy(ruta_imagen, usar_proxy=usar_proxy)
    cliente = OllamaVision(modelo=modelo)
    return limpiar_meta_intro(
        cliente.analizar_imagen(ruta_proxy, PROMPT_DESCRIBIR, temperatura)
    )


def analizar_imagen_completo(
    ruta_imagen: str,
    modelo: str = MODELO_VISION_DEFAULT,
    temperatura: float = 0.2,
    usar_proxy: bool = True,
) -> dict:
    """
    Analiza una imagen con UNA sola llamada a la IA y devuelve
    tanto keywords como descripción.

    Usa PROMPT_COMBINADO que pide un JSON con ambos campos.

    Args:
        ruta_imagen: Ruta al archivo de imagen.
        modelo: Modelo de visión (por defecto moondream).
        temperatura: Control de creatividad.
        usar_proxy: Si True, usa proxy redimensionado.

    Returns:
        Dict con:
          { "keywords": [str, ...], "description": str }

    Raises:
        FileNotFoundError: Si la imagen no existe.
        ValueError: Si no se pudo parsear el JSON de respuesta.
    """
    ruta_proxy = obtener_proxy(ruta_imagen, usar_proxy=usar_proxy)
    cliente = OllamaVision(modelo=modelo)

    respuesta = cliente.analizar_imagen(
        ruta_proxy,
        prompt=PROMPT_COMBINADO,
        temperatura=temperatura,
    )

    resultado = _parsear_combinado(respuesta)

    if resultado is None:
        logger.warning(
            "No se pudo parsear respuesta combinada de: %s. Respuesta: %s",
            ruta_imagen, respuesta
        )
        # Fallback: tratar de parsear keywords y descripción por separado
        keywords = _parsear_keywords(respuesta)
        return {
            "keywords": keywords,
            "description": _descripcion_utilizable(respuesta),
        }

    logger.info(
        "Análisis completo de %s: %d keywords, %d chars descripción",
        Path(ruta_imagen).name,
        len(resultado.get("keywords", [])),
        len(resultado.get("description", ""))
    )
    return resultado


def analizar_imagen_completo_batch(
    rutas_imagenes: list[str],
    modelo: str = MODELO_VISION_DEFAULT,
    temperatura: float = 0.2,
    usar_proxy: bool = True,
) -> list[dict]:
    """
    Analiza múltiples imágenes con UNA sola llamada a la IA cada una
    y devuelve keywords y descripción.

    Args:
        rutas_imagenes: Lista de rutas a imágenes.
        modelo: Modelo de visión.
        temperatura: Control de creatividad.
        usar_proxy: Si True, usa proxies redimensionados.

    Returns:
        Lista de dicts con {"ruta", "keywords", "description", "error"}.
    """
    if usar_proxy:
        rutas_proxy = [(r, obtener_proxy(r)) for r in rutas_imagenes]
    else:
        rutas_proxy = [(r, r) for r in rutas_imagenes]

    rutas_proxy_solo = [p for _, p in rutas_proxy]

    cliente = OllamaVision(modelo=modelo)
    resultados_vision = cliente.analizar_imagenes(
        rutas_proxy_solo,
        prompt=PROMPT_COMBINADO,
        temperatura=temperatura,
    )

    resultados = []
    for (ruta_orig, _), item in zip(rutas_proxy, resultados_vision):
        if item["error"]:
            resultados.append({
                "ruta": ruta_orig,
                "keywords": [],
                "description": "",
                "error": item["error"],
            })
        else:
            parsed = _parsear_combinado(item["respuesta"])
            if parsed is None:
                keywords = _parsear_keywords(item["respuesta"])
                resultados.append({
                    "ruta": ruta_orig,
                    "keywords": keywords,
                    "description": _descripcion_utilizable(item["respuesta"]),
                    "error": None,
                })
            else:
                keywords = parsed.get("keywords", [])
                resultados.append({
                    "ruta": ruta_orig,
                    "keywords": keywords,
                    "description": parsed.get("description", ""),
                    "error": None,
                })

    return resultados


def _descripcion_utilizable(respuesta: str) -> str:
    """
    Extrae una descripción utilizable de una respuesta cruda del modelo.

    Cuando el parseo JSON combinado falla, el fallback guardaba la respuesta
    completa (incluido el JSON roto con 'keywords' y 'description'). Este helper
    filtra:
      - JSON crudo / código: si hay 'keywords' y 'description' como claves
      - Texto muy corto o basura (< 5 chars)
      - Restos del prompt regurgitado por el modelo

    Devuelve "" si no hay nada utilizable (mejor vacío que contaminar la DB).
    """
    texto = respuesta.strip()
    if not texto:
        return ""

    # JSON crudo: detectar claves de prompt combinado
    bajo = texto.lower()
    if ("keywords" in bajo and "description" in bajo) or texto.startswith(("{", "[")):
        # Intentar sacar SOLO el campo description si es JSON parseable
        datos = _reparar_json(texto)
        if datos and isinstance(datos.get("description"), str):
            desc = limpiar_meta_intro(datos["description"])
            if len(desc) >= 5:
                return desc
        return ""

    # Texto muy corto (basura tipo "!!!" o "abc")
    if len(texto) < 5:
        return ""

    # Restos del prompt: frases típicas del ejemplo que el modelo regurgita
    MARCAS_PROMPT = (
        "un perro...", "dos personas...", "un paisaje...", "una foto presenta",
        "la imagen se ve", "en esta imagen se ve", "la foto presenta",
        "el género fotográfico", "elige una",
    )
    marcas_encontradas = [m for m in MARCAS_PROMPT if m in bajo]
    if marcas_encontradas:
        # Si hay VARIAS marcas, es la lista de ejemplos del prompt regurgitada
        # (basura completa). Si hay UNA sola, recortarla y conservar el resto.
        if len(marcas_encontradas) > 1:
            return ""
        marca = marcas_encontradas[0]
        idx = bajo.find(marca)
        resto = texto[idx + len(marca):].strip(" ,.:;'\"").strip()
        if len(resto) >= 5:
            return limpiar_meta_intro(resto)
        return ""

    return limpiar_meta_intro(texto)


def _reparar_json(texto: str) -> Optional[dict]:
    """
    Intenta reparar JSON malformado que devuelven los modelos.

    Problema común: el modelo corta la respuesta o le falta cerrar un bracket.
    Caso típico detectado: '{"keywords": ["a", "b"}, "description": "..."}'
    (el array de keywords se cierra con '}' en vez de ']').

    Estrategia (en orden):
      1. Intentar json.loads directo (y con comillas simples → dobles).
      2. Reparación quirúrgica: si falta ']' del array keywords antes de
         "description", insertar el cierre en el lugar correcto.
      3. Recortar basura alrededor del primer '{' y último '}'.

    Returns:
        Dict parseado, o None si no se pudo reparar.
    """
    texto = texto.strip()

    # Recortar basura alrededor del JSON: desde el primer { hasta el último }
    ini = texto.find("{")
    fin = texto.rfind("}")
    if ini != -1 and fin != -1 and fin > ini:
        texto = texto[ini:fin + 1]

    # Limpiar trailing commas: ["playa", "mar",] → ["playa", "mar"]
    # (los LLM dejan comas sobrantes antes de cerrar ] o })
    texto = re.sub(r",\s*([}\]])", r"\1", texto)

    intentos = [texto, texto.replace("'", '"')]
    # Probar primero parseos directos
    for intento in intentos:
        try:
            datos = json.loads(intento)
            if isinstance(datos, dict):
                return datos
        except (json.JSONDecodeError, TypeError):
            pass

    # Reparación quirúrgica 1: array de keywords cerrado con '}' en vez de ']'.
    # Patrón: ..."última_kw"}, "description": ...  →  ..."última_kw"], "description": ...
    for base in intentos:
        if base.count("[") > base.count("]"):
            reparado = re.sub(r'"},\s*("description"\s*:)', '"], \\1', base, count=1)
            if reparado != base:
                try:
                    datos = json.loads(reparado)
                    if isinstance(datos, dict):
                        return datos
                except (json.JSONDecodeError, TypeError):
                    pass

    # Reparación quirúrgica 2: cerrar brackets faltantes al final del string.
    # Solo funciona si el error es de truncamiento al final (no en el medio).
    for base in intentos:
        for _ in range(8):  # hasta 8 intentos de cierre
            abren = base.count("[") + base.count("{")
            cierran = base.count("]") + base.count("}")
            if abren == cierran:
                break
            # Agregar el bracket que falta (los arrays suelen faltar antes que objetos)
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


def _parsear_combinado(respuesta: str) -> Optional[dict]:
    """
    Parsea la respuesta JSON del prompt combinado.

    Espera: {"keywords": [...], "description": "..."}
    Puede venir dentro de bloques ```json ... ```.

    Returns:
        Dict con "keywords" y "description", o None si falla.
    """
    texto = respuesta.strip()

    # Limpiar bloques de código Markdown
    texto = re.sub(r"^```(?:json)?\s*\n?", "", texto)
    texto = re.sub(r"\n?```\s*$", "", texto)
    texto = texto.strip()

    # Intentar parsear como JSON (con reparación de JSON truncado)
    datos = _reparar_json(texto)
    if datos is not None:
        keywords = datos.get("keywords", [])
        description = datos.get("description", "")
        # Asegurar tipos
        if isinstance(keywords, str):
            # Vino como string "paisaje, montaña" en vez de lista
            keywords = _parsear_keywords(keywords)
        elif not isinstance(keywords, list):
            keywords = [str(keywords)]
        if not isinstance(description, str):
            description = str(description)
        description = limpiar_meta_intro(description)
        return {"keywords": keywords, "description": description}

    # Si no se pudo parsear, buscar keywords con _parsear_keywords y descripción en el resto
    lines = texto.split("\n")
    keywords_line = None
    for line in lines:
        low = line.strip().lower()
        if "keywords" in low or "palabras" in low or "keyword" in low:
            keywords_line = line
            break

    if keywords_line:
        # Intentar extraer lista
        kw = _parsear_keywords(keywords_line)
        if kw:
            desc = texto.strip()
            return {"keywords": kw, "description": limpiar_meta_intro(desc)}

    return None


def clasificar_imagen(
    ruta_imagen: str,
    modelo: str = MODELO_VISION_DEFAULT,
    usar_proxy: bool = True,
) -> str:
    """
    Clasifica una imagen en una categoría predefinida.

    Args:
        ruta_imagen: Ruta al archivo de imagen.
        modelo: Modelo de visión.
        usar_proxy: Si True, usa proxy redimensionado.

    Returns:
        Nombre de la categoría.
    """
    ruta_proxy = obtener_proxy(ruta_imagen, usar_proxy=usar_proxy)
    cliente = OllamaVision(modelo=modelo)
    return cliente.analizar_imagen(ruta_proxy, PROMPT_CLASIFICAR, temperatura=0.1)


def _parsear_keywords(respuesta: str) -> list[str]:
    """
    Parsea la respuesta del modelo y extrae keywords como lista.

    Maneja formatos:
      - "playa, atardecer, palmeras"
      - "'playa', 'atardecer', 'palmeras'" (con/quotes individuales)
      - "1. playa 2. atardecer 3. palmeras"
      - "- playa\\n- atardecer\\n- palmeras"
      - "['playa', 'atardecer', 'palmeras']"
      - "```json\\n[...]\\n```" (Markdown code block)
    """
    # Limpiar
    texto = respuesta.strip().strip("'\"")

    # Limpiar bloques de código Markdown (```json ... ```, ``` ... ```)
    texto = re.sub(r"^```(?:json|python)?\s*\n?", "", texto)
    texto = re.sub(r"\n?```\s*$", "", texto)
    texto = texto.strip()

    # Detectar si viene como JSON objeto: {"keywords": ["a", "b"]}
    # (qwen2.5vl a veces responde así aunque el prompt pida lista plana)
    if texto.startswith("{") and texto.endswith("}"):
        try:
            datos = json.loads(texto)
            if isinstance(datos, dict) and "keywords" in datos:
                kw = datos["keywords"]
                if isinstance(kw, list):
                    return [str(k) for k in kw]
                if isinstance(kw, str):
                    return _parsear_keywords(kw)
        except (json.JSONDecodeError, TypeError):
            pass

    # Detectar si viene como lista JSON (con comillas dobles o simples)
    if texto.startswith("[") and texto.endswith("]"):
        try:
            return json.loads(texto)
        except json.JSONDecodeError:
            # Intentar con comillas dobles (a veces viene con simples)
            try:
                texto_json = texto.replace("'", '"')
                return json.loads(texto_json)
            except json.JSONDecodeError:
                pass

    # Detectar formato numerado: "1. cosa 2. cosa"
    if re.match(r"^\d+[\.\)]", texto):
        items = re.findall(r"\d+[\.\)]\s*([^\d,;]+)", texto)
        if items:
            return [_limpiar_kw(i) for i in items if i.strip()]

    # Detectar formato viñetas: "- cosa" o "* cosa"
    if re.match(r"^[\-\*]", texto):
        items = re.findall(r"[\-\*]\s*([^\n]+)", texto)
        if items:
            return [_limpiar_kw(i) for i in items if i.strip()]

    # Separar por " - " (a veces el modelo usa guiones medios como separador)
    if " - " in texto:
        items = [_limpiar_kw(i) for i in texto.split(" - ") if i.strip()]
        items = [i for i in items if len(i) < 80 and len(i) > 1]
        if len(items) > 1:
            return items

    # Separar por comas (puede venir con quotes individuales: 'cosa', 'otra')
    if "," in texto:
        items = [i.strip().strip("'\"").rstrip(".,;") for i in texto.split(",") if i.strip()]
        items = [_limpiar_kw(i) for i in items if i.strip()]
        items = [i for i in items if len(i) < 80]
        if items:
            return items

    # Separar por saltos de línea
    items = [_limpiar_kw(i) for i in texto.split("\n") if i.strip()]
    items = [i for i in items if len(i) < 80]
    if len(items) > 1:
        return items

    # Si solo queda un item largo, no es keyword - devolver vacío o el texto
    if len(items) == 1 and len(items[0]) > 60:
        return []
    if items:
        return items

    # Si todo falla, tratar como texto único
    return [_limpiar_kw(texto)]


def _limpiar_kw(palabra: str) -> str:
    """Limpia una keyword individual de caracteres no deseados."""
    return palabra.strip().strip("'\"").rstrip(".,;!?¿¡").strip()


# ---- CLI ----
if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Analizar imágenes con modelos de visión Ollama"
    )
    parser.add_argument("imagenes", nargs="+", help="Rutas a las imágenes")
    # Opciones generales
    parser.add_argument("--modelo", default=MODELO_VISION_DEFAULT,
                        help=f"Modelo de visión Ollama. (default: {MODELO_VISION_DEFAULT})")
    parser.add_argument("--list-models", action="store_true",
                        help="Mostrar modelos Ollama instalados y salir")
    parser.add_argument("--action", default="keywords",
                        choices=["keywords", "describir", "clasificar", "combinado"],
                        help="Acción a realizar. 'combinado' hace keywords + descripción en una sola llamada")
    parser.add_argument("--json", help="Exportar resultados a JSON")

    args = parser.parse_args()

    if args.list_models:
        if not asegurar_ollama():
            print("  ⚠️  Ollama no está disponible. No se pueden listar modelos.")
            sys.exit(1)
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
            print()
        except Exception as e:
            print(f"Error al conectar con Ollama: {e}")
        sys.exit(0)

    for ruta in args.imagenes:
        if not Path(ruta).exists():
            print(f"ERROR: No existe: {ruta}")
            continue

        try:
            if args.action == "keywords":
                keywords = extraer_keywords(ruta, modelo=args.modelo)
                print(f"\n{ruta}")
                print(f"  Keywords: {', '.join(keywords)}")

            elif args.action == "describir":
                desc = describir_imagen(ruta, modelo=args.modelo)
                print(f"\n{ruta}")
                print(f"  Descripción: {desc}")

            elif args.action == "clasificar":
                cat = clasificar_imagen(ruta, modelo=args.modelo)
                print(f"\n{ruta}")
                print(f"  Categoría: {cat}")

            elif args.action == "combinado":
                resultado = analizar_imagen_completo(ruta, modelo=args.modelo)
                print(f"\n{ruta}")
                print(f"  Keywords: {', '.join(resultado['keywords'])}")
                print(f"  Descripción: {resultado['description']}")

        except Exception as e:
            print(f"ERROR en {ruta}: {e}")
