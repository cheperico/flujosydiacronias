"""
Identificación y selección de la mejor imagen de una tanda (batch).

Estrategias de selección:
  1. **Por calidad técnica** — analiza nitidez, exposición, composición (usa Ollama visión)
  2. **Por contenido** — elige la imagen que mejor coincide con un tema o descripción
  3. **Por diversidad** — selecciona la más representativa de un conjunto
  4. **Por keywords** — elige la que tiene las keywords más relevantes

Uso básico:
    from scripts.ai_media.batch_selector import seleccionar_mejor_imagen

    # Selección por calidad visual
    mejor = seleccionar_mejor_imagen(["foto1.jpg", "foto2.jpg", "foto3.jpg"])
    print(f"Mejor imagen: {mejor['ruta']}")
    print(f"Razón: {mejor['razon']}")

    # Selección por tema
    mejor = seleccionar_mejor_imagen(
        imagenes,
        criterio="tema",
        tema="atardecer en la playa"
    )
    print(f"Mejor imagen para '{tema}': {mejor['ruta']}")

Línea de comandos:
    python -m scripts.ai_media.batch_selector imagen1.jpg imagen2.jpg imagen3.jpg
    python -m scripts.ai_media.batch_selector *.jpg --criterio tema --tema "paisaje natural"
"""

import json
import logging
from pathlib import Path
from typing import Optional

from scripts.ai_media.ollama_client import OllamaVision
from scripts.ai_media.image_analysis import extraer_keywords, describir_imagen
from scripts.ai_media.prompts import get_config, get_prompt
from scripts.ai_media.proxy import obtener_proxy

logger = logging.getLogger(__name__)

# Modelo de visión para la SELECCIÓN de la mejor imagen de una tanda.
# Este script es SOLO curación (no escribe en la DB), así que no hace falta
# el español ni el modelo pesado del FLUJO IA (image_analysis). moondream es
# ~15x más rápido (~0.8s vs 3-13s por imagen) y suficiente para evaluar
# calidad/tema. El FLUJO IA (improve_db, tag_images, etc.) NO se toca: sigue
# con minicpm-v4.6 donde sí importa la calidad y el español.
# Los prompts/modelos/temperaturas viven en prompts.yaml (grupo "seleccion").
MODELO_SELECCION_DEFAULT = "moondream:latest"


def _resolver_seleccion(
    clave: str,
    modelo: str | None,
    temperatura: float | None,
) -> tuple[str, float, str]:
    """Resuelve (modelo, temperatura, texto) para una clave prompts.yaml."""
    cfg = get_config(clave)
    return (
        modelo or cfg["modelo"],
        cfg["temperatura"] if temperatura is None else temperatura,
        cfg["texto"],
    )

def seleccionar_mejor_imagen(
    rutas_imagenes: list[str],
    criterio: str = "calidad",
    modelo: str | None = None,
    tema: Optional[str] = None,
    temperatura: float | None = None,
    usar_proxy: bool = True,
) -> dict:
    """
    Selecciona la mejor imagen de una tanda según el criterio indicado.

    Args:
        rutas_imagenes: Lista de rutas a imágenes.
        criterio: Estrategia de selección:
            - "calidad": evalúa calidad visual (nitidez, composición, iluminación)
            - "tema": elige la que mejor coincide con el tema indicado
            - "diversidad": elige la más representativa
            - "descripcion": la con mejor descripción general
            - "nitidez": la más nítida (varianza Laplaciano, SIN IA, instantáneo)
        modelo: Modelo de visión.
        tema: Requerido si criterio="tema". Descripción del tema deseado.
        temperatura: Control de creatividad.
        usar_proxy: Si True, redimensiona a 800px antes de enviar a la IA
                    (solo aplica a criterios que usan visión: calidad, tema,
                    diversidad, descripcion).

    Returns:
        Dict con:
          - "ruta": ruta de la imagen seleccionada
          - "puntaje": puntaje numérico (si aplica)
          - "razon": explicación de la selección
          - "evaluaciones": lista de evaluaciones individuales (si criterio=calidad)

    Raises:
        ValueError: Si la lista de imágenes está vacía.
        FileNotFoundError: Si alguna imagen no existe.
    """
    if not rutas_imagenes:
        raise ValueError("La lista de imágenes está vacía")

    # Validar que existan
    for r in rutas_imagenes:
        if not Path(r).exists():
            raise FileNotFoundError(f"No se encuentra la imagen: {r}")

    if len(rutas_imagenes) == 1:
        logger.info("Solo una imagen en la tanda: %s", rutas_imagenes[0])
        return {
            "ruta": rutas_imagenes[0],
            "puntaje": 10,
            "razon": "Única imagen disponible.",
            "evaluaciones": [],
        }

    if criterio == "calidad":
        return _seleccionar_por_calidad(rutas_imagenes, modelo, temperatura, usar_proxy)
    elif criterio == "tema":
        if not tema:
            raise ValueError("El criterio 'tema' requiere el parámetro 'tema'")
        return _seleccionar_por_tema(rutas_imagenes, tema, modelo, temperatura, usar_proxy)
    elif criterio == "diversidad":
        return _seleccionar_por_diversidad(rutas_imagenes, modelo, temperatura, usar_proxy)
    elif criterio == "descripcion":
        return _seleccionar_por_descripcion(rutas_imagenes, modelo, temperatura, usar_proxy)
    elif criterio == "nitidez":
        return _seleccionar_por_nitidez(rutas_imagenes)
    else:
        raise ValueError(f"Criterio desconocido: {criterio}")


def _seleccionar_por_calidad(
    rutas: list[str],
    modelo: str | None,
    temperatura: float | None,
    usar_proxy: bool = True,
) -> dict:
    """Selecciona por calidad visual evaluada por el modelo."""
    modelo, temperatura, prompt = _resolver_seleccion(
        "seleccion.calidad", modelo, temperatura)
    cliente = OllamaVision(modelo=modelo)
    evaluaciones = []

    for ruta in rutas:
        try:
            ruta_ia = obtener_proxy(ruta, usar_proxy=usar_proxy)
            respuesta = cliente.analizar_imagen(
                ruta_ia,
                prompt=prompt,
                temperatura=temperatura,
            )
            # Parsear respuesta: "8.5. Buena composición..."
            puntaje = _extraer_puntaje(respuesta)
            evaluaciones.append({
                "ruta": ruta,
                "puntaje": puntaje,
                "explicacion": respuesta,
            })
            logger.info("Evaluación de %s: %s", Path(ruta).name, respuesta)
        except Exception as e:
            logger.warning("Error evaluando %s: %s", ruta, e)
            evaluaciones.append({
                "ruta": ruta,
                "puntaje": 0,
                "explicacion": f"Error: {e}",
            })

    # Ordenar por puntaje descendente
    evaluaciones.sort(key=lambda x: x["puntaje"], reverse=True)

    mejor = evaluaciones[0]
    return {
        "ruta": mejor["ruta"],
        "puntaje": mejor["puntaje"],
        "razon": mejor["explicacion"],
        "evaluaciones": evaluaciones,
    }


def _seleccionar_por_tema(
    rutas: list[str],
    tema: str,
    modelo: str | None,
    temperatura: float | None,
    usar_proxy: bool = True,
) -> dict:
    """Selecciona la imagen que mejor coincide con un tema."""
    modelo, temperatura, _ = _resolver_seleccion(
        "seleccion.tema", modelo, temperatura)
    cliente = OllamaVision(modelo=modelo)
    evaluaciones = []

    prompt_tema = get_prompt("seleccion.tema", tema=tema)

    for ruta in rutas:
        try:
            ruta_ia = obtener_proxy(ruta, usar_proxy=usar_proxy)
            respuesta = cliente.analizar_imagen(
                ruta_ia, prompt=prompt_tema, temperatura=temperatura,
            )
            puntaje = _extraer_puntaje(respuesta)
            evaluaciones.append({
                "ruta": ruta,
                "puntaje": puntaje,
                "explicacion": respuesta,
            })
        except Exception as e:
            logger.warning("Error evaluando %s: %s", ruta, e)
            evaluaciones.append({
                "ruta": ruta, "puntaje": 0, "explicacion": f"Error: {e}",
            })

    evaluaciones.sort(key=lambda x: x["puntaje"], reverse=True)
    mejor = evaluaciones[0]

    return {
        "ruta": mejor["ruta"],
        "puntaje": mejor["puntaje"],
        "razon": f"Mejor coincidencia con tema '{tema}': {mejor['explicacion']}",
        "evaluaciones": evaluaciones,
    }


def _seleccionar_por_diversidad(
    rutas: list[str],
    modelo: str,
    temperatura: float,
    usar_proxy: bool = True,
) -> dict:
    """
    Selecciona la imagen más representativa del conjunto.
    Analiza todas y pide al modelo que elija la más representativa.
    """
    # Para conjuntos pequeños, analizamos una por una y comparamos
    from scripts.ai_media.image_analysis import extraer_keywords_batch

    resultados = extraer_keywords_batch(
        rutas, modelo=modelo, temperatura=temperatura, usar_proxy=usar_proxy,
    )

    # La imagen con más keywords suele ser la más descriptiva/representativa
    mejor_ruta = None
    max_keywords = 0
    mejor_keywords = []

    for item in resultados:
        if item["error"]:
            continue
        if len(item["keywords"]) > max_keywords:
            max_keywords = len(item["keywords"])
            mejor_ruta = item["ruta"]
            mejor_keywords = item["keywords"]

    if not mejor_ruta:
        # Fallback: primera imagen
        mejor_ruta = rutas[0]

    return {
        "ruta": mejor_ruta,
        "puntaje": max_keywords,
        "razon": f"Imagen más descriptiva del conjunto ({max_keywords} keywords: {', '.join(mejor_keywords)})",
        "evaluaciones": resultados,
    }


def _seleccionar_por_descripcion(
    rutas: list[str],
    modelo: str,
    temperatura: float,
    usar_proxy: bool = True,
) -> dict:
    """Selecciona la imagen con la descripción más rica/detallada."""
    from scripts.ai_media.image_analysis import describir_imagen

    evaluaciones = []
    for ruta in rutas:
        try:
            desc = describir_imagen(
                ruta, modelo=modelo, temperatura=temperatura, usar_proxy=usar_proxy,
            )
            # La longitud de la descripción como proxy de riqueza
            puntaje = min(len(desc) / 10, 10)
            evaluaciones.append({
                "ruta": ruta,
                "puntaje": puntaje,
                "explicacion": desc,
            })
        except Exception as e:
            logger.warning("Error describiendo %s: %s", ruta, e)
            evaluaciones.append({
                "ruta": ruta, "puntaje": 0, "explicacion": f"Error: {e}",
            })

    evaluaciones.sort(key=lambda x: x["puntaje"], reverse=True)
    mejor = evaluaciones[0]

    return {
        "ruta": mejor["ruta"],
        "puntaje": round(mejor["puntaje"], 1),
        "razon": f"Descripción más rica: {mejor['explicacion'][:100]}...",
        "evaluaciones": evaluaciones,
    }


# Lado máximo de análisis de nitidez: la varianza del Laplaciano es una métrica
# de enfoque invariante ante resolución razonable. Reducir aquí corta el coste
# ~10-20x en fotos grandes sin alterar la elección de la más nítida.
_NITIDEZ_ANALISIS = 800


def _seleccionar_por_nitidez(rutas: list[str]) -> dict:
    """
    Selecciona la imagen más nítida usando la varianza del Laplaciano.

    Método clásico de enfoque (focus measure): se convoluciona la imagen en
    escala de grises con un kernel Laplaciano 3x3 y se calcula la varianza
    del resultado. A mayor varianza, más detalles de alto contraste
    (más nítida la imagen).

    Es 100% computacional (Pillow, sin IA): instantáneo incluso con decenas
    de imágenes. Ideal para tandas donde las fotos son casi idénticas y el
    defecto típico es desenfoque o movimiento.

    Args:
        rutas: Lista de rutas a imágenes.

    Returns:
        Dict con la imagen de mayor nitidez.
    """
    from PIL import Image, ImageFilter, ImageStat

    kernel_laplaciano = ImageFilter.Kernel(
        size=(3, 3),
        kernel=[0, 1, 0, 1, -4, 1, 0, 1, 0],
        scale=1,
        offset=0,
    )

    evaluaciones = []
    for ruta in rutas:
        try:
            with Image.open(ruta) as img:
                img.thumbnail((_NITIDEZ_ANALISIS, _NITIDEZ_ANALISIS))
                if img.mode != "L":
                    img = img.convert("L")
                laplaciano = img.filter(kernel_laplaciano)
                var = float(ImageStat.Stat(laplaciano).var[0])
            evaluaciones.append({
                "ruta": ruta,
                "puntaje": round(var, 2),
                "explicacion": f"Nitidez (varianza Laplaciano): {var:.1f}",
            })
            logger.info("Nitidez de %s: %.1f", Path(ruta).name, var)
        except Exception as e:
            logger.warning("Error calculando nitidez de %s: %s", ruta, e)
            evaluaciones.append({
                "ruta": ruta, "puntaje": 0, "explicacion": f"Error: {e}",
            })

    evaluaciones.sort(key=lambda x: x["puntaje"], reverse=True)
    mejor = evaluaciones[0]

    return {
        "ruta": mejor["ruta"],
        "puntaje": mejor["puntaje"],
        "razon": f"Mayor nitidez (varianza Laplaciano {mejor['puntaje']:.1f})",
        "evaluaciones": evaluaciones,
    }


def _extraer_puntaje(respuesta: str) -> float:
    """
    Extrae un puntaje numérico del inicio de la respuesta.
    
    Ejemplos:
      "8.5. Buena composición..." -> 8.5
      "7. Imagen bien iluminada..." -> 7.0
      "3. Muy borrosa..." -> 3.0
    """
    import re
    match = re.match(r"(\d+(?:\.\d+)?)", respuesta.strip())
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return 5.0  # Valor neutral
    return 5.0


def seleccionar_mejores_n(
    rutas_imagenes: list[str],
    n: int = 3,
    criterio: str = "calidad",
    modelo: str | None = None,
    tema: Optional[str] = None,
    usar_proxy: bool = True,
) -> list[dict]:
    """
    Selecciona las N mejores imágenes de una tanda.

    Args:
        rutas_imagenes: Lista de rutas a imágenes.
        n: Número de imágenes a seleccionar.
        criterio: Estrategia de selección.
        modelo: Modelo de visión.
        tema: Requerido si criterio="tema". Tema a buscar.
        usar_proxy: Si True, redimensiona a 800px antes de enviar a la IA.

    Returns:
        Lista de dicts ordenados por puntaje descendente.
    """
    resultado = seleccionar_mejor_imagen(
        rutas_imagenes, criterio=criterio, modelo=modelo, tema=tema, usar_proxy=usar_proxy,
    )
    evaluaciones = resultado.get("evaluaciones", [])
    evaluaciones.sort(key=lambda x: x.get("puntaje", 0), reverse=True)
    return evaluaciones[:n]


# ---- CLI ----
if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Seleccionar la mejor imagen de una tanda"
    )
    parser.add_argument("imagenes", nargs="+", help="Rutas a las imágenes")
    parser.add_argument("--criterio", default="calidad",
                        choices=["calidad", "tema", "diversidad", "descripcion", "nitidez"],
                        help="Criterio de selección (nitidez es computacional, sin IA)")
    parser.add_argument("--tema", help="Tema deseado (requerido si criterio=tema)")
    parser.add_argument("--modelo", default=None,
                        help="Modelo de visión (default: prompts.yaml seleccion.*)")
    parser.add_argument("--no-proxy", action="store_true",
                        help="No usar proxies redimensionados (más lento)")
    parser.add_argument("--n", type=int, default=1,
                        help="Número de mejores imágenes a mostrar (default: 1)")
    parser.add_argument("--json", help="Exportar resultados a JSON")

    args = parser.parse_args()

    if args.criterio == "tema" and not args.tema:
        parser.error("--criterio tema requiere --tema")

    usar_proxy = not args.no_proxy

    if args.n == 1:
        resultado = seleccionar_mejor_imagen(
            args.imagenes,
            criterio=args.criterio,
            modelo=args.modelo,
            tema=args.tema,
            usar_proxy=usar_proxy,
        )
        print(f"\nMejor imagen ({args.criterio}):")
        print(f"  Ruta: {resultado['ruta']}")
        print(f"  Puntaje: {resultado.get('puntaje', 'N/A')}")
        print(f"  Razón: {resultado['razon']}")

        if args.json:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump(resultado, f, ensure_ascii=False, indent=2)
            print(f"\nExportado a: {args.json}")
    else:
        mejores = seleccionar_mejores_n(
            args.imagenes,
            n=args.n,
            criterio=args.criterio,
            modelo=args.modelo,
            usar_proxy=usar_proxy,
        )
        print(f"\nTop {args.n} imágenes ({args.criterio}):")
        for i, item in enumerate(mejores, 1):
            print(f"  {i}. {Path(item['ruta']).name} "
                  f"(puntaje: {item.get('puntaje', 'N/A')})")
            print(f"     {item.get('explicacion', item.get('razon', ''))[:100]}")
