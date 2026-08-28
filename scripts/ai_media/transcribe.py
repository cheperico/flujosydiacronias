"""
Transcripción de audio a texto usando faster-whisper.

Soporta archivos de audio (WAV, MP3, FLAC, OGG, M4A, AAC, etc.)
y **videos** (MP4, MOV, AVI, MKV, etc.): si se pasa un video,
automáticamente extrae la pista de audio con ffmpeg antes de transcribir.

Exportación a SRT (subtítulos), TXT (texto plano) y JSON (con timestamps).

Uso básico:
    from scripts.ai_media.transcribe import transcribir_audio, segmentos_a_srt

    # Desde audio
    segmentos, info = transcribir_audio("audio.wav", modelo="small")
    print(f"Idioma: {info.language}")

    # Desde video (extrae audio automáticamente)
    segmentos, info = transcribir_audio("video.mp4")

    # Exportar
    segmentos_a_srt(segmentos, "transcripcion.srt")
    segmentos_a_txt(segmentos, "transcripcion.txt")

Línea de comandos:
    python -m scripts.ai_media.transcribe audio.wav --modelo small --srt salida.srt
    python -m scripts.ai_media.transcribe video.mp4 --modelo small --srt salida.srt
"""

import json
import logging
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Optional

try:
    import faster_whisper
except ImportError:
    faster_whisper = None  # type: ignore

logger = logging.getLogger(__name__)

if faster_whisper is None:
    logger.warning("Módulo 'faster-whisper' no instalado. Instalar con: pip install faster-whisper")

# Extensiones de audio que faster-whisper puede leer directamente
EXT_AUDIO = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".opus"}

# Extensiones de video que requieren extracción de audio
EXT_VIDEO = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".mxf", ".mts", ".m2ts"}

# Tamaños de modelo whisper disponibles
MODELOS_WHISPER = ["tiny", "base", "small", "medium", "large", "large-v2", "large-v3"]


def es_archivo_video(ruta: str) -> bool:
    """Detecta si un archivo es un video por su extensión."""
    return Path(ruta).suffix.lower() in EXT_VIDEO


def es_archivo_audio(ruta: str) -> bool:
    """Detecta si un archivo es de audio por su extensión."""
    return Path(ruta).suffix.lower() in EXT_AUDIO


def extraer_audio_de_video(
    ruta_video: str,
    formato_salida: str = "wav",
    sample_rate: int = 16000,
    canales: int = 1,
    directorio_salida: Optional[str] = None,
) -> str:
    """
    Extrae la pista de audio de un video usando ffmpeg.

    Convierte a WAV mono 16kHz (formato óptimo para whisper).

    Args:
        ruta_video: Ruta al archivo de video.
        formato_salida: Formato de audio ("wav", "mp3", "flac").
        sample_rate: Frecuencia de muestreo (whisper funciona mejor con 16kHz).
        canales: 1 = mono, 2 = stereo.
        directorio_salida: Directorio para el audio extraído.
                          Si es None, usa directorio temporal.

    Returns:
        Ruta al archivo de audio extraído.

    Raises:
        FileNotFoundError: Si el video no existe o ffmpeg no está instalado.
        RuntimeError: Si falla la extracción.
    """
    ruta = Path(ruta_video)
    if not ruta.exists():
        raise FileNotFoundError(f"No se encuentra el video: {ruta_video}")

    import shutil
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg no está disponible. Instalalo o extraé el audio manualmente."
        )

    # Crear directorio de salida
    if directorio_salida:
        out_dir = Path(directorio_salida)
        out_dir.mkdir(parents=True, exist_ok=True)
        audio_salida = out_dir / f"{ruta.stem}.{formato_salida}"
    else:
        out_dir = Path(tempfile.mkdtemp(prefix="flujos_audio_"))
        audio_salida = out_dir / f"{ruta.stem}.{formato_salida}"

    logger.info(
        "Extrayendo audio de %s -> %s (%d Hz, %d canales)",
        ruta.name, audio_salida.name, sample_rate, canales
    )

    # Mapear formato a codec de audio
    codec_map = {
        "wav": "pcm_s16le",
        "mp3": "libmp3lame",
        "flac": "flac",
    }
    codec = codec_map.get(formato_salida, "pcm_s16le")

    try:
        cmd = [
            ffmpeg, "-i", str(ruta),
            "-vn",                          # sin video
            "-acodec", codec,
            "-ar", str(sample_rate),        # frecuencia de muestreo
            "-ac", str(canales),            # canales
            "-y",                           # sobrescribir
            str(audio_salida),
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600
        )
        if result.returncode != 0:
            logger.error("ffmpeg error: %s", result.stderr[:500])
            raise RuntimeError(
                f"ffmpeg falló al extraer audio: {result.stderr[:200]}"
            )

    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Timeout extrayendo audio de {ruta_video}")
    except FileNotFoundError:
        raise RuntimeError("ffmpeg no encontrado en el sistema")

    if not audio_salida.exists():
        raise RuntimeError(
            f"No se generó el archivo de audio: {audio_salida}"
        )

    logger.info("Audio extraído: %s (%.1f MB)", audio_salida.name,
                audio_salida.stat().st_size / 1_048_576)
    return str(audio_salida)


def transcribir_audio(
    ruta_audio: str,
    modelo: str = "base",
    device: str = "auto",
    compute_type: str = "int8",
    beam_size: int = 5,
    language: Optional[str] = None,
    word_timestamps: bool = False,
    extraer_audio: bool = True,
    vad_filter: bool = False,
    vad_parameters: Optional[dict] = None,
    condition_on_previous_text: bool = True,
    no_speech_threshold: float = 0.6,
    compression_ratio_threshold: float = 2.4,
    log_prob_threshold: float = -1.0,
    incluir_metricas: bool = False,
) -> tuple[list[dict], object]:
    """
    Transcribe un archivo de audio o video a texto usando faster-whisper.

    Si el archivo es un video, extrae automáticamente la pista de audio
    con ffmpeg antes de transcribir.

    Args:
        ruta_audio: Ruta al archivo de audio o video.
        modelo: Tamaño del modelo whisper ("tiny", "base", "small", "medium", "large").
        device: "cpu", "cuda" o "auto" (detecta GPU disponible).
        compute_type: Precisión ("int8", "float16", "float32"). int8 recomendado para CPU.
        beam_size: Tamaño del beam search (mayor = más preciso pero más lento).
        language: Código de idioma (ej: "es", "en"). None = detección automática.
        word_timestamps: Si True, incluye marcas por palabra.
        extraer_audio: Si True y el archivo es un video, extrae audio automáticamente.
                       Si False, pasa el video directamente a whisper (puede fallar).
        vad_filter: Si True, usa Silero VAD para transcribir SOLO los tramos con
                    voz humana, ignorando silencio/ruido ambiental. Es el gate
                    clave para no alucinar sobre clips sin habla.
        vad_parameters: Dict de parámetros para VAD (threshold, min_speech_duration_ms,
                        min_silence_duration_ms, speech_pad_ms). Si None usa defaults
                        de faster-whisper. Recomendado para cámara de acción:
                        {"min_speech_duration_ms": 300}.
        condition_on_previous_text: Si False, transcribe cada tramo sin usar el
                    texto anterior — corta los lazos de repetición típicos de
                    las alucinaciones sobre ruido (ej: "I'm going to finish it" ×10).
        no_speech_threshold: Prob bajo el cual un tramo se considera "no habla".
                    Aceptar texto necesita no_speech_prob < this.
        compression_ratio_threshold: gzip(texto)/len(texto). Si supera este valor,
                    el tramo es texto muy repetitivo (alucinación) y se descarta.
        log_prob_threshold: avg_logprob por token. Aceptar texto necesita
                    avg_logprob >= log_prob_threshold (proxy de confianza/inteligibilidad;
                    recomendado -0.8 para exigir texto asentado).
        incluir_metricas: Si True, cada segmento incluye además los campos
                    "promedio_logprob", "no_hay_habla_prob" y "ratio_compresion",
                    útiles para filtrar alucinaciones post-hoc.

    Returns:
        Tuple de (segmentos, info_deteccion).
        segmentos: lista de dicts con "inicio", "fin", "texto"
                   (+ metricas si incluir_metricas=True).
        info_deteccion: objeto con .language y .language_probability.

    Raises:
        FileNotFoundError: Si no existe el archivo de entrada.
        RuntimeError: Si falla la transcripción o extracción de audio.
    """
    if faster_whisper is None:
        raise ImportError("faster-whisper no instalado. Instalar con: pip install faster-whisper")
    ruta = Path(ruta_audio)
    if not ruta.exists():
        raise FileNotFoundError(f"No se encuentra el archivo: {ruta_audio}")

    # --- Paso 1: Si es video, extraer audio ---
    ruta_a_transcribir = str(ruta)
    audio_temporal = None

    if es_archivo_video(str(ruta)) and extraer_audio:
        try:
            ruta_a_transcribir = extraer_audio_de_video(str(ruta))
            audio_temporal = ruta_a_transcribir
        except Exception as e:
            logger.warning(
                "No se pudo extraer audio del video, intentando con whisper directo: %s", e
            )
            ruta_a_transcribir = str(ruta)

    logger.info(
        "Transcribiendo: %s (modelo=%s, device=%s, language=%s)",
        Path(ruta_a_transcribir).name, modelo, device, language or "auto"
    )

    # --- Paso 2: Detectar dispositivo ---
    if device == "auto":
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
            logger.info("Dispositivo detectado: %s", device)
        except ImportError:
            device = "cpu"

    # --- Paso 3: Transcribir ---
    try:
        model = faster_whisper.WhisperModel(
            modelo, device=device, compute_type=compute_type
        )

        segments, info = model.transcribe(
            ruta_a_transcribir,
            beam_size=beam_size,
            language=language,
            word_timestamps=word_timestamps,
            vad_filter=vad_filter,
            vad_parameters=vad_parameters,
            condition_on_previous_text=condition_on_previous_text,
            no_speech_threshold=no_speech_threshold,
            compression_ratio_threshold=compression_ratio_threshold,
            log_prob_threshold=log_prob_threshold,
        )

        logger.info(
            "Idioma detectado: %s (probabilidad: %.2f)",
            info.language, info.language_probability
        )

        resultado = []
        for segment in segments:
            seg_dict = {
                "inicio": segment.start,
                "fin": segment.end,
                "texto": segment.text.strip(),
            }
            if incluir_metricas:
                seg_dict["promedio_logprob"] = float(getattr(segment, "avg_logprob", 0.0))
                seg_dict["no_hay_habla_prob"] = float(getattr(segment, "no_speech_prob", 0.0))
                seg_dict["ratio_compresion"] = float(getattr(segment, "compression_ratio", 0.0))
            resultado.append(seg_dict)

        logger.info("Transcripción completa: %d segmentos", len(resultado))
        return resultado, info

    except Exception as e:
        logger.error("Error en transcripción: %s", e)
        raise RuntimeError(f"Fallo la transcripción de {ruta_audio}") from e

    finally:
        # Limpiar archivo de audio temporal si se generó
        if audio_temporal and Path(audio_temporal).exists():
            try:
                Path(audio_temporal).unlink()
                # Intentar limpiar directorio temporal también
                parent = Path(audio_temporal).parent
                if "flujos_audio_" in str(parent):
                    parent.rmdir()
            except Exception:
                pass


# ── Filtrado por confianza / detección de alucinaciones ──────────────────────
# Umbrales por defecto. La combinación busca texto "asentado" (avg_logprob alto),
# sin repetición (compression_ratio bajo) y que Whisper no haya marcado "no habla".

UMBRAL_LOGPROB = -0.8
UMBRAL_NO_HABLA = 0.6
UMBRAL_COMPRESION = 2.4
MIN_DURACION_ACEPTADA_S = 1.5


def filtrar_segmentos_confiables(
    segmentos: list[dict],
    logprob_umbral: float = UMBRAL_LOGPROB,
    no_habla_umbral: float = UMBRAL_NO_HABLA,
    compresion_umbral: float = UMBRAL_COMPRESION,
    min_duracion_s: float = MIN_DURACION_ACEPTADA_S,
) -> list[dict]:
    """
    Filtra los segmentos cuyo texto parece una alucinación de Whisper
    (sobre ruido/silencio). Devuelve solo los segmentos confiables.

    Cada segmento debe tener las métricas `promedio_logprob`,
    `no_hay_habla_prob` y `ratio_compresion` (generadas con
    `transcribir_audio(..., incluir_metricas=True)`).

    Criterio de aceptación (todos a la vez):
      - promedio_logprob >= logprob_umbral          (texto confiado/inteligible)
      - no_hay_habla_prob  <  no_habla_umbral       (Whisper NO cree que sea silencio)
      - ratio_compresion   <  compresion_umbral     (texto NO repetitivo)
      - duración del segmento >= min_duracion_s     (no aceptar blips de 0.x s)

    Si un segmento no tiene métricas (vino sin `incluir_metricas`), se conserva
    (no se puede evaluar → no se descarta por falta de datos).
    """
    confiables = []
    for sg in segmentos:
        inicio = sg.get("inicio", 0)
        fin = sg.get("fin", 0)
        texto = (sg.get("texto") or "").strip()
        if not texto:
            continue
        if "promedio_logprob" in sg:
            lp = sg.get("promedio_logprob")
            ns = sg.get("no_hay_habla_prob")
            cr = sg.get("ratio_compresion")
            if lp is not None and lp < logprob_umbral:
                continue
            if ns is not None and ns >= no_habla_umbral:
                continue
            if cr is not None and cr >= compresion_umbral:
                continue
        if (fin - inicio) < min_duracion_s:
            continue
        confiables.append(sg)
    return confiables


def clasificar_estado(segmentos: list[dict]) -> str:
    """
    Clasifica el resultado de una transcripción según tenga o no texto
    confiable tras filtrar alucinaciones.

    Estados posibles:
      - "ok":        hay >=1 segmento confiable (voz real + texto asentado).
      - "sin_voz":   VAD no arrojó segmentos, o los arrojados no pasan el
                     filtro de confianza → audio de ruido/silencio sin habla
                     útil (tu intuición: si Whisper no lo oyó, un humano tampoco).
      - "dudosa":    hay segmentos pero TODOS quedaron descartados por duración
                     o métricas parciales; caso límite para revisión humana.

    Con VAD activado, un clip de solo ruido devuelve 0 segmentos → sin_voz.
    """
    if not segmentos:
        return "sin_voz"
    texto_total = " ".join(s.get("texto", "") for s in segmentos).strip()
    if not texto_total:
        return "sin_voz"
    confiables = filtrar_segmentos_confiables(segmentos)
    if confiables:
        return "ok"
    # Hay texto crudo pero nada pasa el filtro → el audio es de mala calidad.
    return "sin_voz"


def segmentos_a_srt(segmentos: list[dict], archivo_salida: str):
    """
    Convierte segmentos de transcripción a formato SRT (subtítulos).

    Args:
        segmentos: Lista de dicts con "inicio", "fin", "texto".
        archivo_salida: Ruta del archivo .srt a escribir.
    """
    def _formatear_ts(segundos: float) -> str:
        td = timedelta(seconds=segundos)
        total = td.total_seconds()
        h = int(total // 3600)
        m = int((total % 3600) // 60)
        s = total % 60
        return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")

    with open(archivo_salida, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segmentos, 1):
            inicio = _formatear_ts(seg["inicio"])
            fin = _formatear_ts(seg["fin"])
            f.write(f"{i}\n{inicio} --> {fin}\n{seg['texto']}\n\n")

    logger.info("SRT exportado: %s (%d líneas)", archivo_salida, len(segmentos))


def segmentos_a_txt(segmentos: list[dict], archivo_salida: str):
    """
    Exporta transcripción a texto plano (solo texto, sin timestamps).

    Args:
        segmentos: Lista de dicts con "inicio", "fin", "texto".
        archivo_salida: Ruta del archivo .txt a escribir.
    """
    with open(archivo_salida, "w", encoding="utf-8") as f:
        for seg in segmentos:
            f.write(seg["texto"] + "\n")

    logger.info("TXT exportado: %s", archivo_salida)


def segmentos_a_json(segmentos: list[dict], archivo_salida: str):
    """
    Exporta transcripción a JSON con timestamps.

    Args:
        segmentos: Lista de dicts con "inicio", "fin", "texto".
        archivo_salida: Ruta del archivo .json a escribir.
    """
    with open(archivo_salida, "w", encoding="utf-8") as f:
        json.dump(segmentos, f, ensure_ascii=False, indent=2)

    logger.info("JSON exportado: %s", archivo_salida)


def obtener_texto_completo(segmentos: list[dict]) -> str:
    """Concatena todos los segmentos en un solo string."""
    return " ".join(seg["texto"] for seg in segmentos)


# ---- CLI ----
if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Transcribir audio (o audio de video) a texto con faster-whisper"
    )
    parser.add_argument("entrada", help="Ruta al archivo de audio o video")
    parser.add_argument("--modelo", default="base", choices=MODELOS_WHISPER,
                        help="Tamaño del modelo whisper")
    parser.add_argument("--device", default="auto", choices=["cpu", "cuda", "auto"],
                        help="Dispositivo de cómputo")
    parser.add_argument("--language", default=None,
                        help="Código de idioma (ej: es, en). Por defecto auto-detecta")
    parser.add_argument("--no-extraer-audio", action="store_true",
                        help="No extraer audio de video (pasar directo a whisper)")
    parser.add_argument("--srt", help="Exportar a SRT (subtítulos)")
    parser.add_argument("--txt", help="Exportar a TXT (texto plano)")
    parser.add_argument("--json", help="Exportar a JSON")

    args = parser.parse_args()

    # Detectar si es video y avisar
    if es_archivo_video(args.entrada):
        if args.no_extraer_audio:
            print("ℹ️  Pasando video directamente a whisper (puede fallar según codec)")
        else:
            print("ℹ️  Se extraerá el audio del video automáticamente")

    segmentos, info = transcribir_audio(
        args.entrada,
        modelo=args.modelo,
        device=args.device,
        language=args.language,
        extraer_audio=not args.no_extraer_audio,
    )

    duracion = f"{segmentos[-1]['fin']:.1f}s" if segmentos else "0s"

    print(f"\n✅ Transcripción completada")
    print(f"   Idioma: {info.language} (prob: {info.language_probability:.2f})")
    print(f"   Segmentos: {len(segmentos)}")
    print(f"   Duración: {duracion}")
    print()

    if args.srt:
        segmentos_a_srt(segmentos, args.srt)
    if args.txt:
        segmentos_a_txt(segmentos, args.txt)
    if args.json:
        segmentos_a_json(segmentos, args.json)

    # Mostrar preview
    for seg in segmentos[:5]:
        print(f"  [{seg['inicio']:6.1f}s -> {seg['fin']:6.1f}s] {seg['texto']}")
    if len(segmentos) > 5:
        print(f"  ... y {len(segmentos) - 5} segmentos más")
