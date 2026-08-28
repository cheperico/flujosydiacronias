#!/usr/bin/env python3
"""
ingest.py - Escanea una carpeta de medios, extrae metadatos y los ingiere en la DB.

Uso:
    python scripts/ingest.py --root D:/Flujos
    python scripts/ingest.py --root D:/Flujos --db db/flujos.db --exiftool "C:/Program Files/digiKam/exiftool.exe"

Flujo:
    1. Escanea recursivamente todos los archivos en --root
    2. Por cada archivo:
       a. Calcula fingerprint rápido (tamaño + fecha modificación)
          o SHA-256 completo si se usa --full-hash
       b. Si file_hash ya existe en DB -> SKIP
       c. Detecta tipo por extensión
       d. Extrae metadatos según tipo:
          - Imagen: exiftool (EXIF: GPS, fecha, cámara)
          - Video: ffprobe + XML sidecar SONY si existe
          - Audio: ffprobe
       e. Calcula content_hash (contenido puro, sin metadatos)
       f. Detecta si content_hash ya existe con distinto file_hash -> NOTIFICA
       g. Inserta en DB
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

from tqdm import tqdm


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

# Extensiones agrupadas por tipo
EXT_IMAGE = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp", ".heic", ".heif"}
EXT_VIDEO = {".mp4", ".mov", ".avi", ".mkv", ".mts", ".m2ts", ".wmv", ".flv", ".webm", ".mpg", ".mpeg"}
EXT_AUDIO = {".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a", ".wma", ".opus"}
EXT_TEXT = {".txt", ".md", ".csv", ".json", ".xml", ".srt", ".vtt", ".doc", ".docx", ".pdf"}
EXT_SIDECAR_XML = {".xml"}  # XML sidecar SONY
EXT_SIDECAR_AAE = {".aae"}  # Apple AAE sidecar
EXT_ARCHIVE = {".zip", ".rar", ".7z", ".tar", ".gz"}

# Pixel size para content_hash de imágenes (rápido, suficiente para detectar duplicados visuales)
CONTENT_HASH_IMAGE_SIZE = (32, 32)

# Tipo de fingerprint por defecto: rápido (tamaño + fecha modificación)
# Usar --full-hash para SHA-256 completo

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ingest")


# ---------------------------------------------------------------------------
# Hashes
# ---------------------------------------------------------------------------

def fast_fingerprint(filepath: str) -> str:
    """
    Fingerprint rápido: "{size}-{mtime}" — instantáneo pero NO es un hash
    criptográfico. Puede colisionar si el archivo cambia dentro del mismo
    segundo y mantiene el tamaño, o dos archivos distintos coinciden en
    size+mtime. Para deduplicación estricta usar --full-hash (SHA-256 real).
    Se guarda en media.file_hash (UNIQUE); formatos mezclados con SHA-256 son
    opacos para el resto del pipeline.
    """
    size = os.path.getsize(filepath)
    mtime = os.path.getmtime(filepath)
    return f"{size}-{int(mtime)}"


def sha256_file(filepath: str) -> str:
    """SHA-256 de un archivo completo."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_hash_image(filepath: str) -> str:
    """
    Content hash para imágenes: redimensiona a 32x32, convierte a RGB,
    y hashea los píxeles. Esto ignora metadatos EXIF.
    """
    try:
        from PIL import Image
        with Image.open(filepath) as img:
            # Convertir a RGB si tiene transparencia o paleta
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            # Redimensionar para consistencia (ignora resolución)
            img_small = img.resize(CONTENT_HASH_IMAGE_SIZE, Image.LANCZOS)
            return sha256_bytes(img_small.tobytes())
    except Exception as e:
        log.warning("  No se pudo calcular content_hash para imagen %s: %s", filepath, e)
        return sha256_file(filepath)


def content_hash_audio(filepath: str) -> str:
    """Por ahora, content_hash = file_hash para audio. Podríamos mejorar después."""
    return sha256_file(filepath)


# ---------------------------------------------------------------------------
# Metadata extraction helpers
# ---------------------------------------------------------------------------

def run_exiftool(exiftool_path: str, filepath: str) -> dict:
    """Ejecuta exiftool en formato JSON y devuelve dict con metadatos."""
    cmd = [exiftool_path, "-j", "-G", "--short", filepath]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            if data:
                return flatten_exiftool(data[0])
    except Exception as e:
        log.debug("  exiftool error en %s: %s", filepath, e)
    return {}


def flatten_exiftool(d: dict) -> dict:
    """Aplana claves con grupos tipo 'EXIF:DateTimeOriginal' -> 'exif_datetimeoriginal'."""
    flat = {}
    for k, v in d.items():
        if ":" in k:
            group, name = k.split(":", 1)
            flat_key = f"{group.lower()}_{name.lower()}"
        else:
            flat_key = k.lower()
        flat[flat_key] = str(v) if not isinstance(v, (str, int, float)) else v
    return flat


def parse_sony_xml(filepath: str) -> dict:
    """
    Parsea un XML sidecar SONY (CXXXXM01.XML) y extrae metadatos.
    Namespace típico: urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.00
    """
    ns = {
        "ns": "urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.00",
        "lib": "urn:schemas-professionalDisc:lib:ver.2.00",
    }
    meta = {}
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()

        # CreationDate
        elem = root.find(".//ns:CreationDate", ns)
        if elem is not None and "value" in elem.attrib:
            meta["sony_creationdate"] = elem.attrib["value"]

        # LastUpdate (en el root)
        if "lastUpdate" in root.attrib:
            meta["sony_lastupdate"] = root.attrib["lastUpdate"]

        # Duration (en frames, con tcFps en LtcChangeTable)
        dur = root.find(".//ns:Duration", ns)
        if dur is not None and "value" in dur.attrib:
            meta["sony_duration_frames"] = int(dur.attrib["value"])

        ltc = root.find(".//ns:LtcChangeTable", ns)
        if ltc is not None and "tcFps" in ltc.attrib:
            meta["sony_tc_fps"] = ltc.attrib["tcFps"]

        # VideoFormat
        vf = root.find(".//ns:VideoFormat", ns)
        if vf is not None:
            vfr = vf.find("ns:VideoRecPort", ns)
            if vfr is not None:
                meta["sony_video_port"] = vfr.attrib.get("port", "")
            vframe = vf.find("ns:VideoFrame", ns)
            if vframe is not None:
                for attr in ("videoCodec", "captureFps", "formatFps"):
                    if attr in vframe.attrib:
                        meta[f"sony_{attr}"] = vframe.attrib[attr]
            vlayout = vf.find("ns:VideoLayout", ns)
            if vlayout is not None:
                for attr in ("pixel", "numOfVerticalLine", "aspectRatio"):
                    if attr in vlayout.attrib:
                        meta[f"sony_{attr}"] = vlayout.attrib[attr]

        # AudioFormat
        af = root.find(".//ns:AudioFormat", ns)
        if af is not None:
            meta["sony_audio_channels"] = af.attrib.get("numOfChannel", "")
            ports = af.findall("ns:AudioRecPort", ns)
            for i, port in enumerate(ports):
                for attr in ("port", "audioCodec", "trackDst"):
                    if attr in port.attrib:
                        meta[f"sony_audio_port_{i+1}_{attr}"] = port.attrib[attr]

        # Device
        dev = root.find(".//ns:Device", ns)
        if dev is not None:
            for attr in ("manufacturer", "modelName", "serialNo"):
                if attr in dev.attrib:
                    meta[f"sony_device_{attr}"] = dev.attrib[attr]

        # RecordingMode
        rm = root.find(".//ns:RecordingMode", ns)
        if rm is not None:
            for attr in ("type", "cacheRec"):
                if attr in rm.attrib:
                    meta[f"sony_recording_{attr}"] = rm.attrib[attr]

    except ET.ParseError as e:
        log.warning("  Error parseando XML %s: %s", filepath, e)
    except Exception as e:
        log.warning("  Error procesando XML %s: %s", filepath, e)

    return meta


def get_ffprobe_metadata(filepath: str) -> dict:
    """Extrae metadata de un archivo multimedia con ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        filepath,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except Exception as e:
        log.debug("  ffprobe error en %s: %s", filepath, e)
    return {}


def extract_ffprobe_metadata(filepath: str) -> dict:
    """Aplana metadata de ffprobe a un dict clave-valor plano."""
    data = get_ffprobe_metadata(filepath)
    if not data:
        return {}

    meta = {}
    fmt = data.get("format", {})

    # Formato (contenedor)
    if "format_name" in fmt:
        meta["format_name"] = fmt["format_name"]
    if "duration" in fmt:
        meta["duration_secs"] = fmt["duration"]
    if "bit_rate" in fmt:
        meta["bit_rate"] = fmt["bit_rate"]
    if "size" in fmt:
        meta["container_size"] = fmt["size"]

    # Tags del formato (creation_time, etc.)
    fmt_tags = fmt.get("tags", {})
    for k, v in fmt_tags.items():
        meta[f"fmt_tag_{k.lower()}"] = v

    # Streams (primer video, primer audio)
    streams = data.get("streams", [])
    for stream in streams:
        codec_type = stream.get("codec_type", "").lower()
        if codec_type == "video":
            prefix = "video"
            for attr in ("codec_name", "width", "height",
                         "r_frame_rate", "avg_frame_rate",
                         "pix_fmt", "profile", "level"):
                if attr in stream:
                    val = stream[attr]
                    # Simplificar framerate fractions
                    if attr in ("r_frame_rate", "avg_frame_rate") and isinstance(val, str) and "/" in val:
                        try:
                            num, den = val.split("/")
                            val = round(float(num) / float(den), 3)
                        except:
                            pass
                    meta[f"{prefix}_{attr}"] = val
            # Tags de video stream (rotación, etc.)
            vtags = stream.get("tags", {})
            for k, v in vtags.items():
                meta[f"{prefix}_tag_{k.lower()}"] = v
            # Side data (spherical/equirectangular 360°, etc.)
            for sd in stream.get("side_data_list", []):
                sd_type = sd.get("side_data_type", "").lower()
                proj = sd.get("projection", "")
                if sd_type and "spherical" in sd_type:
                    meta[f"{prefix}_spherical_projection"] = proj
                    for k, v in sd.items():
                        if k not in ("side_data_type",):
                            meta[f"{prefix}_sd_{k.lower()}"] = str(v)
            break  # Solo primer video stream

    for stream in streams:
        codec_type = stream.get("codec_type", "").lower()
        if codec_type == "audio":
            prefix = "audio"
            for attr in ("codec_name", "sample_rate", "channels",
                         "channel_layout", "bit_rate"):
                if attr in stream:
                    meta[f"{prefix}_{attr}"] = stream[attr]
            break  # Solo primer audio stream

    return meta


# ---------------------------------------------------------------------------
# Timestamp extraction and normalization
# ---------------------------------------------------------------------------

def parse_timestamp_iso(ts_str: str):
    """Parsea timestamp ISO 8601 con o sin timezone."""
    if not ts_str:
        return None, None, None

    ts_str = ts_str.strip()

    # Intentar con timezone offset: "2025-08-11T20:47:56-03:00"
    # (NO reemplazar T antes, fromisoformat la necesita)
    try:
        dt = datetime.fromisoformat(ts_str)
        return dt.isoformat(), dt.astimezone(timezone.utc).isoformat(), "desde timezone offset"
    except:
        pass

    # Intentar sin timezone: "2025:08:11 20:47:56" (formato EXIF)
    # Reemplazar T por espacio solo para los formatos strptime
    ts_str_no_t = ts_str.replace("T", " ")
    for fmt in [
        "%Y:%m:%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
    ]:
        try:
            dt = datetime.strptime(ts_str_no_t, fmt)
            # Sin timezone -> asumir ART (UTC-3)
            dt_art = dt.replace(tzinfo=timezone(timedelta(hours=-3)))
            return dt.isoformat(), dt_art.astimezone(timezone.utc).isoformat(), "asumido ART -03:00"
        except:
            continue

    return ts_str, None, "no se pudo determinar"


def parse_timestamp_from_filename(basename: str):
    """Parsea timestamp del nombre de archivo con formato YYYY-MM-DD-HH-MM-SS_

    Lectura de derecha a izquierda: lo que falta se completa con 00.
    El guión bajo _ marca el fin del campo temporal.

    Ejemplos válidos:
      2025-05-03-11-34-04_archivo.mp4  → completo
      2025-05-03-11-34_archivo.mp4     → segundos=00
      2025-05-03-11_archivo.mp4        → minutos=00, segundos=00
      2025-05-03_archivo.mp4           → hora=00, minutos=00, segundos=00
    """
    import re
    basename = basename.strip()
    patterns = [
        (r'^(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})_', 6),
        (r'^(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})_', 5),
        (r'^(\d{4})-(\d{2})-(\d{2})-(\d{2})_', 4),
        (r'^(\d{4})-(\d{2})-(\d{2})_', 3),
    ]
    for pattern, n_campos in patterns:
        match = re.match(pattern, basename)
        if match:
            grupos = list(match.groups())
            # Completar con 00 los campos faltantes (de derecha a izquierda)
            while len(grupos) < 6:
                grupos.append("00")
            y, mo, d, h, mi, s = grupos
            try:
                dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s),
                              tzinfo=timezone(timedelta(hours=-3)))
                original = dt.isoformat()
                utc = dt.astimezone(timezone.utc).isoformat()
                return original, utc, "desde nombre de archivo"
            except ValueError:
                return None, None, None
    return None, None, None


def extract_timestamp_image(exif_meta: dict) -> tuple:
    """Extrae timestamp de metadatos EXIF. Devuelve (original, utc, note)."""
    # Orden de preferencia: DateTimeOriginal, CreateDate, ModifyDate
    for key in ("exif_datetimeoriginal", "exif_createdate", "exif_modifydate",
                "xmp_datetimeoriginal", "xmp_createdate"):
        val = exif_meta.get(key)
        if val:
            return parse_timestamp_iso(val)
    return None, None, "sin timestamp en EXIF"


def extract_timestamp_video(filepath: str, sony_xml_meta: dict, ffprobe_meta: dict) -> tuple:
    """Extrae timestamp de video. Prioridad: SONY XML > ffprobe > file modified."""
    # 1. SONY XML CreationDate (tiene timezone!)
    cd = sony_xml_meta.get("sony_creationdate")
    if cd:
        return parse_timestamp_iso(cd)

    # 2. ffprobe format tags creation_time
    ct = ffprobe_meta.get("fmt_tag_creation_time")
    if ct:
        return parse_timestamp_iso(ct)

    # 3. QuickTime: com.apple.quicktime.creationdate
    qt = ffprobe_meta.get("fmt_tag_com_apple_quicktime_creationdate")
    if qt:
        return parse_timestamp_iso(qt)

    return None, None, "sin timestamp en metadatos"


# ---------------------------------------------------------------------------
# Autor inference
# ---------------------------------------------------------------------------

# Mapping de carpetas de celulares a nombres de persona
CARPETA_AUTHOR_MAP = {
    "fABIAN": "Fabian",
    "FOTOS JPM": "Juan Pablo",
    "Fotos y videos Victor": "Victor",
    "Lucas": "Lucas",
    "NaHUEL": "Nahuel",
    "NEGRA": "Negra",
    "Agus": "Agustin",
    "Juan Marco": "Juan Marco",
}


def infer_author(filepath: str, carpeta: str, meta: dict, filetype: str) -> tuple:
    """
    Infiere el autor del medio. Devuelve (author, author_source).
    Prioridad:
    1. EXIF Artist / Creator (imágenes)
    2. Nombre de carpeta (celulares)
    3. Modelo de cámara (SONY)
    4. Vacío
    """
    author = None
    source = None

    if filetype in ("image", "video"):
        # EXIF Artist / XMP Creator / IPTC By-line
        for key in ("exif_artist", "xmp_creator", "iptc_byline", "exif_owner",
                     "quicktime_artist", "quicktime_author"):
            val = meta.get(key)
            if val and val.strip():
                author = val.strip()
                source = "exif"
                break

    # Si no se encontró en EXIF, usar nombre de carpeta
    if not author and carpeta:
        # Buscar coincidencia exacta primero, después parcial
        for folder_name, person_name in CARPETA_AUTHOR_MAP.items():
            if folder_name.lower() in carpeta.lower() or carpeta.lower() in folder_name.lower():
                author = person_name
                source = "carpeta"
                break

    # Si no se encontró, usar marca/modelo de cámara desde ExifTool o sidecar XML
    if not author and filetype == "video":
        maker = meta.get("xml_devicemanufacturer", "") or meta.get("sony_device_manufacturer", "")
        model = meta.get("xml_devicemodelname", "") or meta.get("sony_device_modelName", "")
        if maker and model:
            author = f"{maker} {model}"
            source = "exif"
        elif model:
            author = f"{maker or '?'} {model}"
            source = "exif"

    # Último recurso: el nombre de la carpeta tal cual
    if not author and carpeta and not carpeta.startswith("SONY"):
        author = carpeta
        source = "carpeta"

    return author, source


# ---------------------------------------------------------------------------
# File type detection
# ---------------------------------------------------------------------------

def detect_type(ext: str) -> str:
    ext = ext.lower()
    if ext in EXT_IMAGE:
        return "image"
    if ext in EXT_VIDEO:
        return "video"
    if ext in EXT_AUDIO:
        return "audio"
    if ext in EXT_TEXT:
        return "text"
    if ext in EXT_ARCHIVE:
        return "archive"
    return "other"


def is_sidecar_xml(filename: str) -> bool:
    """Detecta si es un XML sidecar SONY (CXXXXM01.XML)."""
    return bool(re.match(r'^C\d{4}M01\.XML$', filename, re.IGNORECASE))


def is_sidecar_aae(filename: str) -> bool:
    """Detecta si es un archivo .AAE de Apple."""
    return filename.lower().endswith(".aae")


def is_dot_underscore(filename: str) -> bool:
    """Detecta archivos '._' de macOS (Apple Double)."""
    return filename.startswith("._")


def _es_sur_oeste(ref: str) -> bool:
    """True si ref indica Sur/South o West/Oeste (acepta abrev. de 1 letra o texto completo)."""
    return bool(ref) and ref.strip().upper()[0] in ("S", "W")


def parse_gps_dms(dms_str: str, ref: str = "") -> float | None:
    """
    Convierte coordenadas DMS (grados, minutos, segundos) a decimal.
    Ejemplo: "34 deg 38' 34.62\" S" -> -34.64295
    También acepta "34.64295" (decimal directo).

    ref acepta tanto "S"/"W" como "South"/"West" (ExifTool sin -n).
    """
    if not dms_str:
        return None

    dms_str = dms_str.strip()
    dms_str = dms_str.rstrip(",")  # Composite:GPSPosition separa con coma

    # Ya es decimal? (ej: "-34.64295" o "34.64295")
    try:
        val = float(dms_str)
        if _es_sur_oeste(ref):
            val = -abs(val)
        return val
    except ValueError:
        pass

    # Formato DMS: "34 deg 38' 34.62\" S"
    pattern = r"([\d.]+)\s*deg\s*([\d.]+)\s*['\u2032]\s*([\d.]+)\s*[\"\\\u2033]?\s*([NSEWnsew][a-z]*)?"

    match = re.match(pattern, dms_str)
    if match:
        degrees = float(match.group(1))
        minutes = float(match.group(2))
        seconds = float(match.group(3))
        ref = match.group(4) or ref

        decimal = degrees + minutes / 60.0 + seconds / 3600.0
        if _es_sur_oeste(ref):
            decimal = -decimal
        return round(decimal, 7)

    return None


def _parse_gps_position(pos_str: str) -> tuple[float | None, float | None]:
    """
    Parsea Composite:GPSPosition en cualquiera de sus formatos:
      - Decimal:  "-34.64295 -58.45678"  (con -n)
      - DMS:      "31 deg 24' 28.55\" S, 64 deg 11' 23.11\" W"  (sin -n)
    """
    pos_str = pos_str.strip()
    if not pos_str:
        return None, None

    # Dividir por coma si está en formato DMS
    if "," in pos_str:
        halves = pos_str.split(",", 1)
        if len(halves) == 2:
            lat = parse_gps_dms(halves[0].strip())
            lon = parse_gps_dms(halves[1].strip())
            return lat, lon

    # Formato decimal: "lat lon"
    parts = pos_str.split()
    if len(parts) >= 2:
        lat = parse_gps_dms(parts[0])
        lon = parse_gps_dms(parts[1])
        return lat, lon

    return None, None


def extract_gps_from_exif(exif_meta: dict) -> dict:
    """
    Extrae latitud, longitud y altitud de metadatos EXIF.
    Exiftool devuelve GPS en múltiples formatos.
    """
    geo = {}

    # Buscar GPSPosition (decimal con -n, o DMS sin -n)
    gps_pos = exif_meta.get("composite_gpsposition", "")
    if gps_pos:
        lat, lon = _parse_gps_position(gps_pos)
        if lat is not None and lon is not None:
            geo["latitude"] = lat
            geo["longitude"] = lon
            geo["geolocation_source"] = "metadata"

    # Fallback: GPSLatitude + GPSLatitudeRef (DMS)
    if "latitude" not in geo:
        gps_lat = exif_meta.get("exif_gpslatitude", "")
        gps_lat_ref = exif_meta.get("exif_gpslatituderef", "")
        gps_lon = exif_meta.get("exif_gpslongitude", "")
        gps_lon_ref = exif_meta.get("exif_gpslongituderef", "")

        if gps_lat and gps_lon:
            lat = parse_gps_dms(gps_lat, gps_lat_ref)
            lon = parse_gps_dms(gps_lon, gps_lon_ref)
            if lat is not None and lon is not None:
                geo["latitude"] = lat
                geo["longitude"] = lon
                geo["geolocation_source"] = "metadata"

    # Altitud (opcional)
    gps_alt = exif_meta.get("exif_gpsaltitude", "")
    gps_alt_ref = exif_meta.get("exif_gpsaltituderef", "")
    if gps_alt:
        try:
            alt = float(gps_alt)
            if gps_alt_ref == "1":  # 1 = below sea level
                alt = -alt
            geo["altitude"] = alt
        except ValueError:
            pass

    return geo


def find_sony_sidecar(video_filepath: str) -> str | None:
    """
    Dado un video SONY (ej: C0040.MP4), busca su XML sidecar (C0040M01.XML).
    El XML tiene el patrón: CXXXXM01.XML donde XXXX es el número del video.
    """
    dirname = os.path.dirname(video_filepath)
    basename = os.path.basename(video_filepath)
    # C0040.MP4 -> buscar C0040M01.XML
    match = re.match(r'^(C\d{4})', basename, re.IGNORECASE)
    if match:
        prefix = match.group(1)
        sidecar_name = f"{prefix}M01.XML"
        sidecar_path = os.path.join(dirname, sidecar_name)
        if os.path.isfile(sidecar_path):
            return sidecar_path
    return None


# ---------------------------------------------------------------------------
# DB operations
# ---------------------------------------------------------------------------

def init_db(db_path: str):
    """Inicializa la DB con el schema SQL."""
    schema_path = os.path.join(os.path.dirname(__file__), "..", "db", "schema.sql")
    if not os.path.isfile(schema_path):
        # Fallback: schema embebido
        schema_path = None

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    # Primero schema canónico (crea tablas), luego migraciones para DBs viejas.
    # El orden anterior (migrate antes de schema) enmascaraba errores en DB fresca.
    if schema_path:
        with open(schema_path, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
    else:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename_original TEXT NOT NULL,
                filepath_absoluto TEXT NOT NULL,
                filepath_relativo TEXT NOT NULL,
                carpeta TEXT,
                type TEXT NOT NULL,
                subtype TEXT,
                size_bytes INTEGER,
                file_hash TEXT NOT NULL UNIQUE,
                content_hash TEXT,
                sidecar_xml TEXT,
                sidecar_parsed INTEGER DEFAULT 0,
                sidecar_hash TEXT,
                timestamp_original TEXT,
                timestamp_utc TEXT,
                timezone_note TEXT,
                duration_secs REAL,
                end_time TEXT,
                latitude REAL,
                longitude REAL,
                altitude REAL,
                geolocation_source TEXT,
                author TEXT,
                author_source TEXT,
                color_1_hex TEXT,
                color_1_name_css TEXT,
                color_1_name_basic TEXT,
                color_2_hex TEXT,
                color_2_name_css TEXT,
                color_2_name_basic TEXT,
                color_3_hex TEXT,
                color_3_name_css TEXT,
                color_3_name_basic TEXT,
                ingested_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                ingest_batch_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS media_metadata (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
                key TEXT NOT NULL,
                value TEXT,
                UNIQUE(media_id, key)
            );
            CREATE TABLE IF NOT EXISTS config (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_media_content_hash ON media(content_hash);
            CREATE INDEX IF NOT EXISTS idx_media_type ON media(type);
            CREATE INDEX IF NOT EXISTS idx_media_carpeta ON media(carpeta);
            CREATE INDEX IF NOT EXISTS idx_media_timestamp_utc ON media(timestamp_utc);
            CREATE INDEX IF NOT EXISTS idx_media_latlon ON media(latitude, longitude);
            CREATE INDEX IF NOT EXISTS idx_media_gps_time ON media(latitude, timestamp_utc) WHERE latitude IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_media_ingest_batch ON media(ingest_batch_id);
            CREATE INDEX IF NOT EXISTS idx_metadata_key ON media_metadata(key);
        """)

    # Migraciones para DBs creadas con schema anterior (columnas evolutivas)
    migrate_db(conn)

    conn.commit()
    return conn


def migrate_db(conn):
    """Agrega columnas faltantes para compatibilidad con DBs creadas con schema anterior."""
    migrations = [
        ("latitude", "REAL"),
        ("longitude", "REAL"),
        ("altitude", "REAL"),
        ("geolocation_source", "TEXT"),
        ("author", "TEXT"),
        ("author_source", "TEXT"),
        ("color_1_hex", "TEXT"),
        ("color_1_name_css", "TEXT"),
        ("color_1_name_basic", "TEXT"),
        ("color_2_hex", "TEXT"),
        ("color_2_name_css", "TEXT"),
        ("color_2_name_basic", "TEXT"),
        ("color_3_hex", "TEXT"),
        ("color_3_name_css", "TEXT"),
        ("color_3_name_basic", "TEXT"),
        ("duration_secs", "REAL"),
        ("ingest_batch_id", "INTEGER"),
        ("end_time", "TEXT"),
        ("provincia", "TEXT"),
        ("departamento", "TEXT"),
        ("municipio", "TEXT"),
        ("localidad", "TEXT"),
        ("geocode_source", "TEXT"),
        ("geocode_date", "TEXT"),
        ("distance_from_prev_m", "REAL"),
        ("elevation_gain_m", "REAL"),
        ("gradient_pct", "REAL"),
        ("cumul_distance_m", "REAL"),
        ("cumul_elevation_gain_m", "REAL"),
    ]

    # Indices utiles
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_media_gps_time ON media(latitude, timestamp_utc) WHERE latitude IS NOT NULL")
    except sqlite3.OperationalError:
        pass
    for col_name, col_type in migrations:
        try:
            conn.execute(f"ALTER TABLE media ADD COLUMN {col_name} {col_type}")
        except sqlite3.OperationalError:
            pass  # ya existe

    # Migración: tabla config (agregada en Julio 2026)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
    except sqlite3.OperationalError:
        pass

    # Migración: tabla media_keypoints (agregada en Julio 2026)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS media_keypoints (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                media_id              INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
                timestamp_offset_secs REAL NOT NULL,
                timestamp_absolute    TEXT NOT NULL,
                key                   TEXT NOT NULL DEFAULT 'transcription',
                value                 TEXT,
                source                TEXT DEFAULT 'whisper'
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_kp_absolute ON media_keypoints(timestamp_absolute)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_kp_media ON media_keypoints(media_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_kp_key ON media_keypoints(key)
        """)
    except sqlite3.OperationalError:
        pass


def file_hash_exists(conn, file_hash: str) -> bool:
    cursor = conn.execute("SELECT 1 FROM media WHERE file_hash = ?", (file_hash,))
    return cursor.fetchone() is not None


def find_content_hash_duplicates(conn, content_hash: str) -> list:
    """Busca registros con el mismo content_hash pero distinto file_hash."""
    if not content_hash:
        return []
    cursor = conn.execute(
        "SELECT id, filename_original, filepath_absoluto, file_hash FROM media WHERE content_hash = ?",
        (content_hash,),
    )
    return cursor.fetchall()


def insert_media(conn, record: dict) -> int:
    """Inserta un registro en media y devuelve su ID."""
    cursor = conn.execute("""
        INSERT INTO media (
            filename_original, filepath_absoluto, filepath_relativo, carpeta,
            type, subtype, size_bytes, file_hash, content_hash,
            sidecar_xml, sidecar_parsed, sidecar_hash,
            timestamp_original, timestamp_utc, timezone_note, duration_secs, end_time,
            latitude, longitude, altitude, geolocation_source,
            author, author_source,
            color_1_hex, color_1_name_css, color_1_name_basic,
            color_2_hex, color_2_name_css, color_2_name_basic,
            color_3_hex, color_3_name_css, color_3_name_basic,
            ingest_batch_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  ?, ?, ?, ?,
                  ?, ?,
                  ?, ?, ?,
                  ?, ?, ?,
                  ?, ?, ?,
                  ?)
    """, (
        record["filename_original"],
        record["filepath_absoluto"],
        record["filepath_relativo"],
        record.get("carpeta"),
        record["type"],
        record.get("subtype"),
        record.get("size_bytes"),
        record["file_hash"],
        record.get("content_hash"),
        record.get("sidecar_xml"),
        record.get("sidecar_parsed", 0),
        record.get("sidecar_hash"),
        record.get("timestamp_original"),
        record.get("timestamp_utc"),
        record.get("timezone_note"),
        record.get("duration_secs"),
        record.get("end_time"),
        record.get("latitude"),
        record.get("longitude"),
        record.get("altitude"),
        record.get("geolocation_source"),
        record.get("author"),
        record.get("author_source"),
        record.get("color_1_hex"),
        record.get("color_1_name_css"),
        record.get("color_1_name_basic"),
        record.get("color_2_hex"),
        record.get("color_2_name_css"),
        record.get("color_2_name_basic"),
        record.get("color_3_hex"),
        record.get("color_3_name_css"),
        record.get("color_3_name_basic"),
        record.get("ingest_batch_id"),
    ))
    conn.commit()
    return cursor.lastrowid


def insert_metadata(conn, media_id: int, meta: dict):
    """Inserta pares key-value en media_metadata."""
    for key, value in meta.items():
        if value is None:
            continue
        try:
            conn.execute(
                "INSERT OR IGNORE INTO media_metadata (media_id, key, value) VALUES (?, ?, ?)",
                (media_id, key, str(value)),
            )
        except Exception as e:
            log.warning("  Error insertando metadata %s=%s: %s", key, value, e)
    conn.commit()


# ---------------------------------------------------------------------------
# Procesamiento de archivos
# ---------------------------------------------------------------------------

def process_file(
    filepath: str,
    root: str,
    conn: sqlite3.Connection,
    exiftool_path: str | None,
    use_full_hash: bool,
    ingest_stats: dict,
    ingest_batch_id: int | None = None,
    allow_no_timestamp: bool = False,
):
    """Procesa un archivo: extrae metadatos y lo inserta en DB."""

    basename = os.path.basename(filepath)
    ext = os.path.splitext(basename)[1].lower()
    relpath = os.path.relpath(filepath, root)
    carpeta = os.path.basename(os.path.dirname(filepath))

    log.debug("Procesando: %s", relpath)

    # --- Saltar sidecars (se procesan junto con su archivo padre) ---
    if is_sidecar_xml(basename):
        log.info("  -> Sidecar XML, se procesa con su video")
        ingest_stats["sidecar_xml_skipped"] += 1
        return

    if is_sidecar_aae(basename):
        log.info("  -> Sidecar AAE, se procesa con su imagen")
        ingest_stats["sidecar_aae_skipped"] += 1
        return

    # --- Calcular file_hash (fingerprint) ---
    if use_full_hash:
        file_hash = sha256_file(filepath)
    else:
        file_hash = fast_fingerprint(filepath)

    if file_hash_exists(conn, file_hash):
        log.info("  -> Ya existe (fingerprint duplicado). SKIP.")
        ingest_stats["duplicates_file"] += 1
        return

    size_bytes = os.path.getsize(filepath)
    filetype = detect_type(ext)

    log.debug("  Tipo: %s | Tamaño: %s bytes", filetype, f"{size_bytes:,}")

    record = {
        "filename_original": basename,
        "filepath_absoluto": filepath,
        "filepath_relativo": relpath,
        "carpeta": carpeta if carpeta != root else None,
        "type": filetype,
        "size_bytes": size_bytes,
        "file_hash": file_hash,
        "ingest_batch_id": ingest_batch_id,
    }

    # --- Extraer metadatos según tipo ---
    meta = {}  # metadatos varios (van a media_metadata)
    sony_xml_meta = {}
    content_hash = None
    timestamp_original = None
    timestamp_utc = None
    timezone_note = None
    sidecar_xml_path = None
    sidecar_hash = None

    if filetype == "image":
        # Metadatos con exiftool
        if exiftool_path:
            exif_meta = run_exiftool(exiftool_path, filepath)
            if exif_meta:
                meta.update(exif_meta)
                # Timestamp
                ts_orig, ts_utc, ts_note = extract_timestamp_image(exif_meta)
                if ts_orig:
                    timestamp_original, timestamp_utc, timezone_note = ts_orig, ts_utc, ts_note

                # GPS
                geo = extract_gps_from_exif(exif_meta)
                if geo:
                    record["latitude"] = geo.get("latitude")
                    record["longitude"] = geo.get("longitude")
                    record["altitude"] = geo.get("altitude")
                    record["geolocation_source"] = geo.get("geolocation_source")
                    log.debug("  -> GPS: %.6f, %.6f (source: %s)",
                              geo.get("latitude", 0), geo.get("longitude", 0),
                              geo.get("geolocation_source", "?"))

        # Content hash (ignora metadatos)
        content_hash = content_hash_image(filepath)

        # Buscar sidecar AAE asociado
        aae_path = os.path.splitext(filepath)[0] + ".AAE"
        if os.path.isfile(aae_path):
            meta["sidecar_aae"] = aae_path
            log.debug("  -> Tiene sidecar AAE: %s", os.path.basename(aae_path))

        # Autor
        author, author_source = infer_author(filepath, carpeta, meta, filetype)
        if author:
            record["author"] = author
            record["author_source"] = author_source
            log.info("  -> Autor: %s (source: %s)", author, author_source)

    elif filetype == "video":
        # Metadata con ffprobe
        ffprobe_meta = extract_ffprobe_metadata(filepath)
        if ffprobe_meta:
            meta.update(ffprobe_meta)

        # Metadata con ExifTool (marca, modelo, XMP 360°, etc.)
        if exiftool_path:
            exif_meta = run_exiftool(exiftool_path, filepath)
            if exif_meta:
                meta.update(exif_meta)

        # Buscar XML sidecar SONY
        sidecar_xml_path = find_sony_sidecar(filepath)
        if sidecar_xml_path:
            log.info("  -> Tiene sidecar XML: %s", os.path.basename(sidecar_xml_path))
            sony_xml_meta = parse_sony_xml(sidecar_xml_path)
            if sony_xml_meta:
                meta.update(sony_xml_meta)
                record["sidecar_xml"] = os.path.relpath(sidecar_xml_path, root)
                record["sidecar_parsed"] = 1
                sidecar_hash = sha256_file(sidecar_xml_path)
                record["sidecar_hash"] = sidecar_hash

        # Detectar 360° por metadatos (ffprobe side_data + ExifTool XMP)
        if detect_360(meta):
            record["subtype"] = "360"

        # Timestamp
        ts_orig, ts_utc, ts_note = extract_timestamp_video(filepath, sony_xml_meta, ffprobe_meta)
        if ts_orig:
            timestamp_original, timestamp_utc, timezone_note = ts_orig, ts_utc, ts_note

        # Autor (incluye deteccion de marca/modelo desde ExifTool)
        author, author_source = infer_author(filepath, carpeta, meta, filetype)
        if author:
            record["author"] = author
            record["author_source"] = author_source
            log.info("  -> Autor: %s (source: %s)", author, author_source)

        # Content hash de video: siempre file_hash (el hash visual de video es
        # lento y no se usa; la detección de duplicados visuales queda para imágenes)
        content_hash = file_hash

    elif filetype == "audio":
        # Metadata con ffprobe
        ffprobe_meta = extract_ffprobe_metadata(filepath)
        if ffprobe_meta:
            meta.update(ffprobe_meta)

        content_hash = content_hash_audio(filepath)

        # Timestamp
        ct = ffprobe_meta.get("fmt_tag_creation_time")
        if ct:
            ts_orig, ts_utc, ts_note = parse_timestamp_iso(ct)
            if ts_orig:
                timestamp_original, timestamp_utc, timezone_note = ts_orig, ts_utc, ts_note

    elif filetype == "text":
        content_hash = sha256_file(filepath)

    else:
        content_hash = file_hash  # fallback

    record["content_hash"] = content_hash

    # --- Fallback: timestamp desde nombre de archivo (convención YYYY-MM-DD-HH-MM-SS_) ---
    if not timestamp_original:
        ts_orig, ts_utc, ts_note = parse_timestamp_from_filename(basename)
        if ts_orig:
            timestamp_original = ts_orig
            timestamp_utc = ts_utc
            timezone_note = ts_note
            log.info("  Timestamp: desde nombre de archivo")

    record["timestamp_original"] = timestamp_original
    record["timestamp_utc"] = timestamp_utc
    record["timezone_note"] = timezone_note

    # --- Promover duration_secs de meta a columna directa (videos/audios) ---
    if "duration_secs" in meta:
        record["duration_secs"] = meta.pop("duration_secs")

    # --- Calcular end_time: timestamp_utc + duration_secs ---
    if timestamp_utc:
        dur = record.get("duration_secs")
        if dur:
            try:
                dt_end = datetime.fromisoformat(timestamp_utc) + timedelta(seconds=float(dur))
                record["end_time"] = dt_end.isoformat()
            except Exception:
                record["end_time"] = timestamp_utc  # fallback: punto
        else:
            record["end_time"] = timestamp_utc  # punto: mismo timestamp

    # --- Verificar timestamp (si no está permitido sin él, saltar) ---
    if not timestamp_utc and not allow_no_timestamp:
        log.info("  Saltado (sin timestamp): %s", basename)
        ingest_stats["skipped_no_timestamp"] += 1
        return

    # --- Detectar contenido duplicado (mismo content_hash, distinto file_hash) ---
    if content_hash:
        duplicates = find_content_hash_duplicates(conn, content_hash)
        if duplicates:
            log.warning("  ⚠ CONTENIDO DUPLICADO (mismo contenido, diferentes metadatos o formatos):")
            for dup in duplicates:
                log.warning("    Ya existe: id=%s | %s | hash=%s", dup[0], dup[1], dup[3])
                log.warning("    Path: %s", dup[2])
            ingest_stats["duplicates_content"] += 1

    # --- Insertar en DB ---
    try:
        media_id = insert_media(conn, record)
        if meta:
            insert_metadata(conn, media_id, meta)
        log.debug("  ✅ Insertado (id=%s)", media_id)
        ingest_stats["inserted"] += 1
        if not timestamp_utc:
            ingest_stats["ingested_no_timestamp"] += 1
    except Exception as e:
        log.error("  ❌ Error insertando: %s", e)
        ingest_stats["errors"] += 1


def detect_360(meta: dict) -> bool:
    """
    Detecta si un video es 360° por metadatos.
    
    Revisa:
    1. Claves con "spherical"/"equirectangular"/"stereo" y valores truthy
       (cubre ffprobe tags, side_data, ExifTool XMP:Spherical)
    2. Valores con "equirectangular" incluso si la clave no es obvia
       (cubre ExifTool XMP:ProjectionType)
    """
    for key, value in meta.items():
        kl = key.lower()
        val = str(value).lower()
        if "spherical" in kl or "equirectangular" in kl or "stereo" in kl:
            if "true" in val or "1" in val or "equirectangular" in val:
                return True
        # Algunos metadatos (ej: XMP:ProjectionType) ponen el tipo en el valor
        if "projection" in kl and "equirectangular" in val:
            return True
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Ingiere medios desde una carpeta a la base de datos Flujos",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=r"""
Ejemplos:
  python scripts/ingest.py --root D:\Flujos
  python scripts/ingest.py --root D:\Flujos --db db\flujos.db --verbose
  python scripts/ingest.py --root D:\Flujos --exiftool "C:\Program Files\digiKam\exiftool.exe"
        """,
    )
    parser.add_argument("--root", required=True, help="Carpeta raíz a escanear")
    parser.add_argument(
        "--db",
        default=None,
        help="Ruta a la base de datos SQLite (default: ./db/flujos.db)",
    )
    parser.add_argument(
        "--exiftool",
        default=None,
        help="Ruta al ejecutable de exiftool (default: auto-buscar)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Modo verbose (debug logging)",
    )
    parser.add_argument(
        "--recursive", "-r",
        action="store_true",
        help="Escanea subcarpetas recursivamente (default: solo raíz)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo escanea y muestra qué haría, no escribe en DB",
    )
    parser.add_argument(
        "--full-hash",
        action="store_true",
        help=(
            "Usar SHA-256 completo como file_hash (lento en archivos grandes). "
            "Por defecto usa fingerprint rápido: tamaño + fecha de modificación."
        ),
    )
    parser.add_argument(
        "--types",
        default=None,
        help=(
            "Tipos de medio a ingerir, separados por coma. "
            "Ej: --types image,video  (default: todos)"
        ),
    )
    parser.add_argument(
        "--allow-no-timestamp",
        action="store_true",
        help=(
            "Ingerir archivos incluso si no tienen timestamp. "
            "Por defecto se saltan archivos sin timestamp."
        ),
    )

    args = parser.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        log.error("La carpeta raíz no existe: %s", root)
        sys.exit(1)

    # Determinar ruta de DB
    if args.db:
        db_path = os.path.abspath(args.db)
    else:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        db_path = os.path.join(project_root, "db", "flujos.db")

    # Determinar exiftool
    exiftool_path = args.exiftool
    if not exiftool_path:
        # Buscar en ubicaciones comunes
        candidates = [
            "C:\\Program Files\\digiKam\\exiftool.exe",
            "C:\\Program Files\\exiftool.exe",
            "exiftool",  # en PATH
        ]
        for c in candidates:
            if c == "exiftool":
                try:
                    subprocess.run([c, "-ver"], capture_output=True, timeout=5)
                    exiftool_path = c
                    break
                except:
                    continue
            elif os.path.isfile(c):
                exiftool_path = c
                break

    if exiftool_path:
        log.info("exiftool: %s", exiftool_path)
    else:
        log.warning("exiftool no encontrado. Las imágenes se procesarán sin metadatos EXIF.")

    log.info("Base de datos: %s", db_path)
    log.info("Carpeta raíz: %s", root)

    # Inicializar DB
    conn = init_db(db_path)
    log.info("Schema verificado/creado.")

    # Generar batch_id para esta corrida (timestamp-based, unico)
    import time
    ingest_batch_id = int(time.time() * 1000) % 1000000
    log.info("Batch ID de esta ingesta: %s", ingest_batch_id)

    # Guardar raíz de ingesta y batch actual
    if not args.dry_run:
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
            ("ingest_root", os.path.abspath(root)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
            ("current_ingest_batch", str(ingest_batch_id)),
        )
        conn.commit()

    if args.dry_run:
        log.info("=== DRY RUN - No se escribirá en la DB ===")

    # Determinar tipos de medio a ingerir
    tipos_permitidos = {"image", "video", "audio", "text"}
    if args.types:
        tipos_seleccionados = set(t.strip().lower() for t in args.types.split(","))
        tipos_permitidos = tipos_seleccionados & tipos_permitidos
        if not tipos_permitidos:
            log.error("Ningún tipo válido en --types. Válidos: image, video, audio, text")
            sys.exit(1)
        log.info("Tipos a ingerir: %s", ", ".join(sorted(tipos_permitidos)))
    else:
        log.info("Tipos a ingerir: todos (image, video, audio, text)")

    # Estadísticas
    stats = {
        "scanned": 0,
        "inserted": 0,
        "duplicates_file": 0,
        "duplicates_content": 0,
        "sidecar_xml_skipped": 0,
        "sidecar_aae_skipped": 0,
        "skipped_dot_underscore": 0,
        "errors": 0,
        "skipped_type": 0,
        "skipped_no_timestamp": 0,
        "ingested_no_timestamp": 0,
    }

    # Escanear archivos
    log.info("Escaneando %s ...", root)
    all_files = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Saltar carpetas ocultas y excluir/
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d.lower() != "excluir"
        ]
        # Si no es recursivo, solo procesar la raíz
        if not args.recursive and dirpath != root:
            dirnames.clear()
            continue
        for f in filenames:
            all_files.append(os.path.join(dirpath, f))

    # Ordenar por nombre para consistencia
    all_files.sort()
    total = len(all_files)
    log.info("Archivos encontrados: %s", total)

    # Procesar con barra de progreso
    pbar = tqdm(total=total, desc="Ingestando", unit="arch", ncols=80)

    for i, filepath in enumerate(all_files, 1):
        stats["scanned"] += 1
        relpath_display = os.path.relpath(filepath, root)
        pbar.set_postfix_str(f"{relpath_display[:50]}...", refresh=False)

        ext = os.path.splitext(filepath)[1].lower()
        basename = os.path.basename(filepath)
        filetype = detect_type(ext)

        # Saltar archivos ._ de macOS (Apple Double)
        if is_dot_underscore(basename):
            stats["skipped_dot_underscore"] += 1
            pbar.update(1)
            continue

        # Saltar tipos no soportados
        if filetype == "other" and ext not in EXT_SIDECAR_XML and ext not in EXT_SIDECAR_AAE:
            stats["skipped_type"] += 1
            pbar.update(1)
            continue

        # Filtrar por tipo de medio seleccionado (sidecars Sony/Apple siempre pasan)
        es_sidecar = (ext in EXT_SIDECAR_XML and is_sidecar_xml(basename)) or ext in EXT_SIDECAR_AAE
        if filetype not in tipos_permitidos and not es_sidecar:
            stats["skipped_type"] += 1
            log.debug("  Saltado (tipo no seleccionado): %s", basename)
            pbar.update(1)
            continue

        if args.dry_run:
            stats["inserted"] += 1
            pbar.update(1)
            continue

        process_file(filepath, root, conn, exiftool_path,
                     args.full_hash, stats, ingest_batch_id,
                     allow_no_timestamp=args.allow_no_timestamp)
        pbar.update(1)

    pbar.close()

    # --- Reporte final ---
    log.info("")
    log.info("=" * 60)
    log.info("  INGEST COMPLETADO")
    log.info("=" * 60)
    log.info("  Archivos escaneados:           %s", f"{stats['scanned']:,}")
    log.info("  Insertados:                    %s", f"{stats['inserted']:,}")
    log.info("  Duplicados (file_hash):        %s", f"{stats['duplicates_file']:,}")
    log.info("  Contenido duplicado:           %s", f"{stats['duplicates_content']:,}")
    log.info("  Sidecars XML saltados:         %s", f"{stats['sidecar_xml_skipped']:,}")
    log.info("  Sidecars AAE saltados:         %s", f"{stats['sidecar_aae_skipped']:,}")
    log.info("  Saltados (._ macOS):           %s", f"{stats['skipped_dot_underscore']:,}")
    log.info("  Saltados (tipo):               %s", f"{stats['skipped_type']:,}")
    log.info("  Saltados (sin timestamp):      %s", f"{stats['skipped_no_timestamp']:,}")
    if args.allow_no_timestamp:
        log.info("  Ingeridos sin timestamp:        %s", f"{stats['ingested_no_timestamp']:,}")
    log.info("  Errores:                       %s", f"{stats['errors']:,}")
    log.info("=" * 60)

    conn.close()


if __name__ == "__main__":
    main()
