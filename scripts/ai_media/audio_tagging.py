#!/usr/bin/env python3
"""
audio_tagging.py — Reconoce sonidos ambientales en audios/videos (audio tagging).

Usa el modelo CED-mini de sherpa-onnx (527 clases de AudioSet, ~100 MB, int8)
para clasificar los sonidos de cada archivo de audio/video: tráfico, pájaros,
viento, agua, motores, voces, música, etc. Corre 100% local en CPU (RTF ~0.014,
es decir ~14x más rápido que tiempo real).

Pipeline por archivo:
    1. ffmpeg extrae el audio a WAV 16 kHz mono (en memoria, sin archivos temp).
    2. El audio se divide en ventanas de VENTANA_SECS (10 s) y se clasifica cada
       ventana con el modelo CED-mini.
    3. Las probabilidades se suman por etiqueta y se queda con el top-k global.
    4. Las etiquetas EN se traducen a ES con un glosario (las no mapeadas se
       dejan en inglés normalizadas).

El resultado se guarda en `media_metadata`:
    - clave `ia_keywords_sonido`: texto coma-separado de los sonidos en español
    - clave `ia_sonido_raw`: JSON con [{name, prob}] de las top-k etiquetas

Uso:
    python scripts/ai_media/audio_tagging.py                  # skip: solo pendientes
    python scripts/ai_media/audio_tagging.py --mode update    # re-procesa todos
    python scripts/ai_media/audio_tagging.py --mode replace   # limpia y regenera
    python scripts/ai_media/audio_tagging.py --dry-run        # previsualiza sin escribir
    python scripts/ai_media/audio_tagging.py --top-k 5        # 5 etiquetas (default)
    python scripts/ai_media/audio_tagging.py --modelo <onnx>  # otro modelo onnx
    python scripts/ai_media/audio_tagging.py --no-descargar   # no auto-descargar el modelo

Modos:
    skip    → audios/videos con archivo existente que aún NO tienen
              ia_keywords_sonido (default)
    update  → re-procesa TODOS los audios/videos con archivo existente
    replace → limpia ia_keywords_sonido/ia_sonido_raw existentes y regenera

Descarga automática del modelo:
    Si no existe model.int8.onnx en models/audio/, el script lo descarga solo
    desde GitHub Releases (asset oficial del proyecto sherpa-onnx) y lo extrae.
    Se puede deshabilitar con --no-descargar (útil en entornos sin internet).

Requisitos (ver AGENTS.md, sección "Dependencias / requisitos"):
    pip install onnxruntime sherpa-onnx
    (El modelo se descarga solo; no hace falta descargarlo a mano.)
"""

import argparse
import io
import json
import logging
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import wave

log = logging.getLogger(__name__)

# ── Modelo y constantes ──────────────────────────────────────────────────────
# Ruta al modelo CED-mini. Si no existe, se descarga automáticamente (ver
# _descargar_modelo). También se puede pasar --modelo con otra ruta al .onnx.
RUTA_MODELO_DEFAULT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "models", "audio", "sherpa-onnx-ced-mini-audio-tagging-2024-04-19",
)
ONNX_DEFAULT = os.path.join(RUTA_MODELO_DEFAULT, "model.int8.onnx")
LABELS_DEFAULT = os.path.join(RUTA_MODELO_DEFAULT, "class_labels_indices.csv")

# Claves en DB
CLAVE_SALIDA = "ia_keywords_sonido"      # texto ES coma-separado
CLAVE_RAW = "ia_sonido_raw"              # JSON con [{name, prob}]

# Parámetros del modelo
TOP_K_DEFAULT = 5            # etiquetas por media (plan_keywords.md etapa 3)
NUM_THREADS_DEFAULT = 4      # hilos de CPU para onnxruntime
VENTANA_SECS = 10            # duración de cada ventana de audio
MAX_VENTANAS = 30            # máx ventanas procesadas (cubre 300 s de audio)

# Umbral de probabilidad mínima para incluir una etiqueta en el resultado.
# Se aplica sobre la MEDIA POR VENTANA (ver _procesar_media), escala 0-1.
# Calibrado para este corpus (viaje terrestre BA→Tucumán 2025): habla/música
# promedian 0.49-0.81 por ventana; el ruido de baja confianza 0.06-0.17. Ver
# docs/plan_keywords.md §Sonido.
UMBRAL_PROB = 0.15

# ⚠️ EXCLUSIÓN ESPECÍFICA DE CORPUS (viaje terrestre Buenos Aires→Tucumán, 2025)
# Estas clases AudioSet son FALSOS POSITIVOS para este corpus: el viento/rodadura
# de la bici se clasifica como agua (clase 'boat, water vehicle': 51 detecciones,
# prob media ~0.10), como erupción, explosión o serpiente (siseo de viento). El
# viaje no incluye transporte acuático, volcanes, explosiones ni ofidios, por lo
# que estas etiquetas son ruido y se descartan ANTES de traducir.
# ANTES de incluir sonidos que sí puedan tener estas clases (p. ej. un cruce en
# ferry, una grabación en un lago, contenido volcánico o pirotécnico) REVISAR/
# ELIMINAR esta lista o usar el flag --incluir-excluidas. Es una decisión de
# DOMINIO, no del modelo. Calibrado con la lista "a" (validación): todas las
# dudosas resultaron falsos positivos; se excluyen las de frecuencia ≥ 3 que
# pasarían el corte por cantidad de la nube.
CLASES_EXCLUIDAS_POR_CORPUS: frozenset[str] = frozenset({
    "boat, water vehicle",
    "motorboat, speedboat",
    "rowboat, canoe, kayak",
    "sailboat, sailing ship",
    "ocean",
    "eruption",
    "explosion",
    "snake",
})

# Nombres del modelo vienen con mayúsculas ('Boat, Water vehicle'); se compara
# normalizado a minúsculas.
_CLASES_EXCLUIDAS_NORM: frozenset[str] = frozenset(
    c.lower() for c in CLASES_EXCLUIDAS_POR_CORPUS
)

# ── Descarga del modelo (si no existe) ───────────────────────────────────────
# El modelo CED-mini se descarga automáticamente desde GitHub Releases y se
# extrae en models/audio/. Solo ocurre si falta model.int8.onnx.
URL_MODELO = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "audio-tagging-models/sherpa-onnx-ced-mini-audio-tagging-2024-04-19.tar.bz2"
)


def _descargar_modelo(destino_dir: str) -> None:
    """
    Descarga y extrae el modelo CED-mini desde GitHub Releases.

    El asset es un .tar.bz2 que contiene model.int8.onnx, model.onnx,
    class_labels_indices.csv y test_wavs/. Se descarga a un archivo temporal,
    se extrae a una carpeta temporal y los archivos necesarios (model.int8.onnx
    y class_labels_indices.csv) se copian a `destino_dir`, sea cual sea la
    estructura de directorios que traiga el tar (puede venir anidado).

    Args:
        destino_dir: Carpeta donde dejar los archivos (RUTA_MODELO_DEFAULT).

    Raises:
        RuntimeError: si la descarga o la extracción fallan.
    """
    import tempfile

    os.makedirs(destino_dir, exist_ok=True)
    tmp_tar = None
    tmp_ext = os.path.join(tempfile.gettempdir(), "flujos_cedmini_extract")
    shutil.rmtree(tmp_ext, ignore_errors=True)
    os.makedirs(tmp_ext, exist_ok=True)
    try:
        log.info("  Descargando modelo CED-mini (~45 MB) desde GitHub Releases...")
        req = urllib.request.Request(URL_MODELO, headers={"User-Agent": "flujos"})
        with urllib.request.urlopen(req, timeout=300) as resp, \
                tempfile.NamedTemporaryFile(suffix=".tar.bz2", delete=False) as tmp:
            tmp_tar = tmp.name
            tam_total = resp.headers.get("Content-Length")
            tam_total = int(tam_total) if tam_total else None
            descargado = 0
            while True:
                bloque = resp.read(256 * 1024)
                if not bloque:
                    break
                tmp.write(bloque)
                descargado += len(bloque)
                if tam_total:
                    log.info("  Descarga: %d%% (%d MB / %d MB)",
                             descargado * 100 // tam_total,
                             descargado // (1024 * 1024),
                             tam_total // (1024 * 1024))
        log.info("  Descarga completa (%d MB). Extrayendo...",
                 descargado // (1024 * 1024))

        with tarfile.open(tmp_tar, "r:bz2") as tar:
            # filter="data" protege contra escritura fuera de la carpeta
            tar.extractall(tmp_ext, filter="data")

        # Buscar los archivos necesarios en cualquier profundidad dentro del tar
        onnx_src = _buscar_en(tmp_ext, "model.int8.onnx")
        csv_src = _buscar_en(tmp_ext, "class_labels_indices.csv")
        if not onnx_src or not csv_src:
            raise RuntimeError(
                "El modelo se descargó pero no se encontraron model.int8.onnx "
                "ni class_labels_indices.csv en el contenido descargado.")

        shutil.copy2(onnx_src, os.path.join(destino_dir, "model.int8.onnx"))
        shutil.copy2(csv_src, os.path.join(destino_dir, "class_labels_indices.csv"))
        # Copiar también model.onnx si existe (más preciso, ~40 MB) de forma opcional
        onnx_fp32 = _buscar_en(tmp_ext, "model.onnx")
        if onnx_fp32:
            shutil.copy2(onnx_fp32, os.path.join(destino_dir, "model.onnx"))
    except (urllib.error.URLError, OSError, tarfile.TarError, EOFError) as e:
        raise RuntimeError(f"Fallo la descarga/extracción del modelo: {e}")
    finally:
        shutil.rmtree(tmp_ext, ignore_errors=True)
        if tmp_tar and os.path.isfile(tmp_tar):
            try:
                os.unlink(tmp_tar)
            except OSError:
                pass

    # Validar que quedaron los archivos necesarios
    if not (os.path.isfile(os.path.join(destino_dir, "model.int8.onnx"))
            and os.path.isfile(os.path.join(destino_dir, "class_labels_indices.csv"))):
        raise RuntimeError("El modelo no quedó correctamente instalado en "
                           f"{destino_dir}.")
    log.info("  ✅ Modelo CED-mini listo en: %s", destino_dir)


def _buscar_en(raiz: str, nombre: str) -> str | None:
    """Devuelve la ruta de la primera coincidencia de `nombre` bajo `raiz`."""
    for dir_actual, _subdirs, archivos in os.walk(raiz):
        if nombre in archivos:
            return os.path.join(dir_actual, nombre)
    return None


def _resolver_modelo(onnx_path: str, labels_path: str,
                     no_descargar: bool = False) -> tuple[str, str]:
    """
    Resuelve la ruta del modelo, descargándolo automáticamente si falta.

    Si el usuario pasó un --modelo explícito que existe, se respeta. Si no
    existe (o no se pasó), se usa el por defecto y se descarga cuando hace falta
    (a menos que `no_descargar` esté activo).

    Args:
        onnx_path: Ruta al .onnx (default o uno dado con --modelo).
        labels_path: Ruta al CSV de etiquetas.
        no_descargar: Si es True, no descarga el modelo y lanza RuntimeError
                      si falta.

    Returns:
        (onnx_path resuelto, labels_path resuelto).

    Raises:
        RuntimeError: si falta el modelo y no se puede/no se debe descargar.
    """
    # --modelo explícito que existe → respetar tal cual
    if onnx_path != ONNX_DEFAULT:
        if os.path.isfile(onnx_path):
            return onnx_path, labels_path
        # --modelo explícito inexistente → advertir y volver al default
        if not no_descargar:
            log.warning("  El modelo --modelo no existe: %s. Usando el por defecto.", onnx_path)
        onnx_path = ONNX_DEFAULT
        labels_path = LABELS_DEFAULT

    # Falta el modelo por defecto → descargar (o fallar si --no-descargar)
    if not os.path.isfile(ONNX_DEFAULT) or not os.path.isfile(LABELS_DEFAULT):
        if no_descargar:
            raise RuntimeError(
                f"No existe el modelo en {RUTA_MODELO_DEFAULT} y --no-descargar está activo. "
                f"Descargalo manualmente o quitá --no-descargar.")
        log.info("  No existe el modelo CED-mini. Descargándolo automáticamente...")
        _descargar_modelo(RUTA_MODELO_DEFAULT)
        if not os.path.isfile(ONNX_DEFAULT):
            raise RuntimeError("La descarga del modelo no produjo model.int8.onnx.")

    return onnx_path, labels_path

# ── Glosario EN → ES ─────────────────────────────────────────────────────────
# Traducción de las etiquetas de AudioSet que pueden aparecer en el viaje.
# Las palabras/etiquetas no mapeadas se dejan en inglés (normalizadas).
GLOSARIO: dict[str, str] = {
    # Voz / habla
    "speech": "voz", "male speech": "voz masculina", "man speaking": "hombre hablando",
    "female speech": "voz femenina", "woman speaking": "mujer hablando",
    "child speech": "voz infantil", "kid speaking": "niño hablando",
    "conversation": "conversación", "narration": "narración", "monologue": "monólogo",
    "babbling": "balbuceo", "speech synthesizer": "voz sintetizada",
    "shout": "grito", "bellow": "grito fuerte", "yell": "grito",
    "screaming": "gritos", "whispering": "susurro", "laughter": "risas",
    "giggle": "risita", "chuckle": "risita", "belly laugh": "carcajada",
    "crying": "llanto", "sobbing": "sollozo", "baby cry": "llanto de bebé",
    "wail": "lamento", "moan": "gemido", "sigh": "suspiro",
    "singing": "canto", "choir": "coro", "chant": "canto ritual",
    "humming": "tarareo", "whistling": "silbido", "breathing": "respiración",
    "cough": "tos", "sneeze": "estornudo", "sniff": "resoplido",
    "applause": "aplausos", "cheering": "vítores", "crowd": "multitud",
    "children playing": "niños jugando", "children shouting": "niños gritando",
    "hubbub": "vocerío", "speech noise": "ruido de voces", "speech babble": "vocerío",
    "public space": "espacio público", "inside": "interior", "outside": "exterior",
    "large room or hall": "sala grande", "small room": "sala pequeña",

    # Naturaleza y ambiente
    "bird": "pájaro", "bird vocalization": "canto de pájaro", "bird call": "canto de pájaro",
    "bird song": "canto de pájaro", "bird flight": "vuelo de pájaro",
    "flapping wings": "aleteo", "chirp": "chirrido", "tweet": "pío",
    "crow": "cuervo", "caw": "graznido", "owl": "búho", "hoot": "ululato",
    "pigeon": "paloma", "dove": "paloma", "coo": "arrullo", "chicken": "gallina", "cluck": "cacareo",
    "rooster": "gallo", "crowing": "canto del gallo", "duck": "pato", "quack": "graznido",
    "goose": "ganso", "goose honk": "graznido de ganso", "turkey": "pavo",
    "gobble": "gluglú", "horse": "caballo", "neigh": "relincho", "whinny": "relincho",
    "cattle": "ganado", "moo": "mugido", "cowbell": "cencerro", "sheep": "oveja",
    "goat": "cabra", "bleat": "balido", "pig": "cerdo", "oink": "gruñido",
    "dog": "perro", "bark": "ladrido", "whimper": "gemido", "growling": "gruñido",
    "cat": "gato", "meow": "maullido", "purr": "ronroneo", "caterwaul": "maullido",
    "roaring cats": "rugido de felinos", "lions": "leones", "tigers": "tigres",
    "wolves": "lobos", "howl": "aullido", "wild animals": "animales salvajes",
    "farm animals": "animales de granja", "domestic animals": "animales domésticos",
    "pets": "mascotas", "working animals": "animales de trabajo",
    "insect": "insecto", "bee": "abeja", "buzz": "zumbido", "buzzer": "zumbador",
    "wasp": "avispa", "fly": "mosca", "housefly": "mosca", "mosquito": "mosquito",
    "cricket": "grillo", "frog": "rana", "croak": "croar", "snake": "serpiente",
    "hiss": "siseo", "whale vocalization": "canto de ballena",
    "water": "agua", "stream": "arroyo", "waterfall": "cascada", "rain": "lluvia",
    "raindrop": "gota de lluvia", "rain on surface": "lluvia sobre superficie",
    "thunder": "trueno", "thunderstorm": "tormenta", "ocean": "océano", "surf": "olas",
    "waves": "olas", "splash": "chapoteo", "trickle": "hilo de agua",
    "gurgling": "burbujeo", "pour": "verter", "drip": "goteo",
    "wind": "viento", "wind noise": "ruido de viento", "wind chime": "campanilla de viento",
    "rustling leaves": "susurro de hojas", "rustle": "susurro", "environmental noise": "ruido ambiental",
    "rural or natural": "entorno rural", "field recording": "grabación de campo",
    "silence": "silencio", "white noise": "ruido blanco", "pink noise": "ruido rosa",

    # Tráfico y vehículos
    "traffic noise": "ruido de tráfico", "roadway noise": "ruido de ruta",
    "road": "ruta", "vehicle": "vehículo", "motor vehicle": "vehículo motorizado",
    "car": "auto", "car passing by": "auto pasando", "truck": "camión",
    "bus": "colectivo", "motorcycle": "motocicleta", "bicycle": "bicicleta",
    "bicycle bell": "campana de bicicleta", "bell": "campana", "ding": "campanada",
    "jingle bell": "cascabel", "church bell": "campana de iglesia",
    "vehicle horn": "bocina de auto", "car horn": "bocina de auto",
    "honking": "bocinazos", "honk": "bocinazo", "horn": "bocina",
    "air horn": "bocina de aire", "truck horn": "bocina de camión",
    "train horn": "bocina de tren", "train": "tren", "train whistle": "silbato de tren",
    "train wheels squealing": "chirrido de ruedas de tren", "rail transport": "transporte ferroviario",
    "railroad car": "vagón de tren", "train wagon": "vagón de tren",
    "metro": "subte", "subway": "subte", "underground": "subte",
    "engine": "motor", "engine starting": "motor arrancando",
    "engine knocking": "golpeteo de motor", "idling": "motor en ralentí",
    "revving": "aceleración", "accelerating": "acelerando", "vroom": "rugido de motor",
    "aircraft": "avión", "airplane": "avión", "aircraft engine": "motor de avión",
    "jet engine": "motor a reacción", "helicopter": "helicóptero",
    "emergency vehicle": "vehículo de emergencia", "siren": "sirena",
    "police car": "patrullero", "fire engine": "bomberos", "fire truck": "camión de bomberos",
    "ambulance": "ambulancia", "car alarm": "alarma de auto", "alarm": "alarma",
    "fire alarm": "alarma de incendio", "smoke alarm": "alarma de humo",
    "ice cream truck": "camión de helados", "tire squeal": "chirrido de neumáticos",
    "skidding": "derrape", "reversing beeps": "pitido de retroceso",
    "boat": "barco", "water vehicle": "vehículo acuático", "motorboat": "lancha",
    "speedboat": "lancha rápida", "ship": "barco", "sailboat": "velero",
    "sailing ship": "velero", "kayak": "kayak", "canoe": "canoa", "rowboat": "bote de remos",
    "foghorn": "sirena de niebla",

    # Música
    "music": "música", "musical instrument": "instrumento musical",
    "instrument": "instrumento", "guitar": "guitarra", "acoustic guitar": "guitarra acústica",
    "electric guitar": "guitarra eléctrica", "bass guitar": "bajo",
    "piano": "piano", "electric piano": "piano eléctrico", "keyboard": "teclado",
    "violin": "violín", "cello": "violonchelo", "double bass": "contrabajo",
    "fiddle": "violín", "trumpet": "trompeta", "trombone": "trombón",
    "saxophone": "saxofón", "clarinet": "clarinete", "flute": "flauta",
    "harmonica": "armónica", "bagpipes": "gaita", "accordion": "acordeón",
    "drums": "batería", "drum": "tambor", "drum kit": "batería", "snare drum": "caja",
    "bass drum": "bombo", "cymbal": "platillo", "percussion": "percusión",
    "xylophone": "xilófono", "marimba": "marimba", "organ": "órgano",
    "harp": "arpa", "banjo": "banjo", "mandolin": "mandolina", "ukulele": "ukulele",
    "sitar": "sitar", "didgeridoo": "didgeridoo", "synthesizer": "sintetizador",
    "singing bowl": "cuenco tibetano", "opera": "ópera", "choir": "coro",
    "rock music": "rock", "pop music": "pop", "jazz": "jazz", "blues": "blues",
    "country": "música country", "folk music": "música folclórica", "reggae": "reggae",
    "funk": "funk", "disco": "disco", "techno": "techno", "house music": "house",
    "electronic music": "música electrónica", "electronic dance music": "música electrónica",
    "classical music": "música clásica", "orchestra": "orquesta",
    "background music": "música de fondo", "theme music": "música temática",
    "soundtrack music": "música de película", "music of latin america": "música latinoamericana",
    "salsa music": "salsa", "vocal music": "música vocal", "christmas music": "música navideña",
    "lullaby": "canción de cuna", "happy music": "música alegre", "sad music": "música triste",

    # Sonidos de objetos / actividades
    "footsteps": "pasos", "walk": "caminar", "footstep": "paso",
    "run": "correr", "clapping": "aplausos", "finger snapping": "chasquido de dedos",
    "fart": "pedo", "burping": "eructo", "chewing": "masticar",
    "food": "comida", "cutlery": "cubiertos", "dishes": "platos", "glass": "vidrio",
    "pots": "ollas", "sink": "pileta", "toilet flush": "descarga de inodoro",
    "door": "puerta", "doorbell": "timbre", "knock": "golpe en la puerta",
    "sliding door": "puerta corrediza", "telephone": "teléfono",
    "telephone bell ringing": "teléfono sonando", "ringtone": "ringtone",
    "dial tone": "tono de marcado", "busy signal": "señal de ocupado",
    "clock": "reloj", "tick": "tic tac", "watch": "reloj",
    "camera": "cámara", "lens reflex camera": "cámara réflex",
    "computer keyboard": "teclado de computadora", "typing": "tecleo",
    "typewriter": "máquina de escribir", "printer": "impresora",
    "cash register": "caja registradora", "keyboard": "teclado",
    "keys jangling": "llaves", "zipper": "cierre", "clothing": "ropa",
    "chop": "cortar", "chopping": "picar", "slicing": "rebanar",
    "frying": "freír", "boiling": "hervir", "microwave oven": "microondas",
    "blender": "licuadora", "vacuum cleaner": "aspiradora", "hair dryer": "secador de pelo",
    "lawn mower": "cortacésped", "chainsaw": "motosierra", "drill": "taladro",
    "power tool": "herramienta eléctrica", "hammer": "martillo", "sawing": "aserrando",
    "jackhammer": "martillo neumático", "explosion": "explosión", "gunshot": "disparo",
    "gunfire": "disparos", "firecracker": "petardo", "fireworks": "fuegos artificiales",
    "machine gun": "ametralladora", "cap gun": "pistola de juguete",
    "mechanical fan": "ventilador", "air conditioning": "aire acondicionado",
    "electric toothbrush": "cepillo eléctrico", "electric razor": "afeitadora eléctrica",
    "shaver": "afeitadora", "sewing machine": "máquina de coser",
    "dentist's drill": "torno dental", "dental drill": "torno dental",
    "toothbrush": "cepillo de dientes", "writing": "escribir",
    "shuffling cards": "barajar cartas", "crumpling": "arrugar papel",
    "tearing": "rasgar", "crinkling": "crujido", "snap": "chasquido",
    "click": "clic", "clicking": "clics", "clack": "chasquido", "clank": "golpeteo",
    "clang": "golpe metálico", "clatter": "traqueteo", "crash": "choque",
    "smash": "estruendo", "bang": "golpe", "boom": "estallido", "pop": "pop",
    "thud": "golpe sordo", "thump": "golpe", "thunk": "golpe", "slap": "palmada",
    "whack": "golpe", "smack": "golpe", "punch": "puñetazo", "thwack": "golpe",
    "squeak": "chirrido", "squeal": "chillido", "squish": "squish", "squawk": "graznido",
    "splash": "chapoteo", "splatter": "salpicadura", "spray": "spray",
    "gush": "chorro", "slosh": "chapoteo", "swish": "frufrú", "swoosh": "frufrú",
    "whir": "zumbido", "whirring": "zumbido", "hum": "zumbido", "mains hum": "zumbido de red",
    "buzz": "zumbido", "purr": "ronroneo", "vibration": "vibración", "rumble": "retumbo",
    "throbbing": "pulsación", "pulse": "pulso", "beep": "bip", "bleep": "bip",
    "ping": "ping", "pong": "pong", "tone": "tono", "sine wave": "onda sinusoidal",
    "chirp tone": "tono chirrido", "electronic tuner": "afinador electrónico",
    "dtmf": "tonos DTMF", "dialing": "marcado", "sidetone": "tono de retorno",
    "echo": "eco", "reverberation": "reverberación", "distortion": "distorsión",
    "effects unit": "unidad de efectos", "chorus effect": "efecto chorus",
    "music": "música", "song": "canción", "musical": "musical",
    "radio": "radio", "television": "televisión", "video game music": "música de videojuego",
    "sampler": "sampler", "drum machine": "caja de ritmos", "beatboxing": "beatbox",
    "rapping": "rapeo", "scratching": "scratch", "turntable": "tocadiscos",

    # Otras
    "noise": "ruido", "sound effect": "efecto de sonido", "static": "estática",
    "cacophony": "cacofonía", "pink noise": "ruido rosa", "white noise": "ruido blanco",
    "vibration": "vibración", "heartbeat": "latido", "heart sounds": "latidos",
    "heart murmur": "soplo cardíaco", "stomach rumble": "ruido de estómago",
    "gargling": "gárgaras", "hiccup": "hipo", "snoring": "ronquido",
    "wheeze": "sibilancia", "gasp": "jadeo", "pant": "jadeo", "snort": "bufido",
    "grunt": "gruñido", "groan": "gemido", "eructation": "eructo",
    "farm animals": "animales de granja", "livestock": "ganado", "bovinae": "bovinos",
    "canidae": "cánidos", "mice": "ratones", "rats": "ratas", "rodents": "roedores",
    "housefly": "mosca", "mosquito": "mosquito", "birds": "pájaros", "fowl": "aves de corral",
    "animal": "animal",

    # Clases AudioSet sin traducir detectadas en auditoría de la DB
    # (docs/plan_keywords.md etapa 3, 2026-08). Incluye préstamos léxicos
    # válidos en español (flamenco, tabla, mantra) mapeados como identidad
    # para dejar explícito que no son fugas de inglés.
    "eruption": "erupción", "steam": "vapor",
    "steam whistle": "silbato de vapor", "wood": "madera",
    "arrow": "flecha", "skateboard": "monopatín",
    "clip-clop": "trote de caballo", "bow-wow": "ladrido", "yip": "ganido",
    "whimper (dog)": "gemido",
    "dove": "paloma",
    "flap": "aleteo",
    "plucked string instrument": "instrumento de cuerda pulsada",
    "brass instrument": "instrumento de viento metal",
    "wind instrument": "instrumento de viento",
    "woodwind instrument": "instrumento de viento madera",
    "steel guitar": "guitarra hawaiana", "slide guitar": "guitarra slide",
    "strum": "rasgueo",
    "electronic organ": "órgano electrónico", "hammond organ": "órgano hammond",
    "chime": "carrillón", "tick-tock": "tic tac", "toot": "pitido",
    "punk rock": "punk", "rock and roll": "rock and roll",
    "soul music": "música soul", "gospel music": "música gospel",
    "new-age music": "música new age", "flamenco": "flamenco",
    "tabla": "tabla", "mantra": "mantra",
    "crackle": "crujido", "sizzle": "siseo", "whoosh": "frufrú",
    "burst": "estallido", "boing": "rebote", "scrape": "raspado",
    "rub": "frotado", "patter": "repiqueteo", "clickety-clack": "traqueteo",
    "propeller": "hélice", "airscrew": "hélice",
    "fixed-wing aircraft": "avión",
    "race car": "auto de carreras", "auto racing": "carrera de autos",
    "artillery fire": "disparos",
}

# Traducciones de palabras sueltas para etiquetas compuestas no cubiertas
PALABRAS: dict[str, str] = {
    "animal": "animal", "domestic": "doméstico", "wild": "salvaje", "large": "grande",
    "small": "pequeño", "man": "hombre", "woman": "mujer", "male": "masculino",
    "female": "femenino", "kid": "niño", "child": "niño", "children": "niños",
    "speaking": "hablando", "talk": "hablar", "speech": "voz", "noise": "ruido",
    "music": "música", "song": "canción", "singing": "canto", "vocal": "vocal",
    "voice": "voz", "sound": "sonido", "motor": "motor", "vehicle": "vehículo",
    "road": "ruta", "traffic": "tráfico", "car": "auto", "truck": "camión",
    "bus": "colectivo", "train": "tren", "airplane": "avión", "aircraft": "avión",
    "water": "agua", "rain": "lluvia", "wind": "viento", "bird": "pájaro",
    "footsteps": "pasos", "walk": "caminar", "running": "corriendo", "run": "correr",
    "applause": "aplausos", "crowd": "multitud", "laughter": "risas", "cry": "llanto",
    "fire": "fuego", "explosion": "explosión", "gunshot": "disparo", "gunfire": "disparos",
    "engine": "motor", "bicycle": "bicicleta", "bike": "bicicleta", "bell": "campana",
    "horn": "bocina", "siren": "sirena", "alarm": "alarma", "clock": "reloj",
    "ticking": "tic tac", "tick": "tic", "door": "puerta", "knock": "golpe",
    "telephone": "teléfono", "phone": "teléfono", "computer": "computadora",
    "keyboard": "teclado", "typing": "tecleo", "drill": "taladro", "saw": "sierra",
    "hammer": "martillo", "power": "eléctrico", "tool": "herramienta",
    "rain": "lluvia", "storm": "tormenta", "thunder": "trueno", "wind": "viento",
    "air": "aire", "breath": "respiración", "breathing": "respiración",
    "cough": "tos", "sneeze": "estornudo", "snore": "ronquido", "snoring": "ronquido",
    "crying": "llanto", "screaming": "gritos", "shouting": "gritos", "yelling": "gritos",
    "cheering": "vítores", "applauding": "aplaudiendo", "clapping": "aplausos",
    "whistling": "silbido", "whistle": "silbato", "singing": "canto", "humming": "tarareo",
    "eating": "comer", "chewing": "masticar", "drinking": "beber", "food": "comida",
    "cooking": "cocinar", "frying": "freír", "boiling": "hervir",
    "washing": "lavar", "cleaning": "limpiar", "sweeping": "barrer",
    "vacuum": "aspiradora", "lawn": "césped", "mower": "cortacésped",
    "chainsaw": "motosierra", "electric": "eléctrico", "electrical": "eléctrico",
    "mechanical": "mecánico", "machine": "máquina", "engine": "motor",
    "vehicle": "vehículo", "automobile": "automóvil", "sport": "deporte",
    "game": "juego", "play": "jugar", "music": "música", "dance": "baile",
    "dancing": "bailando", "party": "fiesta", "celebration": "celebración",
    "animal": "animal", "animals": "animales", "pet": "mascota", "pets": "mascotas",
    "dog": "perro", "cat": "gato", "horse": "caballo", "cow": "vaca", "sheep": "oveja",
    "goat": "cabra", "pig": "cerdo", "chicken": "gallina", "rooster": "gallo",
    "duck": "pato", "bird": "pájaro", "birds": "pájaros", "insect": "insecto",
    "bee": "abeja", "fly": "mosca", "mosquito": "mosquito", "cricket": "grillo",
    "frog": "rana", "snake": "serpiente", "fish": "pez", "insects": "insectos",
    "noise": "ruido", "sound": "sonido", "loud": "fuerte", "quiet": "tranquilo",
    "high": "agudo", "low": "grave", "frequency": "frecuencia", "medium": "medio",
    "light": "liviano", "heavy": "pesado", "outside": "exterior", "inside": "interior",
    "natural": "natural", "rural": "rural", "urban": "urbano", "city": "ciudad",
    "town": "pueblo", "village": "pueblo", "street": "calle", "road": "ruta",
    "highway": "autopista", "parking": "estacionamiento", "garage": "garaje",
    "building": "edificio", "house": "casa", "home": "casa", "room": "habitación",
    "kitchen": "cocina", "bathroom": "baño", "bedroom": "dormitorio",
    "restaurant": "restaurante", "bar": "bar", "cafe": "café", "store": "tienda",
    "shop": "tienda", "market": "mercado", "hospital": "hospital", "school": "escuela",
    "church": "iglesia", "airport": "aeropuerto", "station": "estación",
    "bus station": "terminal de colectivos", "metro": "subte", "subway": "subte",
    "rail": "ferrocarril", "railway": "ferrocarril", "track": "vía",
    "bicycle": "bicicleta", "motorcycle": "motocicleta", "scooter": "moto",
    "bike": "bicicleta", "pedal": "pedal", "cycling": "ciclismo",
    "sports": "deportes", "outdoor": "al aire libre", "camping": "campamento",
    "tent": "carpa", "hiking": "senderismo", "walking": "caminata", "running": "corriendo",
    "jogging": "trote", "mountain": "montaña", "hill": "colina", "valley": "valle",
    "river": "río", "lake": "lago", "sea": "mar", "ocean": "océano", "beach": "playa",
    "forest": "bosque", "woods": "bosque", "field": "campo", "farm": "granja",
    "desert": "desierto", "sky": "cielo", "sun": "sol", "wind": "viento",
    "cold": "frío", "hot": "calor", "warm": "cálido", "weather": "clima",
    "tap": "canilla", "faucet": "grifo", "van": "camioneta", "synthesizer": "sintetizador",
    "microphone": "micrófono", "whistle": "silbato", "rattle": "sonajero",
    "or": "o", "and": "y", "manmade": "artificial", "mammal": "mamífero",
    "etc": "etcétera",
}

# Palabras que se descartan del resultado final (poco informativas solas)
PALABRAS_BASURA = {
    "inside", "outside", "music", "noise", "sound", "sound effect", "effects unit",
    "vibration", "pulse", "beep", "tone", "static", "echo", "field recording",
    "environmental noise", "public space", "small room", "large room or hall",
    "etcétera",
}


def _traducir_etiqueta(etiqueta_en: str) -> str:
    """
    Traduce una etiqueta de AudioSet a español usando el glosario.

    AudioSet agrupa varios conceptos en una etiqueta separados por comas
    (ej: "Male speech, man speaking") y usa paréntesis como aclaración
    (ej: "Zipper (clothing)", "Roaring cats (lions, tigers)"). Se normalizan
    los paréntesis a partes separadas por coma para que cada componente se
    traduzca con el glosario (evita fugas de inglés tipo "zipper (clothing").
    Primero busca cada parte completa; si no está, traduce palabra por palabra
    con PALABRAS (las palabras no mapeadas se dejan en inglés).

    Args:
        etiqueta_en: Etiqueta en inglés (ej: "Bird vocalization").

    Returns:
        Etiqueta traducida al español (o normalizada en inglés si no hay mapeo).
    """
    etiqueta = etiqueta_en.strip().lower()
    # Aclaraciones entre paréntesis → partes independientes (ver docstring)
    etiqueta = etiqueta.replace("(", ", ").replace(")", "")

    # Dividir por comas (cada concepto se traduce por separado)
    partes_originales = [p.strip() for p in etiqueta.split(",") if p.strip()]
    traducidas: list[str] = []

    for parte in partes_originales:
        if parte in GLOSARIO:
            traducidas.append(GLOSARIO[parte])
            continue

        # Traducir palabra por palabra
        palabras = parte.split()
        partes: list[str] = []
        usadas = 0
        for p in palabras:
            limpia = p.strip(" .,;:()[]{}'\"-")
            if limpia in PALABRAS:
                partes.append(PALABRAS[limpia])
                usadas += 1
            else:
                partes.append(limpia)
        if usadas == 0:
            traducidas.append(parte)  # nada que traducir, original
        else:
            traducidas.append(" ".join(partes))

    return ", ".join(traducidas)


def _limpiar_etiqueta(etiqueta: str) -> str:
    """Normaliza una etiqueta para el resultado (espacios, comas, puntuación)."""
    e = etiqueta.strip().lower()
    e = e.replace("_", " ")
    e = " ".join(e.split())
    e = e.strip(" ,.;:()[]{}'\"-")
    return e


def _es_basura(etiqueta: str) -> bool:
    """Determina si una etiqueta traducida es poco informativa."""
    if not etiqueta:
        return True
    if etiqueta in PALABRAS_BASURA:
        return True
    if len(etiqueta) < 3:
        return True
    return False


def _extraer_audio_ffmpeg(ruta_archivo: str) -> tuple[list[float], int]:
    """
    Extrae el audio de un archivo a WAV 16 kHz mono usando ffmpeg (en memoria).

    Args:
        ruta_archivo: Ruta absoluta al archivo de audio/video.

    Returns:
        Tupla (samples, rate) con samples en floats [-1, 1] y rate=16000.

    Raises:
        RuntimeError: si ffmpeg falla o no hay audio.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg no está en el PATH. Instalalo o agrégalo al PATH.")

    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", ruta_archivo,
        "-ac", "1", "-ar", "16000", "-f", "wav", "pipe:1",
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
    if proc.returncode != 0 or not proc.stdout:
        err = proc.stderr.decode("utf-8", errors="replace").strip()[:300]
        raise RuntimeError(f"ffmpeg falló: {err}")

    # Parsear WAV desde memoria
    try:
        with wave.open(io.BytesIO(proc.stdout), "rb") as w:
            rate = w.getframerate()
            nch = w.getnchannels()
            n = w.getnframes()
            data = w.readframes(n)
    except (wave.Error, EOFError) as e:
        raise RuntimeError(f"No se pudo parsear el WAV extraído: {e}")

    if n == 0:
        raise RuntimeError("El audio extraído está vacío.")

    fmt = f"<{n * nch}h"
    try:
        vals = struct.unpack(fmt, data)
    except struct.error:
        # data puede ser impar por padding; recortar
        usable = len(data) - (len(data) % 2)
        vals = struct.unpack(f"<{usable // 2}h", data[:usable])

    if nch > 1:
        vals = vals[0::nch]

    samples = [v / 32768.0 for v in vals]
    return samples, rate


def _ventanas_de_audio(samples: list[float], rate: int) -> list[list[float]]:
    """
    Divide el audio en ventanas de VENTANA_SECS segundos (máx MAX_VENTANAS).

    Args:
        samples: Muestras en floats [-1, 1].
        rate: Tasa de muestreo (16000).

    Returns:
        Lista de ventanas (cada una con sus samples). Si el audio es más corto
        que una ventana, devuelve una única ventana con el audio completo.
    """
    tam_ventana = rate * VENTANA_SECS
    total = len(samples)
    if total <= tam_ventana:
        return [samples]

    n_ventanas = min(MAX_VENTANAS, -(-total // tam_ventana))  # ceil
    ventanas = []
    for i in range(n_ventanas):
        inicio = i * tam_ventana
        fin = min(inicio + tam_ventana, total)
        ventanas.append(samples[inicio:fin])
    return ventanas


def _clasificar_ventana(tagging, samples: list[float], rate: int) -> list[tuple[str, float]]:
    """
    Clasifica una ventana de audio con el modelo de audio tagging.

    Args:
        tagging: instancia sherpa_onnx.AudioTagging ya configurada.
        samples: muestras en floats [-1, 1].
        rate: tasa de muestreo.

    Returns:
        Lista de (etiqueta_en, probabilidad) ordenada por probabilidad desc.
    """
    stream = tagging.create_stream()
    stream.accept_waveform(rate, samples)
    eventos = tagging.compute(stream)
    return [(ev.name, float(ev.prob)) for ev in eventos]


def _procesar_media(tagging, ruta_archivo: str, top_k: int,
                    umbral: float = UMBRAL_PROB,
                    excluir: frozenset[str] = CLASES_EXCLUIDAS_POR_CORPUS) -> list[tuple[str, float]]:
    """
    Procesa un archivo completo: extrae audio, clasifica ventanas y agrega.

    Las probabilidades por etiqueta se suman entre ventanas y se normalizan a
    MEDIA POR VENTANA (dividido por el número de ventanas procesadas), de modo
    que queden en escala 0-1 y sean comparables entre medios de distinta
    duración (antes se acumulaba la suma, que inflaba las probs de los medios
    largos y hacía inútil el umbral).

    Se aplican el umbral de probabilidad (sobre la media) y la exclusión de
    clases específicas del corpus.

    Args:
        tagging: instancia sherpa_onnx.AudioTagging.
        ruta_archivo: Ruta absoluta al archivo.
        top_k: cantidad de etiquetas a devolver.
        umbral: probabilidad mínima por ventana (0-1) para incluir la etiqueta.
        excluir: clases AudioSet a descartar por decisión de corpus.

    Returns:
        Lista de (etiqueta_en, prob_media) ordenada por prob desc, ya sin las
        clases excluidas.
    """
    samples, rate = _extraer_audio_ffmpeg(ruta_archivo)
    ventanas = _ventanas_de_audio(samples, rate)

    acumulado: dict[str, float] = {}
    for i, ventana in enumerate(ventanas):
        try:
            resultados = _clasificar_ventana(tagging, ventana, rate)
        except Exception as e:
            log.debug("  Error clasificando ventana %d: %s", i, e)
            continue
        for etiqueta, prob in resultados:
            acumulado[etiqueta] = acumulado.get(etiqueta, 0.0) + prob

    if not acumulado:
        return []

    n_ventanas = max(1, len(ventanas))
    ordenados = sorted(
        ((et, prob / n_ventanas) for et, prob in acumulado.items()),
        key=lambda x: -x[1],
    )

    # Exclusión por corpus ANTES del umbral/traducción (clases AudioSet crudas,
    # comparadas case-insensitive porque el modelo las devuelve con mayúsculas).
    if excluir:
        excluir_norm = frozenset(c.lower() for c in excluir)
        ordenados = [(et, p) for et, p in ordenados if et.lower() not in excluir_norm]

    # Umbral de confianza sobre la media por ventana
    ordenados = [(et, p) for et, p in ordenados if p >= umbral]

    return ordenados[:top_k]


def _query_segun_modo(mode: str) -> str:
    """
    Devuelve la query para listar los medios a procesar según el modo.

    skip    → audios/videos con archivo existente y SIN ia_keywords_sonido
    update  → audios/videos con archivo existente (todos)
    replace → igual que update (el clean va aparte)
    """
    base = """
        SELECT m.id, m.filename_original, m.type, m.filepath_absoluto,
               m.duration_secs
        FROM media m
        WHERE m.type IN ('audio', 'video')
          AND m.filepath_absoluto IS NOT NULL
          AND m.filepath_absoluto != ''
    """
    if mode == "skip":
        return base + """
            AND NOT EXISTS (
                SELECT 1 FROM media_metadata out_
                WHERE out_.media_id = m.id AND out_.key = ?
            )
            ORDER BY m.id
        """
    return base + " ORDER BY m.id"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Reconoce sonidos ambientales en audios/videos (audio tagging con "
                    "sherpa-onnx CED-mini) y guarda las etiquetas en media_metadata "
                    "con clave 'ia_keywords_sonido'.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", default=None, help="Ruta a la base de datos (default: db/flujos.db)")
    parser.add_argument("--mode", default="skip", choices=["skip", "update", "replace"],
                        help="skip: solo sin tags (default) | update: todos | replace: limpia y regenera")
    parser.add_argument("--modelo", default=None,
                        help=f"Ruta al .onnx del modelo (default: CED-mini en models/audio/, ruta={ONNX_DEFAULT})")
    parser.add_argument("--labels", default=None,
                        help=f"Ruta al CSV de etiquetas (default: {LABELS_DEFAULT})")
    parser.add_argument("--top-k", type=int, default=TOP_K_DEFAULT,
                        help=f"Etiquetas por media (default: {TOP_K_DEFAULT})")
    parser.add_argument("--umbral-prob", type=float, default=UMBRAL_PROB,
                        help=f"Probabilidad mínima por ventana (0-1) para incluir una "
                             f"etiqueta (default: {UMBRAL_PROB}). Ver docs/plan_keywords.md §Sonido.")
    parser.add_argument("--incluir-excluidas", action="store_true",
                        help="NO excluir las clases específicas del corpus "
                             "(CLASES_EXCLUIDAS_POR_CORPUS, p. ej. transporte acuático). "
                             "Usar solo si el contenido puede tenerlas.")
    parser.add_argument("--threads", type=int, default=NUM_THREADS_DEFAULT,
                        help=f"Hilos de CPU para el modelo (default: {NUM_THREADS_DEFAULT})")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limitar a N registros (para pruebas)")
    parser.add_argument("--no-descargar", action="store_true",
                        help="No descargar el modelo automáticamente si falta (falla con error)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Previsualizar sin escribir")
    parser.add_argument("--verbose", action="store_true", help="Log detallado")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Resolver DB (permite ejecución standalone desde cualquier directorio)
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from db.util import abrir, resolver_db

    db_path = resolver_db(args.db)
    if not os.path.isfile(db_path):
        log.error("No existe la DB: %s", db_path)
        sys.exit(1)

    conn = abrir(db_path)
    conn.row_factory = sqlite3.Row

    # ── Modo replace: limpiar claves de salida antes de regenerar ──
    if args.mode == "replace":
        conn.execute(
            "DELETE FROM media_metadata WHERE key IN (?, ?)",
            (CLAVE_SALIDA, CLAVE_RAW),
        )
        conn.commit()
        log.info("  [replace] Limpiado %s / %s de la DB.", CLAVE_SALIDA, CLAVE_RAW)

    # ── Cargar modelo sherpa-onnx ──
    onnx_path = args.modelo or ONNX_DEFAULT
    labels_path = args.labels or LABELS_DEFAULT
    try:
        onnx_path, labels_path = _resolver_modelo(
            onnx_path, labels_path, no_descargar=args.no_descargar)
    except RuntimeError as e:
        log.error("  %s", e)
        conn.close()
        sys.exit(1)

    try:
        import sherpa_onnx
    except ImportError:
        log.error("Falta sherpa_onnx. Instalalo: pip install onnxruntime sherpa-onnx")
        conn.close()
        sys.exit(1)

    config = sherpa_onnx.AudioTaggingConfig(
        model=sherpa_onnx.AudioTaggingModelConfig(ced=onnx_path, num_threads=args.threads),
        labels=labels_path,
        top_k=args.top_k,
    )
    tagging = sherpa_onnx.AudioTagging(config)
    log.info("  Modelo cargado: %s (threads=%d, top_k=%d)",
             os.path.basename(onnx_path), args.threads, args.top_k)

    # ── Listar medios ──
    query = _query_segun_modo(args.mode)
    params: list = []
    if args.mode == "skip":
        params = [CLAVE_SALIDA]
    rows = conn.execute(query, params).fetchall()
    if args.limit:
        rows = rows[:args.limit]

    if not rows:
        print("  No hay audios/videos con archivo existente para procesar.")
        conn.close()
        return

    log.info("  Registros a procesar: %d (mode=%s)", len(rows), args.mode)

    # ── Dry-run (sin escribir, sin clasificar) ──
    if args.dry_run:
        print("\n  [DRY-RUN] Registros a procesar (máx 5):")
        for r in rows[:5]:
            existe = os.path.isfile(r["filepath_absoluto"])
            print(f"\n  media {r['id']} [{r['type']}] {r['filename_original']}")
            print(f"    archivo: {'OK' if existe else 'FALTA'} | duración: {r['duration_secs'] or '?'}s")
            print(f"    ruta: {r['filepath_absoluto']}")
        print(f"\n  Total: {len(rows)}")
        conn.close()
        return

    # ── Procesar (envuelto en manejar_interrupcion) ──
    # Al cortar con Ctrl+C se commitean los pendientes (el guardado ya es por
    # ítem cada 10) y se sale con mensaje claro, sin traceback.
    from scripts.ai_media.checkpoint import manejar_interrupcion
    with manejar_interrupcion(conn=conn, etiqueta="audio_tagging"):
        _ejecutar(conn, args, rows, tagging)


def _ejecutar(conn, args, rows, tagging) -> None:
    """
    Clasifica los sonidos de cada audio/video y guarda las etiquetas en la DB.

    Separado de main() para poder envolverlo en manejar_interrupcion sin
    re-indentar el cuerpo (mismo nivel de indentación de función). El
    guardado por ítem (cada 10) ya existía y no se modifica.
    """
    # ── Procesar ──
    ok = 0
    errors = 0
    sin_audio = 0
    t_inicio = time.perf_counter()
    for i, r in enumerate(rows, 1):
        mid = r["id"]
        ruta = r["filepath_absoluto"]

        if not os.path.isfile(ruta):
            log.warning("  [media %s] archivo no existe, skip: %s", mid, ruta)
            errors += 1
            continue

        try:
            top = _procesar_media(tagging, ruta, args.top_k,
                                  umbral=args.umbral_prob,
                                  excluir=None if args.incluir_excluidas
                                  else CLASES_EXCLUIDAS_POR_CORPUS)
        except RuntimeError as e:
            log.warning("  [media %s] ⚠ %s", mid, e)
            # Sin pista de audio: limpiar tags de sonido stale (de corridas
            # anteriores) para no dejar dudosas obsoletas; el raw se deja como
            # referencia.
            conn.execute(
                "DELETE FROM media_metadata WHERE media_id = ? AND key = ?",
                (mid, CLAVE_SALIDA),
            )
            sin_audio += 1
            continue
        except Exception as e:
            log.warning("  [media %s] ⚠ error inesperado: %s", mid, e)
            errors += 1
            continue

        # Traducir y filtrar
        final: list[tuple[str, float]] = []
        for etiqueta, prob in top:
            traducida = _limpiar_etiqueta(_traducir_etiqueta(etiqueta))
            # Las etiquetas compuestas quedan separadas por comas; evaluar cada parte
            for parte in (p.strip() for p in traducida.split(",") if p.strip()):
                if _es_basura(parte):
                    continue
                final.append((parte, prob))

        if not final:
            log.debug("  [media %s] sin etiquetas sobre el umbral.", mid)
            # Guardar igualmente el raw para depuración
            raw = [{"name": n, "prob": round(p, 4)} for n, p in top]
            conn.execute(
                "INSERT OR REPLACE INTO media_metadata (media_id, key, value) VALUES (?, ?, ?)",
                (mid, CLAVE_RAW, json.dumps(raw, ensure_ascii=False)),
            )
            # Limpiar tags de sonido stale de corridas anteriores (ya no hay
            # etiquetas válidas sobre el umbral).
            conn.execute(
                "DELETE FROM media_metadata WHERE media_id = ? AND key = ?",
                (mid, CLAVE_SALIDA),
            )
            continue

        # Quitar duplicados preservando el orden por probabilidad
        vistos: set[str] = set()
        unicos: list[tuple[str, float]] = []
        for etiqueta, prob in final:
            if etiqueta not in vistos:
                vistos.add(etiqueta)
                unicos.append((etiqueta, prob))

        # Corte duro a top_k: las etiquetas compuestas ("voz masculina, hombre
        # hablando") se separan en varias partes y pueden exceder top_k; el
        # contrato es N etiquetas por media (plan_keywords.md etapa 3).
        unicos = unicos[: args.top_k]

        texto_es = ", ".join(e for e, _ in unicos)
        raw = [{"name": n, "prob": round(p, 4)} for n, p in top]

        conn.execute(
            "INSERT OR REPLACE INTO media_metadata (media_id, key, value) VALUES (?, ?, ?)",
            (mid, CLAVE_SALIDA, texto_es),
        )
        conn.execute(
            "INSERT OR REPLACE INTO media_metadata (media_id, key, value) VALUES (?, ?, ?)",
            (mid, CLAVE_RAW, json.dumps(raw, ensure_ascii=False)),
        )
        ok += 1
        if args.verbose:
            log.info("  [media %s] sonidos: %s", mid, texto_es)
        if i % 10 == 0:
            conn.commit()
            log.info("  Progreso: %d/%d (%d ok, %d err, %d sin audio)",
                     i, len(rows), ok, errors, sin_audio)

    conn.commit()
    total = time.perf_counter() - t_inicio
    log.info("  ✅ Audio tagging: %d ok | %d errores | %d sin audio | %.1fs (%.2fs/media)",
             ok, errors, sin_audio, total, total / max(1, len(rows)))
    conn.close()


if __name__ == "__main__":
    main()
