"""
Exporta datos de flujos.db → visualizacion.db para una visualización web.
Script genérico de deploy: sirve a cualquier implementación web (la web actual
vive en deploy/). Lee la tabla media + media_metadata y reconstruye medios.
También exporta telegram_messages (chat) con sus fotos vinculadas.

Modo deploy (por defecto, --deploy-dir):
  Copia los medios a <dir>/media/<carpeta>/<archivo>, transcodifica videos
  grandes o 360° a MP4/H.264 web (si ffmpeg está disponible) y escribe la DB
  en <dir>/db/visualizacion.db con ruta_absoluta web-relativa ('media/...'
  con slash '/' siempre), de modo que servir_medio.php la resuelva contra la
  raíz de deploy (fallback __DIR__.'/../..').
  El destino por defecto es deploy/ en la raíz del proyecto.

Modo snapshot local (--snapshot-local):
  Comportamiento dev local: DB local deploy/db/visualizacion.db
  con rutas absolutas de Windows, sin copiar medios ni transcodificar.
"""
import argparse
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLUJOS_DB = os.path.join(BASE, 'db', 'flujos.db')
VIZ_DB = os.path.join(BASE, 'deploy', 'db', 'visualizacion.db')
DEPLOY_DEFAULT = os.path.join(BASE, 'deploy')

log = logging.getLogger(__name__)


def resolver_ruta_absoluta(ruta: str) -> str:
    """
    Convierte una ruta a absoluta. Algunas filas guardan rutas relativas al
    proyecto (ej: 'n\\telegram\\audio_1.ogg', o 'telegram/...'). Apache/PHP las
    aborta con file_exists() porque busca relativo al cwd del servidor, no a la
    raíz de Flujos. Aquí se resuelven contra BASE (raíz del proyecto).
    """
    if not ruta:
        return ruta
    # Ya es absoluta en Windows (C:\ o \\): dejarla como está.
    if len(ruta) >= 3 and ruta[1:3] == ':\\':
        return ruta
    if ruta.startswith('\\\\') or ruta.startswith('/'):
        return ruta
    # Relativa a la raíz del proyecto → unir con BASE y normalizar slashes.
    return os.path.normpath(os.path.join(BASE, ruta))


def _parsear_dimension(texto: str) -> tuple[int, int]:
    """Parsea una dimensión 'WxH' a (ancho, alto). Sale con error si es inválida."""
    partes = texto.lower().split('x')
    if len(partes) != 2:
        raise SystemExit(f"Dimensiones inválidas para --transcode-box: '{texto}' (se esperaba WxH, ej: 1280x720)")
    try:
        ancho, alto = int(partes[0]), int(partes[1])
    except ValueError:
        raise SystemExit(f"Dimensiones inválidas para --transcode-box: '{texto}' (se esperaba WxH, ej: 1280x720)")
    if ancho <= 0 or alto <= 0:
        raise SystemExit(f"Dimensiones inválidas para --transcode-box: '{texto}' (deben ser mayores a 0)")
    return ancho, alto


def _ruta_web_relativa(carpeta: str, archivo: str) -> str:
    """
    Construye la ruta web-relativa 'media/<carpeta>/<archivo>' con slash '/'
    SIEMPRE (aunque Windows use backslash), para resolver contra la raíz de
    deploy en servir_medio.php.
    """
    partes = [(p or '').replace('\\', '/').strip('/') for p in (carpeta, archivo)]
    return 'media/' + '/'.join(partes)


def _valor_verdadero(v) -> bool:
    """Interpreta un marcador de DB (xmp_spherical) como booleano."""
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ('1', 'true', 'yes', 'si', 's')


def _resolver_comando(nombre: str) -> str | None:
    """Resuelve la ruta de ffmpeg/ffprobe (variable de entorno o shutil.which)."""
    var = os.environ.get(nombre.upper())
    if var:
        return var
    return shutil.which(nombre)


def _es_360_equirectangular(stream: dict, formato: dict, ancho: int, alto: int) -> bool:
    """
    Detecta video 360° equirectangular:
    1) Metadata esférica: side_data del stream o tags del stream/formato
       (spherical / projection_type == 'equirectangular').
    2) Heurística de respaldo: aspecto exactamente 2:1 y ancho >= 3840.
    """
    for sd in stream.get('side_data_list') or []:
        proyeccion = str(sd.get('projection') or sd.get('projection_type') or '').lower()
        if proyeccion in ('equirectangular', 'equirect'):
            return True
        if 'spherical' in str(sd.get('side_data_type') or '').lower():
            return True
    for tags in (stream.get('tags') or {}, formato.get('tags') or {}):
        for clave, valor in tags.items():
            k = str(clave).lower()
            v = str(valor).lower()
            if 'spherical' in k or '360' in k:
                return True
            if 'projection' in k and 'equirect' in v:
                return True
    if ancho > 0 and ancho == alto * 2 and ancho >= 3840:
        return True
    return False


def _probar_video(ruta: str, ffprobe: str) -> dict:
    """
    Corre ffprobe sobre un video y devuelve ancho, alto, códec, si tiene audio
    y si es 360° equirectangular. Devuelve {} si no se pudo inspeccionar.
    """
    try:
        resultado = subprocess.run(
            [ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', ruta],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.SubprocessError, OSError) as e:
        log.warning("ffprobe falló para %s: %s", ruta, e)
        return {}
    if resultado.returncode != 0:
        log.warning("ffprobe con error para %s: %s", ruta, (resultado.stderr or '').strip()[-300:])
        return {}
    try:
        datos = json.loads(resultado.stdout or '{}')
    except json.JSONDecodeError:
        return {}
    stream_video = next((s for s in datos.get('streams') or [] if s.get('codec_type') == 'video'), None)
    if not stream_video:
        return {}
    ancho = int(stream_video.get('width') or 0)
    alto = int(stream_video.get('height') or 0)
    if ancho <= 0 or alto <= 0:
        return {}
    return {
        'ancho': ancho,
        'alto': alto,
        'codec_video': stream_video.get('codec_name'),
        'tiene_audio': any(s.get('codec_type') == 'audio' for s in datos.get('streams') or []),
        'es_360': _es_360_equirectangular(stream_video, datos.get('format') or {}, ancho, alto),
    }


def _calcular_escala_regular(ancho: int, alto: int, caja: tuple[int, int]) -> tuple[int, int] | None:
    """
    Calcula el destino de un video regular dentro de la caja manteniendo
    proporción (dimensiones pares para libx264/yuv420p). Devuelve None si no
    corresponde transcodificar (ya entra en la caja).
    """
    if not (ancho > 1920 or alto > 1080):
        return None
    ancho_caja, alto_caja = caja
    escala = min(ancho_caja / ancho, alto_caja / alto)
    if escala >= 1.0:
        return None
    w = int(round(ancho * escala))
    h = int(round(alto * escala))
    w -= w % 2
    h -= h % 2
    return max(2, w), max(2, h)


def _calcular_escala_360(ancho: int, alto: int, largo: int) -> tuple[int, int] | None:
    """
    Calcula el destino de un video 360° con lado mayor = `largo` manteniendo
    proporción (dimensiones pares). Devuelve None si no excede el target.
    """
    if max(ancho, alto) <= largo:
        return None
    escala = largo / max(ancho, alto)
    if ancho >= alto:
        w = int(round(ancho * escala))
        h = int(round(alto * escala))
    else:
        h = int(round(alto * escala))
        w = int(round(ancho * escala))
    w -= w % 2
    h -= h % 2
    return max(2, w), max(2, h)


def _transcodificar_video(fuente: str, destino: str, ancho: int, alto: int,
                         tiene_audio: bool, ffmpeg: str) -> float | None:
    """
    Transcodifica un video a MP4/H.264 web en el destino. Devuelve los
    segundos que tardó, o None si falló (borra el destino parcial).
    """
    # Tope de bitrate según resolución (para tamaño predecible en hosting compartido).
    pixeles = ancho * alto
    if pixeles > 1200000:
        maxrate, bufsize = '4500k', '9000k'
    elif pixeles > 700000:
        maxrate, bufsize = '3000k', '6000k'
    else:
        maxrate, bufsize = '2000k', '4000k'

    cmd = [ffmpeg, '-y', '-i', fuente,
           '-vf', f'scale={ancho}:{alto}', '-r', '30',
           '-c:v', 'libx264', '-preset', 'medium', '-crf', '23',
           '-maxrate', maxrate, '-bufsize', bufsize,
           '-g', '60', '-sc_threshold', '0',
           '-profile:v', 'main', '-level', '4.1']
    if tiene_audio:
        cmd += ['-c:a', 'aac', '-b:a', '128k']
    else:
        cmd += ['-an']
    cmd += ['-movflags', '+faststart', destino]
    inicio = time.time()
    try:
        resultado = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as e:
        log.warning("No se pudo ejecutar ffmpeg: %s", e)
        return None
    duracion = time.time() - inicio
    if resultado.returncode != 0:
        log.warning("Transcode falló (%s -> %s): %s",
                    os.path.basename(fuente), os.path.basename(destino),
                    (resultado.stderr or '').strip()[-300:])
        if os.path.exists(destino):
            try:
                os.remove(destino)
            except OSError:
                pass
        return None
    return duracion


def _copiar_medio(origen: str, destino: str) -> None:
    """Copia el archivo origen al destino creando el directorio si falta."""
    try:
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        shutil.copy2(origen, destino)
    except OSError as e:
        log.warning("No se pudo copiar %s -> %s: %s", origen, destino, e)


def _copiar_y_transcodificar_medios(flujos_db: str, deploy_dir: str, transcode: bool,
                                    caja: tuple[int, int], largo_360: int,
                                    dry_run: bool) -> dict[int, int]:
    """
    Copia los medios a <deploy_dir>/media/<carpeta>/<archivo> y transcodifica
    los videos que correspondan (directo al destino final, sin copia previa).
    En dry_run solo lista qué se haría, sin copiar ni transcodificar.
    Devuelve {id_media: tamaño_web} para los videos transcodificados.
    """
    mapa_tamanos: dict[int, int] = {}

    conn = sqlite3.connect(flujos_db)
    conn.row_factory = sqlite3.Row
    filas = conn.execute(
        "SELECT id, filename_original, carpeta, type, filepath_absoluto, size_bytes "
        "FROM media ORDER BY id"
    ).fetchall()

    # Marcador 360° desde la ingesta (media_metadata → key xmp_spherical,
    # escrito por improve_db --step video_metadata). Si la DB lo marca,
    # prevalece sobre la detección por ffprobe.
    marcadores_360: dict[int, bool] = {}
    try:
        cur360 = conn.execute(
            "SELECT media_id, value FROM media_metadata WHERE key = 'xmp_spherical'"
        )
        for r360 in cur360:
            marcadores_360[r360['media_id']] = _valor_verdadero(r360['value'])
    except sqlite3.Error as e:
        log.warning("No se pudo leer xmp_spherical de media_metadata: %s", e)

    conn.close()

    ffprobe = _resolver_comando('ffprobe')
    ffmpeg = _resolver_comando('ffmpeg') if transcode else None
    if transcode and not ffmpeg:
        log.warning("ffmpeg no encontrado: los videos se copiarán sin transcodificar.")
    if transcode and not ffprobe:
        log.warning("ffprobe no encontrado: no se podrá detectar resolución ni 360°; videos se copian tal cual.")

    stats = {
        'copiar': 0,
        'faltantes': 0,
        'omitidos_texto': 0,
        'videos_360': 0,
        'videos_regulares': 0,
        'videos_transcodificados': 0,
        'omitidos_existente': 0,
    }

    if dry_run:
        log.info("Plan de deploy (DRY-RUN, no escribe nada):")
        log.info("  destino: %s", os.path.join(deploy_dir, 'media'))

    inicio = time.time()
    for r in filas:
        tipo = r['type']
        carpeta = r['carpeta'] or ''
        archivo = r['filename_original'] or ''
        if tipo == 'text':
            stats['omitidos_texto'] += 1
            continue

        origen = resolver_ruta_absoluta(r['filepath_absoluto'])
        destino = os.path.join(deploy_dir, 'media', carpeta, archivo)

        if not os.path.exists(origen):
            stats['faltantes'] += 1
            log.warning("Fuente no existe (id %s): %s", r['id'], origen)
            continue

        # Skip-if-exists: si el destino ya existe y es igual o más nuevo que la
        # fuente, no se reprocesa (export incremental).
        if os.path.exists(destino):
            try:
                destino_mas_nuevo = os.path.getmtime(destino) >= os.path.getmtime(origen)
            except OSError:
                destino_mas_nuevo = False
            if destino_mas_nuevo:
                stats['omitidos_existente'] += 1
                try:
                    mapa_tamanos[r['id']] = os.path.getsize(destino)
                except OSError:
                    pass
                continue

        # Garantizar el directorio destino antes de copiar o transcodificar.
        if not dry_run:
            os.makedirs(os.path.dirname(destino), exist_ok=True)

        # Video con transcode activo: inspeccionar y decidir.
        if tipo == 'video' and transcode and ffprobe:
            info = _probar_video(origen, ffprobe)
            if info:
                # Marcador 360° desde la DB (lo escribe la ingesta vía
                # --step video_metadata → key xmp_spherical); ffprobe queda
                # como respaldo para videos no marcados.
                es_360_db = marcadores_360.get(r['id'], False)
                if es_360_db:
                    info['es_360'] = True
                if info['es_360']:
                    dims = _calcular_escala_360(info['ancho'], info['alto'], largo_360)
                    motivo = '360°'
                else:
                    dims = _calcular_escala_regular(info['ancho'], info['alto'], caja)
                    motivo = 'grande'
                if dims:
                    if dry_run:
                        stats['videos_360' if info['es_360'] else 'videos_regulares'] += 1
                        log.info("  [%s] id=%s  %s  %dx%d -> %dx%d",
                                 motivo, r['id'], archivo,
                                 info['ancho'], info['alto'], dims[0], dims[1])
                        continue
                    if ffmpeg:
                        duracion = _transcodificar_video(origen, destino, dims[0], dims[1],
                                                         info['tiene_audio'], ffmpeg)
                        if duracion is not None:
                            stats['videos_transcodificados'] += 1
                            stats['videos_360' if info['es_360'] else 'videos_regulares'] += 1
                            log.info("  Transcode id=%s  %s  %dx%d -> %dx%d  (%.1fs)",
                                     r['id'], archivo, info['ancho'], info['alto'],
                                     dims[0], dims[1], duracion)
                            try:
                                mapa_tamanos[r['id']] = os.path.getsize(destino)
                            except OSError:
                                log.warning("No se pudo leer el tamaño de %s", destino)
                            continue
                    # Sin ffmpeg o transcode falló → copia simple del original.
                    _copiar_medio(origen, destino)
                    stats['copiar'] += 1
                    continue
            # Sin datos de ffprobe o ya entra en la caja → copia simple.

        # Copia simple (imagen, audio, o video sin transcode).
        if not dry_run:
            _copiar_medio(origen, destino)
        stats['copiar'] += 1

    duracion_total = time.time() - inicio

    if dry_run:
        log.info("  Se copiarían: %d archivos", stats['copiar'])
        log.info("  Se transcodificarían: %d videos (360°: %d, regulares: %d)",
                 stats['videos_360'] + stats['videos_regulares'],
                 stats['videos_360'], stats['videos_regulares'])
        log.info("  Omitidos (ya existentes): %d", stats['omitidos_existente'])
        if not transcode:
            log.info("  Transcode no activado (--transcode): los videos se copian tal cual.")
        if stats['faltantes']:
            log.warning("  Fuentes faltantes (no se copiarían): %d", stats['faltantes'])
        log.info("DRY-RUN: no se copió ni escribió nada.")
        return {}

    log.info("Copia/transcode de medios completado en %.1fs:", duracion_total)
    log.info("  Copiados: %d", stats['copiar'])
    log.info("  Videos transcodificados: %d (360°: %d, regulares: %d)",
             stats['videos_transcodificados'], stats['videos_360'], stats['videos_regulares'])
    log.info("  Omitidos (type='text'): %d", stats['omitidos_texto'])
    log.info("  Omitidos (ya existentes): %d", stats['omitidos_existente'])
    if stats['faltantes']:
        log.warning("  Fuentes faltantes (no copiados): %d", stats['faltantes'])
    return mapa_tamanos


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Exporta flujos.db -> visualizacion.db (deploy genérico o snapshot local)."
    )
    parser.add_argument('--deploy-dir', default=DEPLOY_DEFAULT,
                        help="Carpeta de deploy: copia los medios a <dir>/media/... y escribe la DB "
                             "en <dir>/db/visualizacion.db con rutas web-relativas (media/...). "
                             "Default: deploy/ en la raíz del proyecto.")
    parser.add_argument('--snapshot-local', action='store_true',
                        help="Modo local (dev): escribe deploy/db/visualizacion.db con rutas "
                             "absolutas, sin copiar medios.")
    parser.add_argument('--transcode', action='store_true',
                        help="Transcodificar videos grandes a MP4/H.264 web (opcional; por defecto "
                             "solo se copian tal cual).")
    parser.add_argument('--transcode-box', default='1280x720',
                        help="Caja destino para videos regulares sobredimensionados (WxH). Default: 1280x720.")
    parser.add_argument('--transcode-360-largo', type=int, default=1440,
                        help="Lado mayor para videos 360° equirectangulares. Default: 1440.")
    parser.add_argument('--dry-run', action='store_true',
                        help="Previsualizar (deploy) sin copiar ni escribir.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format='%(message)s')

    mapa_tamanos: dict[int, int] = {}
    if args.snapshot_local:
        viz_db = VIZ_DB
        # No copia medios ni transcodifica: snapshot local con rutas absolutas.
    else:
        deploy_dir = os.path.abspath(args.deploy_dir)
        caja = _parsear_dimension(args.transcode_box)
        if args.transcode_360_largo <= 0:
            raise SystemExit("--transcode-360-largo debe ser mayor a 0")
        transcode_activo = args.transcode
        mapa_tamanos = _copiar_y_transcodificar_medios(
            FLUJOS_DB, deploy_dir, transcode_activo, caja, args.transcode_360_largo, args.dry_run
        )
        viz_db = os.path.join(deploy_dir, 'db', 'visualizacion.db')
        if args.dry_run:
            return

    print(f"Leyendo {FLUJOS_DB}...")
    src = sqlite3.connect(FLUJOS_DB)
    src.row_factory = sqlite3.Row

    # Obtener metadata tags de media_metadata
    meta = {}
    cur = src.execute("""
        SELECT media_id, key, value
        FROM media_metadata
        WHERE key IN (
            'dia_semana', 'weather_label', 'ia_description',
            'whisper_segments', 'ia_keywords', 'texto_completo',
            'titulo_seccion', 'ia_keywords_texto',
            'ia_keywords_transcripcion', 'ia_keywords_sonido'
        )
    """)
    for r in cur:
        meta.setdefault(r['media_id'], {})[r['key']] = r['value']

    # Transcripción: concatenar los textos de whisper_segments
    def extraer_transcripcion(valor):
        if not valor:
            return None
        try:
            segs = json.loads(valor)
            if not isinstance(segs, list):
                return None
            textos = [str(s.get('texto', '')).strip() for s in segs if isinstance(s, dict) and s.get('texto')]
            if not textos:
                return None
            txt = ' '.join(textos).strip()
            if len(txt) > 600:
                txt = txt[:597] + '...'
            return txt
        except Exception:
            return None

    def unir_keywords(*valores):
        """Une listas de keywords separadas por coma, preservando orden y deduplicando."""
        vistas = set()
        salida = []
        for valor in valores:
            if not valor:
                continue
            partes = [p.strip() for p in str(valor).split(',') if p.strip()]
            for parte in partes:
                clave = parte.lower()
                if clave in vistas:
                    continue
                vistas.add(clave)
                salida.append(parte)
        return ', '.join(salida) if salida else None

    # Obtener embeddings 2D (si existen)
    embs = {}
    try:
        cur = src.execute("SELECT media_id, embedding FROM media_embeddings WHERE modelo='nomic-embed-text'")
        for r in cur:
            blob = r['embedding']
            if blob and len(blob) >= 16:
                # Podría ser embedding de 768 o de 2 dims. Guardamos como placeholder.
                embs[r['media_id']] = blob
    except:
        pass

    # Obtener registros
    cur = src.execute("""
        SELECT id, filename_original, carpeta, type, subtype,
               filepath_absoluto, filepath_relativo, size_bytes, timestamp_utc, duration_secs,
               latitude, longitude, localidad, municipio, provincia,
               author,
               color_1_hex, color_1_name_basic,
               color_2_hex, color_2_name_basic,
               color_3_hex, color_3_name_basic
        FROM media
        ORDER BY id
    """)
    filas = cur.fetchall()
    print(f"  {len(filas)} registros leídos")

    # Construir visualizacion.db
    if os.path.exists(viz_db):
        os.remove(viz_db)
    os.makedirs(os.path.dirname(viz_db), exist_ok=True)
    dst = sqlite3.connect(viz_db)
    dst.execute("PRAGMA journal_mode=WAL")
    dst.execute("PRAGMA foreign_keys=ON")

    dst.executescript("""
        CREATE TABLE medios (
            id INTEGER PRIMARY KEY,
            archivo TEXT NOT NULL,
            carpeta TEXT,
            tipo TEXT,
            subtipo TEXT,
            ruta_absoluta TEXT,
            ruta_relativa TEXT NOT NULL,
            tamano_bytes INTEGER,
            fecha TEXT,
            hora TEXT,
            franja_horaria TEXT,
            mes TEXT,
            anio TEXT,
            duracion_seg REAL,
            ancho INTEGER,
            alto INTEGER,
            latitud REAL,
            longitud REAL,
            localidad TEXT,
            municipio TEXT,
            provincia TEXT,
            autor TEXT,
            color_1 TEXT,
            color_1_hex TEXT,
            color_2 TEXT,
            color_2_hex TEXT,
            color_3 TEXT,
            color_3_hex TEXT,
            dia_semana TEXT,
            clima TEXT,
            descripcion TEXT,
            keywords TEXT,
            transcripcion TEXT,
            titulo TEXT,
            embedding_x REAL,
            embedding_y REAL,
            cluster REAL
        );

        CREATE TABLE categorias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            grupo TEXT NOT NULL,
            valor TEXT NOT NULL,
            conteo INTEGER DEFAULT 0,
            UNIQUE(grupo, valor)
        );

        CREATE TABLE medio_categoria (
            medio_id INTEGER NOT NULL,
            categoria_id INTEGER NOT NULL,
            PRIMARY KEY(medio_id, categoria_id)
        );

        CREATE TABLE telegram_messages (
            id INTEGER PRIMARY KEY,
            message_id INTEGER,
            chat_id INTEGER,
            from_name TEXT,
            text TEXT,
            date_utc TEXT,
            message_type TEXT,
            has_media INTEGER DEFAULT 0,
            fotos TEXT
        );

        CREATE TABLE keypoints (
            id INTEGER PRIMARY KEY,
            media_id INTEGER NOT NULL,
            kp_key TEXT NOT NULL,
            value TEXT,
            offset_secs REAL,
            timestamp_absolute TEXT,
            media_tipo TEXT,
            media_subtipo TEXT,
            archivo TEXT,
            carpeta TEXT,
            latitud REAL,
            longitud REAL,
            posicion_fuente TEXT,
            fecha TEXT,
            hora TEXT
        );
        CREATE INDEX idx_kp_key ON keypoints(kp_key);
        CREATE INDEX idx_kp_media ON keypoints(media_id);
        CREATE INDEX idx_kp_ts ON keypoints(timestamp_absolute);
    """)

    insert_sql = """
        INSERT INTO medios (
            id, archivo, carpeta, tipo, subtipo,
            ruta_absoluta, ruta_relativa, tamano_bytes,
            fecha, hora, mes, anio, duracion_seg,
            latitud, longitud, localidad, municipio, provincia,
            autor,
            color_1, color_1_hex,
            color_2, color_2_hex,
            color_3, color_3_hex,
            dia_semana, clima, descripcion, keywords, transcripcion, titulo
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    count = 0
    for r in filas:
        ts = r['timestamp_utc']
        fecha = hora = mes = anio = None
        if ts:
            try:
                dt = datetime.fromisoformat(ts)
                fecha = dt.strftime('%Y-%m-%d')
                hora = dt.strftime('%H:%M')
                mes = str(dt.month)
                anio = str(dt.year)
            except:
                pass

        m = meta.get(r['id'], {})
        dia_sem = m.get('dia_semana')
        clima = m.get('weather_label')
        es_texto = (r['type'] == 'text')
        desc = m.get('texto_completo') or m.get('ia_description')
        if r['type'] == 'audio':
            keywords = unir_keywords(m.get('ia_keywords_sonido'), m.get('ia_keywords_transcripcion'))
        elif r['type'] == 'video':
            keywords = unir_keywords(
                m.get('ia_keywords'),
                m.get('ia_keywords_transcripcion'),
                m.get('ia_keywords_sonido'),
            )
        elif es_texto:
            keywords = unir_keywords(m.get('ia_keywords_texto'), m.get('ia_keywords'))
        else:
            keywords = m.get('ia_keywords')
        # Titulo propio del texto (subtitulo `##`). NULL si el texto no tiene
        # titulo o no es texto: la web muestra la cabecera solo si existe.
        titulo = (m.get('titulo_seccion') or None) if es_texto else None

        if not args.snapshot_local:
            # Ruta web-relativa (slash '/') + tamaño del archivo web si se transcodificó.
            ruta_abs = _ruta_web_relativa(r['carpeta'], r['filename_original'])
            tamano = mapa_tamanos.get(r['id'], r['size_bytes'])
        else:
            ruta_abs = resolver_ruta_absoluta(r['filepath_absoluto'])
            tamano = r['size_bytes']

        vals = (
            r['id'],
            r['filename_original'],
            r['carpeta'],
            r['type'],
            r['subtype'],
            ruta_abs,
            r['filepath_relativo'],
            tamano,
            fecha, hora, mes, anio,
            r['duration_secs'],
            r['latitude'], r['longitude'],
            r['localidad'], r['municipio'], r['provincia'],
            r['author'],
            r['color_1_name_basic'], r['color_1_hex'],
            r['color_2_name_basic'], r['color_2_hex'],
            r['color_3_name_basic'], r['color_3_hex'],
            dia_sem, clima, desc, keywords, extraer_transcripcion(m.get('whisper_segments')),
            titulo
        )
        try:
            dst.execute(insert_sql, vals)
            count += 1
        except Exception as e:
            print(f"  Error insertando id {r['id']}: {e}")

    dst.commit()

    # Actualizar categorías desde tipos
    cur = dst.execute("SELECT tipo, COUNT(*) FROM medios WHERE tipo IS NOT NULL GROUP BY tipo")
    for r in cur:
        dst.execute("INSERT OR IGNORE INTO categorias (grupo, valor, conteo) VALUES ('tipo', ?, ?)", (r[0], r[1]))
    dst.commit()

    # ── Exportar Telegram (chat) ─────────────────────────────
    # Mapa: telegram_messages.id (PK) → lista de media_ids de fotos
    fotos_map = {}
    for r in src.execute("""
        SELECT message_id, media_id FROM telegram_media
        WHERE media_type = 'photo' AND media_id IS NOT NULL
        ORDER BY message_id, media_order
    """):
        fotos_map.setdefault(r['message_id'], []).append(r['media_id'])

    # Mapa: id → tiene media adjunta (para has_media)
    has_media_ids = set(r[0] for r in src.execute("SELECT DISTINCT message_id FROM telegram_media"))

    tg_count = 0
    cur = src.execute("""
        SELECT id, message_id, chat_id, from_name, text, date_utc, message_type
        FROM telegram_messages
        ORDER BY id
    """)
    for r in cur:
        mid = r['id']
        fotos_json = json.dumps(fotos_map.get(mid, []))
        has = 1 if mid in has_media_ids else 0
        dst.execute(
            "INSERT INTO telegram_messages (id, message_id, chat_id, from_name, text, date_utc, message_type, has_media, fotos)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (mid, r['message_id'], r['chat_id'], r['from_name'], r['text'], r['date_utc'], r['message_type'], has, fotos_json)
        )
        tg_count += 1
    dst.commit()

    # ── Exportar keypoints (con posición materializada) ────────────
    def _norm_dt(ts: str | None):
        if not ts:
            return None
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (ValueError, TypeError):
            return None

    tracks = []
    try:
        import sys
        if BASE not in sys.path:
            sys.path.insert(0, BASE)
        from scripts.track_gpx import cargar_tracks, interpolar_posicion
        tracks = cargar_tracks(src)
        if tracks:
            print(f"  Tracks GPX cargados: {len(tracks)}")
        else:
            print("  Tracks GPX: 0 (sin posición interpolada para keypoints sin GPS propio)")
    except Exception as e:
        print(f"  Aviso: no se pudieron cargar tracks GPX ({e})")

    def _resolver_posicion(kp_media_lat, kp_media_lon, kp_ts_abs):
        if kp_media_lat is not None and kp_media_lon is not None:
            return kp_media_lat, kp_media_lon, "media"
        dt = _norm_dt(kp_ts_abs)
        if dt is None or not tracks:
            return None, None, None
        try:
            for tr in tracks:
                pos = interpolar_posicion(tr["puntos_tiempo"], dt)
                if pos is not None:
                    lat, lon, _ele = pos
                    return lat, lon, "track"
        except Exception:
            pass
        return None, None, None

    try:
        kp_rows = src.execute("""
            SELECT k.id, k.media_id, k.key, k.value, k.timestamp_offset_secs,
                   k.timestamp_absolute,
                   m.type, m.subtype, m.filename_original, m.carpeta,
                   m.latitude, m.longitude
            FROM media_keypoints k
            JOIN media m ON m.id = k.media_id
            ORDER BY k.id
        """).fetchall()
    except sqlite3.OperationalError as e:
        print(f"  Aviso: no se pudo leer media_keypoints ({e})")
        kp_rows = []

    kp_insert = """
        INSERT INTO keypoints
            (id, media_id, kp_key, value, offset_secs, timestamp_absolute,
             media_tipo, media_subtipo, archivo, carpeta,
             latitud, longitud, posicion_fuente, fecha, hora)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    kp_count = 0
    kp_con_pos = 0
    for r in kp_rows:
        lat, lon, fuente = _resolver_posicion(r["latitude"], r["longitude"], r["timestamp_absolute"])
        if lat is not None:
            kp_con_pos += 1
        ts_abs = r["timestamp_absolute"]
        fecha_kp = hora_kp = None
        if ts_abs:
            try:
                dt = _norm_dt(ts_abs)
                if dt:
                    fecha_kp = dt.strftime("%Y-%m-%d")
                    hora_kp = dt.strftime("%H:%M")
            except Exception:
                pass
        try:
            dst.execute(kp_insert, (
                r["id"], r["media_id"], r["key"], r["value"], r["timestamp_offset_secs"],
                r["timestamp_absolute"],
                r["type"], r["subtype"], r["filename_original"], r["carpeta"],
                lat, lon, fuente, fecha_kp, hora_kp,
            ))
            kp_count += 1
        except Exception as e:
            print(f"  Error insertando keypoint {r['id']}: {e}")
    dst.commit()
    print(f"  Keypoints exportados: {kp_count} (con posición: {kp_con_pos}, sin: {kp_count - kp_con_pos})")
    if kp_count:
        try:
            for row in dst.execute("SELECT kp_key, COUNT(*) c FROM keypoints GROUP BY kp_key ORDER BY c DESC"):
                print(f"    {row[0]}: {row[1]}")
        except Exception:
            pass

    # Resumen
    print(f"\n  Insertados: {count} registros")
    cur = dst.execute("SELECT tipo, COUNT(*) FROM medios GROUP BY tipo ORDER BY COUNT(*) DESC")
    for r in cur:
        print(f"    {r[0]}: {r[1]}")
    cur = dst.execute("SELECT MIN(fecha), MAX(fecha) FROM medios")
    dr = cur.fetchone()
    print(f"  Rango fechas: {dr[0]} -> {dr[1]}")
    cur = dst.execute("SELECT COUNT(*) FROM medios WHERE provincia IS NOT NULL")
    print(f"  Con provincia: {cur.fetchone()[0]}")
    cur = dst.execute("SELECT COUNT(*) FROM medios WHERE latitud IS NOT NULL")
    print(f"  Con GPS: {cur.fetchone()[0]}")
    cur = dst.execute("SELECT COUNT(*) FROM medios WHERE municipio IS NOT NULL")
    print(f"  Con municipio: {cur.fetchone()[0]}")
    cur = dst.execute("SELECT COUNT(*) FROM telegram_messages")
    print(f"  Telegram mensajes: {cur.fetchone()[0]}")
    cur = dst.execute("SELECT COUNT(*) FROM telegram_messages WHERE fotos IS NOT NULL AND fotos != '[]'")
    print(f"  Telegram con fotos: {cur.fetchone()[0]}")

    src.close()
    dst.close()
    print(f"\nOK {viz_db} actualizada ({count} registros, {tg_count} mensajes telegram)")


if __name__ == '__main__':
    main()
