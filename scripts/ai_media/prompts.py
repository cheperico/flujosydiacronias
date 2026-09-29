#!/usr/bin/env python3
"""
prompts.py — Loader central de prompts de IA.

Los prompts editables viven en `prompts.yaml` (mismo directorio). Cada entrada
tiene 3 campos vivos: `modelo`, `temperatura` y `texto`. Este módulo los lee
con cache y ofrece fallback a los defaults embebidos (DEFAULTS) si el YAML
falta, una clave está rota o `pyyaml` no está instalado: el pipeline nunca
se rompe por editar el YAML.

Precedencia: argumento explícito del llamador / CLI > YAML > DEFAULTS.

Sustitución de placeholders ({tema}, {kw}, {desc}) con .replace() — nunca
.format(), porque varios prompts contienen llaves JSON literales.
"""

import logging
import os
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

PROMPTS_YAML = Path(__file__).resolve().parent / "prompts.yaml"

# Placeholders conocidos por clave (solo estos se sustituyen).
PLACEHOLDERS_POR_CLAVE: dict[str, tuple[str, ...]] = {
    "seleccion.tema": ("tema",),
    "traduccion.ambos": ("kw", "desc"),
    "traduccion.keywords": ("kw",),
    "traduccion.descripcion": ("desc",),
}

# ──────────────────────────────────────────────
#  DEFAULTS embebidos (backup si el YAML falta o falla).
#  Copias exactas de los prompts originales del código.
# ──────────────────────────────────────────────
DEFAULTS: dict[str, dict[str, Any]] = {
    "vision.combinado": {
        "modelo": "minicpm-v4.6:latest",
        "temperatura": 0.2,
        "texto": (
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
        ),
    },
    "vision.keywords_solo": {
        "modelo": "minicpm-v4.6:latest",
        "temperatura": 0.2,
        "texto": "Give me exactly 5 keywords for this image, comma-separated.",
    },
    "vision.describir_solo": {
        "modelo": "minicpm-v4.6:latest",
        "temperatura": 0.3,
        "texto": (
            "Describe what you see in this image in detail. "
            "Start directly with the scene, without any preamble."
        ),
    },
    "vision.clasificar": {
        "modelo": "minicpm-v4.6:latest",
        "temperatura": 0.1,
        "texto": (
            "Clasificá esta imagen en una de estas categorías: "
            "naturaleza, urbano, retrato, abstracto, documento, evento, paisaje, arquitectura, "
            "objeto, arte, comida, tecnología, deporte, noche, macro, otras. "
            "Respondé solo con el nombre de la categoría."
        ),
    },
    "sentido.transcripcion": {
        "modelo": "gemma3:latest",
        "temperatura": 0.2,
        "texto": (
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
        ),
    },
    "sentido.texto": {
        "modelo": "gemma3:latest",
        "temperatura": 0.2,
        "texto": (
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
        ),
    },
    "seleccion.calidad": {
        "modelo": "moondream:latest",
        "temperatura": 0.2,
        "texto": (
            "Evaluate this image's visual quality considering sharpness, "
            "composition, lighting, color and interesting content. "
            "Reply ONLY with a number 1-10 (10 = excellent) and a brief reason "
            "of 10-15 words. Format: '8. Sharp, good composition, vibrant colors.'"
        ),
    },
    "seleccion.tema": {
        "modelo": "moondream:latest",
        "temperatura": 0.2,
        "texto": (
            "Does this image match '{tema}'? Reply ONLY with a 1-10 score "
            "(10 = perfect match) and a brief reason. "
            "Format: '8. Matches: natural landscape with mountains and vegetation.'"
        ),
    },
    "clustering.tags": {
        "modelo": "moondream:latest",
        "temperatura": 0.1,
        "texto": (
            "Reply with ONLY 3 comma-separated keywords describing the main "
            "content of this image. Example: 'sunset, plaza, bicycles'"
        ),
    },
    "clustering.desc": {
        "modelo": "moondream:latest",
        "temperatura": 0.1,
        "texto": (
            "Briefly describe what is seen in this image in one sentence "
            "of at most 15 words. Avoid judgments, only describe content."
        ),
    },
    "traduccion.ambos": {
        "modelo": "translategemma",
        "temperatura": 0.1,
        "texto": (
            "Traducí al ESPAÑOL rioplatense (Argentina) los siguientes datos de una imagen.\n"
            "Reglas:\n"
            "1. Keywords: SUSTANTIVOS, en el mismo orden, separadas por comas.\n"
            "2. NO dejes palabras en inglés, traducí TODAS.\n"
            "3. NO es portugués: en español se dice 'persona', 'objeto', 'color', 'acción'.\n"
            "4. Descripción: traducción natural y completa.\n"
            'Respondé SOLO con JSON: {"keywords": "palabras en español separadas por comas", '
            '"description": "descripción en español"}\n\n'
            "Keywords EN: {kw}\n"
            "Descripción EN: {desc}"
        ),
    },
    "traduccion.keywords": {
        "modelo": "translategemma",
        "temperatura": 0.1,
        "texto": (
            "Traducí estas palabras clave del inglés al ESPAÑOL rioplatense (Argentina).\n"
            "Reglas:\n"
            "1. Devolvé SOLO las palabras en español, separadas por comas, en el mismo orden.\n"
            "2. Las palabras deben ser SUSTANTIVOS (no verbos ni frases verbales).\n"
            "3. NO dejes ninguna palabra en inglés, traducí TODAS.\n"
            "4. NO es portugués: recordá que en español se dice 'persona', 'objeto', 'color', 'acción'.\n"
            "\n"
            "Palabras en inglés: {kw}"
        ),
    },
    "traduccion.descripcion": {
        "modelo": "translategemma",
        "temperatura": 0.1,
        "texto": (
            "Traducí este texto del inglés al ESPAÑOL rioplatense (Argentina).\n"
            "Reglas:\n"
            "1. Devolvé SOLO la traducción, sin comentarios.\n"
            "2. NO es portugués: en español se dice 'persona', 'objeto', 'imagen', 'acción'.\n"
            "3. Mantené el tono natural del español.\n\n"
            "Texto en inglés: {desc}"
        ),
    },
}

_cache: dict[str, dict[str, Any]] | None = None


def _cargar_yaml() -> dict[str, dict[str, Any]]:
    """Lee prompts.yaml una vez (cache). Sin pyyaml o con error → {} (fallback a DEFAULTS)."""
    global _cache
    if _cache is not None:
        return _cache
    datos: dict[str, dict[str, Any]] = {}
    try:
        import yaml  # type: ignore
    except ImportError:
        log.warning("pyyaml no instalado (pip install pyyaml): prompts.yaml ignorado, se usan defaults embebidos.")
        _cache = datos
        return _cache
    if not PROMPTS_YAML.is_file():
        log.warning("No existe %s: se usan defaults embebidos.", PROMPTS_YAML)
        _cache = datos
        return _cache
    try:
        with open(PROMPTS_YAML, encoding="utf-8") as f:
            crudo = yaml.safe_load(f) or {}
        for grupo, entradas in crudo.items():
            if not isinstance(entradas, dict):
                continue
            for nombre, cfg in entradas.items():
                if isinstance(cfg, dict):
                    clave = f"{grupo}.{nombre}"
                    if clave not in DEFAULTS:
                        log.warning("Clave '%s' en YAML no corresponde a ningún prompt conocido (¿typo? se ignora).", clave)
                        continue
                    datos[clave] = cfg
    except Exception as e:
        log.warning("Error parseando %s (%s): se usan defaults embebidos.", PROMPTS_YAML, e)
        datos = {}
    _cache = datos
    return _cache


def _estricto() -> bool:
    """FLUIR_PROMPTS_STRICT=1 → fallar fuerte en vez de fallback (para CI)."""
    return os.environ.get("FLUIR_PROMPTS_STRICT", "") == "1"


def _validar(clave: str, cfg: dict[str, Any]) -> dict[str, Any] | None:
    """Valida una entrada del YAML. Devuelve None si es inválida (→ fallback)."""
    defecto = DEFAULTS.get(clave)
    if defecto is None:
        log.warning("Clave de prompt desconocida '%s' (se ignora).", clave)
        return None
    texto = cfg.get("texto", "")
    modelo = cfg.get("modelo", "") or defecto["modelo"]
    temp = cfg.get("temperatura", defecto["temperatura"])
    if not isinstance(texto, str) or not texto.strip():
        log.warning("Prompt '%s' vacío en YAML: se usa default embebido.", clave)
        return None
    if not isinstance(modelo, str) or not modelo.strip():
        log.warning("Modelo vacío en '%s': se usa default '%s'.", clave, defecto["modelo"])
        modelo = defecto["modelo"]
    try:
        temp = float(temp)
    except (TypeError, ValueError):
        log.warning("Temperatura inválida en '%s': se usa default %s.", clave, defecto["temperatura"])
        temp = float(defecto["temperatura"])
    if not (0.0 <= temp <= 2.0):
        log.warning("Temperatura %.2f fuera de rango en '%s': se usa default %s.", temp, clave, defecto["temperatura"])
        temp = float(defecto["temperatura"])
    return {"modelo": modelo.strip(), "temperatura": temp, "texto": texto}


def get_config(clave: str) -> dict[str, Any]:
    """Devuelve {"modelo", "temperatura", "texto"} para una clave.

    Precedencia: YAML válido > DEFAULTS. Con FLUIR_PROMPTS_STRICT=1,
    una entrada inválida lanza KeyError en vez de fallback.
    """
    crudo = _cargar_yaml().get(clave)
    if crudo is not None:
        valido = _validar(clave, crudo)
        if valido is not None:
            return valido
        if _estricto():
            raise KeyError(f"Entrada de prompt inválida en YAML: '{clave}'")
    if clave not in DEFAULTS:
        raise KeyError(f"Clave de prompt desconocida: '{clave}' (claves: {sorted(DEFAULTS)})")
    return dict(DEFAULTS[clave])


def _sanitizar(valor: str) -> str:
    """Colapsa espacios/saltos de línea en valores de placeholder (anti-inyección)."""
    return " ".join(str(valor).split())


def get_prompt(clave: str, **valores: str) -> str:
    """Devuelve el texto del prompt, sustituyendo solo sus placeholders conocidos.

    Placeholders desconocidos o no declarados para la clave se dejan intactos
    con un warning (no se rompe por editar el YAML). Si un placeholder
    conocido no aparece en el texto (p. ej. se borró editando), también
    avisa: el valor se habría perdido en silencio.
    """
    texto = get_config(clave)["texto"]
    conocidos = PLACEHOLDERS_POR_CLAVE.get(clave, ())
    for nombre in conocidos:
        if nombre in valores:
            marcador = "{" + nombre + "}"
            if marcador not in texto:
                log.warning("Placeholder '%s' no encontrado en '%s' (¿se borró editando?).", marcador, clave)
            texto = texto.replace(marcador, _sanitizar(valores[nombre]))
    for nombre in valores:
        if nombre not in conocidos:
            log.warning("Placeholder '%s' no declarado para '%s' (se ignora).", nombre, clave)
    return texto


def listar_prompts() -> dict[str, dict[str, Any]]:
    """Todas las claves con su config efectiva (YAML > DEFAULTS)."""
    return {clave: get_config(clave) for clave in sorted(DEFAULTS)}


def recargar() -> None:
    """Limpia el cache (útil en tests)."""
    global _cache
    _cache = None


if __name__ == "__main__":
    import argparse
    import json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Ver prompts efectivos (YAML > defaults)")
    parser.add_argument("--clave", default=None, help="Mostrar solo una clave (ej: vision.combinado)")
    parser.add_argument("--json", action="store_true", help="Salida JSON")
    args = parser.parse_args()

    if args.clave:
        cfg = get_config(args.clave)
        if args.json:
            print(json.dumps({args.clave: cfg}, ensure_ascii=False, indent=2))
        else:
            print(f"[{args.clave}] modelo={cfg['modelo']} temp={cfg['temperatura']}\n{cfg['texto']}")
    else:
        todo = listar_prompts()
        if args.json:
            print(json.dumps(todo, ensure_ascii=False, indent=2))
        else:
            for clave, cfg in todo.items():
                primera = cfg["texto"].strip().split("\n")[0][:100]
                print(f"{clave:28s} modelo={cfg['modelo']:22s} temp={cfg['temperatura']:<4} | {primera}...")
