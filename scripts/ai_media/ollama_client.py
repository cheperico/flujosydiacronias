"""
Cliente compartido para modelos de visión y lenguaje de Ollama.

Proporciona una interfaz unificada para:
  - Analizar imágenes con modelos de visión (qwen2.5vl, moondream, llama3.2-vision, gemma4)
  - Consultar modelos de texto
  - Listar y seleccionar modelos disponibles

Modelos de visión recomendados (ordenados por capacidad):
  1. minicpm-v4.6:latest (1.6 GB) — GANADOR de la comparativa (Ago 2026), default (MODELO_VISION_DEFAULT)
  2. qwen2.5vl:3b (3.2 GB) — liviano, sigue bien prompts complejos
  3. qwen2.5vl:latest (6.0 GB) — mejor equilibrio calidad/velocidad
  4. llama3.2-vision:latest (7.8 GB) — buena calidad general
  5. gemma4:e4b (9.6 GB) — multimodal potente
  6. moondream:latest (1.7 GB) — rápido y ligero, pero NO sigue prompts complejos

Uso básico:
    from scripts.ai_media.ollama_client import OllamaVision

    cliente = OllamaVision(modelo="minicpm-v4.6:latest")
    respuesta = cliente.analizar_imagen("ruta/a/imagen.jpg",
                                        "Describí esta imagen en una frase")
    print(respuesta)
"""

import logging
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional

try:
    import ollama
except ImportError:
    ollama = None  # type: ignore

logger = logging.getLogger(__name__)

if ollama is None:
    logger.warning("Módulo 'ollama' no instalado. Instalar con: pip install ollama")

# Modelos de visión disponibles en el sistema
MODELOS_VISION = [
    "minicpm-v4.6:latest",
    "qwen2.5vl:latest",
    "qwen2.5vl:3b",
    "moondream:latest",
    "llama3.2-vision:latest",
    "gemma4:e4b",
]

# Modelos de texto disponibles
MODELOS_TEXTO = [
    "gemma3:latest",
    "qwen3.5:9b",
    "qwen3.5:4b",
    "deepseek-r1:latest",
    "llama3.1:8b-instruct-q4_K_M",
    "llama3.2:3b-instruct-q4_K_M",
]

# Contexto (num_ctx) por defecto para modelos de visión.
# Ollama sin num_ctx carga el máximo del modelo (128000) → 8.2 GB RAM,
# saturando la memoria y disparando el swapping en máquinas sin GPU.
# 4096 cubre los ~2718 tokens de una imagen 1600px + prompt, y deja margen
# para datos extra (estilo de descripción, keywords obligatorias).
# Memoria del modelo: ~2.9 GB.
NUM_CTX_DEFAULT = 4096


# ═══════════════════════════════════════════════════════════════════════
#  Verificación e inicio automático del servidor Ollama
# ═══════════════════════════════════════════════════════════════════════

# Host/puerto por defecto del servidor Ollama (configurable vía entorno)
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "127.0.0.1")
OLLAMA_PORT = int(os.environ.get("OLLAMA_PORT", "11434"))


def ollama_responde(timeout: float = 2.0) -> bool:
    """Verifica si el servidor Ollama responde en el host/puerto configurado.

    Args:
        timeout: Segundos máximo para el intento de conexión.

    Returns:
        True si hay un proceso escuchando en el puerto de Ollama.
    """
    try:
        with socket.create_connection((OLLAMA_HOST, OLLAMA_PORT), timeout=timeout):
            return True
    except OSError:
        return False


def _buscar_binario_ollama() -> str | None:
    """Localiza el binario de Ollama (PATH primero, luego rutas típicas de Windows)."""
    binario = shutil.which("ollama")
    if binario:
        return binario
    candidatos = [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe"),
        r"C:\Program Files\Ollama\ollama.exe",
    ]
    for c in candidatos:
        if os.path.isfile(c):
            return c
    return None


def iniciar_ollama() -> bool:
    """Lanza `ollama serve` en segundo plano sin bloquear la terminal.

    Returns:
        True si se encontró el binario y se lanzó el proceso.
    """
    binario = _buscar_binario_ollama()
    if not binario:
        logger.error(
            "No se encontró el binario de Ollama en PATH. "
            "Instalalo o agregalo al PATH manualmente."
        )
        return False
    try:
        flags = {}
        if os.name == "nt":
            flags["creationflags"] = subprocess.CREATE_NO_WINDOW
        subprocess.Popen(
            [binario, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            **flags,
        )
        return True
    except Exception as e:
        logger.error("No se pudo iniciar `ollama serve`: %s", e)
        return False


def asegurar_ollama(espera_max: float = 30.0, auto_iniciar: bool = True) -> bool:
    """Asegura que el servidor Ollama esté corriendo, iniciándolo si hace falta.

    Verifica si el puerto de Ollama responde; si no y `auto_iniciar` es True,
    lanza `ollama serve` en segundo plano y espera hasta `espera_max` segundos
    a que levante.

    Args:
        espera_max: Segundos máximo de espera tras lanzar el servidor.
        auto_iniciar: Si False, solo verifica sin intentar iniciar.

    Returns:
        True si Ollama quedó disponible, False si no.
    """
    if ollama_responde():
        return True

    if not auto_iniciar:
        logger.warning("Ollama no está corriendo (auto-inicio desactivado).")
        return False

    logger.info("Ollama no responde. Iniciando `ollama serve`...")
    if not iniciar_ollama():
        return False

    t0 = time.monotonic()
    while time.monotonic() - t0 < espera_max:
        if ollama_responde(timeout=0.5):
            logger.info("Ollama iniciado correctamente.")
            return True
        time.sleep(0.5)

    logger.error("Ollama no respondió tras %.0f s de espera.", espera_max)
    return False


class OllamaVision:
    """Cliente para analizar imágenes usando modelos de visión de Ollama."""

    def __init__(self, modelo: str = "minicpm-v4.6:latest", timeout: int = 180,
                 num_ctx: int = NUM_CTX_DEFAULT):
        """
        Args:
            modelo: Nombre del modelo de visión a usar.
            timeout: Timeout en segundos para la consulta.
            num_ctx: Ventana de contexto (num_ctx) para la inferencia.
                Evita que Ollama reserve el contexto máximo del modelo
                (128000 → 8.2 GB RAM). 4096 cubre imagen 1600px + prompt
                con margen, usando ~2.9 GB.
        """
        if ollama is None:
            raise ImportError("Módulo 'ollama' no instalado. Instalar con: pip install ollama")
        # Asegurar que el servidor Ollama esté corriendo antes de consultarlo
        if not asegurar_ollama():
            logger.error(
                "Ollama no está disponible. Verificá que el servidor esté "
                "corriendo (ollama serve) o que el binario esté en PATH."
            )
        if modelo not in self._listar_modelos_disponibles():
            logger.warning(
                "Modelo '%s' no encontrado entre los disponibles. "
                "Se intentará cargar igualmente.", modelo
            )
        self.modelo = modelo
        self.timeout = timeout
        self.num_ctx = num_ctx

    @staticmethod
    def _listar_modelos_disponibles() -> list[str]:
        """Devuelve lista de modelos instalados en Ollama."""
        try:
            response = ollama.list()
            # Ollama Python >=0.3 devuelve ListResponse con .models
            # Versiones anteriores devuelven dict con clave "models"
            if hasattr(response, "models"):
                modelos = response.models
            elif isinstance(response, dict):
                modelos = response.get("models", [])
            else:
                modelos = list(response)
            return [m.model if hasattr(m, "model") else str(m) for m in modelos]
        except Exception as e:
            logger.warning("No se pudo listar modelos de Ollama: %s", e)
            return []

    def analizar_imagen(
        self,
        ruta_imagen: str,
        prompt: str = "Describí esta imagen en una frase breve.",
        temperatura: float = 0.3,
    ) -> str:
        """
        Analiza una imagen con el modelo de visión.

        Args:
            ruta_imagen: Ruta al archivo de imagen.
            prompt: Instrucción/pregunta sobre la imagen.
            temperatura: Control de creatividad (0.0 = determinista, 1.0 = creativo).

        Returns:
            Texto con la respuesta del modelo.
        """
        ruta = Path(ruta_imagen)
        if not ruta.exists():
            raise FileNotFoundError(f"No se encuentra la imagen: {ruta_imagen}")

        logger.info("Analizando imagen: %s con modelo %s", ruta.name, self.modelo)

        try:
            # Leer imagen como bytes
            with open(ruta, "rb") as f:
                imagen_bytes = f.read()

            # Usar Client con timeout (la función global ollama.chat() no acepta timeout)
            cliente = ollama.Client(timeout=self.timeout)
            response = cliente.chat(
                model=self.modelo,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                        "images": [imagen_bytes],
                    }
                ],
                options={"temperature": temperatura, "num_ctx": self.num_ctx},
            )
            # Compatibilidad: ollama >=0.3 devuelve ChatResponse (objeto),
            # versiones anteriores devuelven dict
            if hasattr(response, "message"):
                texto = response.message.content.strip()
            elif isinstance(response, dict):
                texto = response["message"]["content"].strip()
            else:
                texto = str(response).strip()
            logger.debug("Respuesta obtenida (%d caracteres)", len(texto))
            return texto

        except Exception as e:
            logger.error("Error al analizar imagen con %s: %s", self.modelo, e)
            raise

    def analizar_imagenes(
        self,
        rutas_imagenes: list[str],
        prompt: str = "Describí esta imagen en una frase breve.",
        temperatura: float = 0.3,
    ) -> list[dict]:
        """
        Analiza múltiples imágenes y devuelve resultados.

        Args:
            rutas_imagenes: Lista de rutas a imágenes.
            prompt: Prompt para cada imagen.
            temperatura: Control de creatividad.

        Returns:
            Lista de dicts con {"ruta": str, "respuesta": str, "error": str|None}
        """
        resultados = []
        for ruta in rutas_imagenes:
            try:
                respuesta = self.analizar_imagen(ruta, prompt, temperatura)
                resultados.append({"ruta": ruta, "respuesta": respuesta, "error": None})
            except Exception as e:
                logger.error("Error en %s: %s", ruta, e)
                resultados.append({"ruta": ruta, "respuesta": None, "error": str(e)})
        return resultados

    def cambiar_modelo(self, nuevo_modelo: str):
        """Cambia el modelo de visión activo."""
        logger.info("Cambiando modelo: %s -> %s", self.modelo, nuevo_modelo)
        self.modelo = nuevo_modelo


class OllamaEmbedding:
    """Cliente para generar embeddings vectoriales usando Ollama.

    Uso:
        cliente = OllamaEmbedding(modelo="nomic-embed-text")
        vector = cliente.embed("Texto a embedder")
        print(len(vector))  # 768 para nomic-embed-text
    """

    def __init__(self, modelo: str = "nomic-embed-text", timeout: int = 60):
        # Asegurar que el servidor Ollama esté corriendo antes de usarlo
        if not asegurar_ollama():
            logger.error(
                "Ollama no está disponible. Verificá que el servidor esté "
                "corriendo (ollama serve) o que el binario esté en PATH."
            )
        self.modelo = modelo
        self.timeout = timeout

    def embed(self, texto: str) -> list[float]:
        """
        Genera un embedding vectorial para el texto dado.

        Args:
            texto: Texto a embedder.

        Returns:
            Lista de floats con el vector de embedding.
        """
        try:
            # Compatibilidad: ollama >=0.3 usa 'input', versiones anteriores usan 'prompt'
            kwargs = {"model": self.modelo}
            # La API >=0.3 cambió prompt → input; probar ambas
            try:
                kwargs["input"] = texto
                response = ollama.embeddings(**kwargs)
            except TypeError:
                kwargs.pop("input")
                kwargs["prompt"] = texto
                response = ollama.embeddings(**kwargs)

            # Compatibilidad dict/objeto
            if hasattr(response, "embedding"):
                vector = response.embedding
            elif isinstance(response, dict):
                vector = response.get("embedding", [])
            else:
                vector = list(response)

            if not vector:
                raise ValueError("Ollama devolvió un embedding vacío")
            return vector
        except Exception as e:
            logger.error("Error generando embedding con %s: %s", self.modelo, e)
            raise


