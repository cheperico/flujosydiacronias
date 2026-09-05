#!/usr/bin/env python3
"""
Flujos y Diacronías - Punto de entrada unificado

Uso:
  python flujos.py                                  -> Menu interactivo
  python flujos.py --tui                            -> Menu interactivo
  python flujos.py ingest --root D:/Medios ...      -> Ingestar medios
  python flujos.py query --distinct author --count   -> Consultar DB
  python flujos.py relocate --new-root E:/Medios     -> Relocalizar archivos
  python flujos.py check-db                          -> Inspeccionar DB
  python flujos.py check-gps                         -> Revisar GPS en archivos
  python flujos.py geocode                           -> Geocodificar coordenadas GPS a localidad/provincia
  python flujos.py geocode --limit 100               -> Con limite de registros
  python flujos.py gradient                          -> Calcular gradientes de ruta (pendiente/esfuerzo fisico entre puntos GPS)
  python flujos.py gradient --dry-run                -> Previsualizar gradientes sin escribir en DB
  python flujos.py mover --new-root NUEVA_RAIZ --mode mover  -> Mover archivos a nueva ubicación
  python flujos.py mover --new-root NUEVA_RAIZ --mode copiar --update-db  -> Copiar archivos y actualizar DB
  python flujos.py import-telegram -e RUTA_AL_EXPORT      -> Importar chat de Telegram a la DB
  python flujos.py --help | --ayuda | -h                   -> Esta ayuda
"""

import argparse
import io
import os
import sqlite3
import subprocess
import sys
from typing import Callable

# Forzar UTF-8 en consola Windows para poder usar caracteres Unicode.
# reconfigurar in-place: no reasigna sys.stdout ni toma posesión del buffer,
# evita que módulos importados (ej: check_db_data) rompan el flujo con
# "I/O operation on closed file" al re-empaquetar el mismo buffer.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        # Fallback: reemplazo directo (sin double-wrap posterior)
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Asegurar que scripts/ este en el path para imports relativos
_scripts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)


# ── Ayuda ────────────────────────────────────────────────────────────────────

AYUDA = """
███████╗██╗     ██╗   ██╗     ██╗ ██████╗ ███████╗    ██╗   ██╗
██╔════╝██║     ██║   ██║     ██║██╔═══██╗██╔════╝    ╚██╗ ██╔╝
█████╗  ██║     ██║   ██║     ██║██║   ██║███████╗     ╚████╔╝
██╔══╝  ██║     ██║   ██║██   ██║██║   ██║╚════██║      ╚██╔╝
██║     ███████╗╚██████╔╝╚█████╔╝╚██████╔╝███████║       ██║
╚═╝     ╚══════╝ ╚═════╝  ╚════╝  ╚═════╝ ╚══════╝       ╚═╝
██████╗ ██╗ █████╗  ██████╗██████╗  ██████╗ ███╗   ██╗██╗ █████╗ ███████╗
██╔══██╗██║██╔══██╗██╔════╝██╔══██╗██╔═══██╗████╗  ██║██║██╔══██╗██╔════╝
██║  ██║██║███████║██║     ██████╔╝██║   ██║██╔██╗ ██║██║███████║███████╗
██║  ██║██║██╔══██║██║     ██╔══██╗██║   ██║██║╚██╗██║██║██╔══██║╚════██║
██████╔╝██║██║  ██║╚██████╗██║  ██║╚██████╔╝██║ ╚████║██║██║  ██║███████║
╚═════╝ ╚═╝╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝╚═╝  ╚═╝╚══════╝

  Buenos Aires -> Tucuman                           

USO:
  python flujos.py <comando> [opciones]

COMANDOS:

  ingest      Ingerir medios desde una carpeta a la base de datos.
              Ej: python flujos.py ingest --root D:/Medios --verbose

  query       Consultar y explorar la base de datos.
              Ej: python flujos.py query --distinct author --count

  relocate    Actualizar rutas absolutas cuando los archivos se mudan.
              Ej: python flujos.py relocate --new-root E:/Medios

   check-db    Mostrar resumen de la DB (totales, salud, batches, GPS, signo).
               Opciones: --db RUTA --verbose/-v --limit N
               Ej: python flujos.py check-db --verbose

   check-gps   Revisar que archivos tienen GPS (via ExifTool).
               Opciones: --db RUTA --exiftool RUTA --samples N --folder CARPETA
               Ej: python flujos.py check-gps --folder D:/Fotos --samples 10

   check-data  Stats de clima/dia/geocode (weather, dia_semana, municipio).
   (alias:      Opciones: --db RUTA --limit N
    check-clima, Ej: python flujos.py check-data --limit 20
    check-geo)

   geocode     Geocodificar coordenadas GPS (lat,lon) a provincia/localidad
               usando la API Georef Argentina (batch).
               Opciones: --db RUTA --limit N --mode skip|update|replace --dry-run
                         --coords LAT,LON
               Ej: python flujos.py geocode --limit 100 --mode update

   gradient    Calcular gradientes de ruta entre puntos GPS consecutivos.
               Calcula distancia, pendiente y esfuerzo fisico acumulado.
               Opciones: --db RUTA --mode skip|update|replace --dry-run
                         --verbose/-v --quiet/-q
               Ej: python flujos.py gradient --mode update --verbose

   astronomia  Calcular posicion del sol y clasificar twilight (NOAA).
               Calcula elevacion, azimut y momento del dia para cada
               registro con GPS + timestamp.
               Opciones: --db RUTA --mode skip|update|replace --dry-run --verbose/-v
               Ej: python flujos.py astronomia --mode update --verbose

   undo-ingest       Deshacer una ingesta por batch ID (alias: undo).
   (alias: undo)

   backfill-end-time Calcular end_time para registros existentes
   (alias:           que no lo tengan (migracion).
    backfill)

   improve-db        Ejecutar pasos de mejora sobre la DB (9 pasos: colors,
                     keywords, descriptions, combinado, transcribe, keypoints,
                     timestamps, gps, video_metadata).
                     Opciones: --steps X,Y --mode skip|update|replace --db RUTA
                               --list --no-mostrar --workers N
                     Ej: python flujos.py improve-db --steps keywords --mode update

   analizar-video    Analizar videos con IA: scene detection + muestreo por
   (alias: analizar)  escena + keywords (minicpm-v4.6).
                     Opciones: --file RUTA --db RUTA --folder CARPETA --modelo MODELO --por-escena N --mejores-por-escena N
                               --max-duracion-escena S --sensibilidad 0-1 --no-proxy --dry-run --limit N --json
                     Ej: python flujos.py analizar-video --dry-run

   keypoints-contexto Keypoints de contexto (devenir geografico) contra los
   (alias: keypoints)tracks GPX: elevacion, astronomia, movimiento, ubicacion
                     y clima en media_keypoints.
                     Opciones: --db RUTA --mode skip|update|replace --intervalo 30 --frecuencia-gruesa 300 --umbral-* --dry-run --cache/--no-cache
                     Ej: python flujos.py keypoints-contexto --mode update --dry-run

   mapa              Generar un mapa HTML interactivo con Folium
                     a partir del track GPX y los GPS de la BD.
                     Opciones: --output/-o RUTA --db RUTA --no-markers --road-colors --tolerancia-metros 1000 --umbral-gap-aviso 1800 --assets-cache DIR
                     Ej: python flujos.py mapa --road-colors --tolerancia-metros 500

    mapa-municipios   Genera un mapa HTML por municipio recorrido, con variantes
    (alias: mapas)    (ruta, puntos, contexto, gradiente). Nombre:
                      mapa_municipio_<municipio>_<variante>.html (sin acentos:
                      'Río Hondo' -> 'Rio_Hondo')
                      Opciones: --output/-o DIR --db RUTA --variantes LISTA --municipio SUBSTR --mode skip|update --dry-run
                                --tolerancia-metros 1000 --umbral-gap-aviso 1800 --no-embebido --zooms LISTA --tiles-cache DIR --assets-cache DIR
                      Ej: python flujos.py mapa-municipios --variantes ruta,puntos --mode update

    mapa-unificado    Mapa unificado offline/online con clusters multicolor
    (alias: unificado, y expansión de transcripción por segmentos en el mismo mapa.
     unificado)        Opciones: --modo offline|online --db RUTA --output/-o RUTA --no-contexto --con-waypoints --sin-cluster --sin-segmentos --dry-run
                      --tiles-cache DIR --assets-cache DIR
                      Ej: python flujos.py mapa-unificado --modo offline
                      Ej: python flujos.py mapa-unificado --modo online --db deploy/db/visualizacion.db

   export-csv        Exporta todas las tablas de la DB a archivos CSV (alias: csv).
   (alias: csv)      Opciones: --db RUTA --table TABLA --output DIR --list-tables --dry-run
                     Ej: python flujos.py export-csv --table media
                     Ej: python flujos.py csv --output ./mis_exports

   reset-db          Hace backup de la DB actual y crea una nueva
   (alias: reset)     desde cero (schema limpio).

   backup-db         Solo backup (sin borrar): copia la DB actual con timestamp.
   (alias: backup)

   restore-db        Restaura la DB desde un backup previo.
   (alias: restore)

   import-telegram  Importar un export de Telegram (chats, mensajes, multimedia).
   (alias: tg)       Opciones: -e/--export-path RUTA --mode skip|update|replace
                    --include-system/--no-system --ingest-media/--no-ingest --dry-run --db RUTA
                    Ej: python flujos.py import-telegram -e RUTA_AL_EXPORT --mode update

   ingest-textos    Ingerir textos .md de la carpeta textos/ como medios type='text' (alias: textos).
   (alias: textos)   Opciones: --root CARPETA --db RUTA --mode skip|update|replace --dry-run
                    Ej: python flujos.py ingest-textos --mode update

   ingest-gpx       Ingerir un archivo GPX (tracks/waypoints + backfill altitud) (alias: gpx).
   (alias: gpx)      Opciones: --gpx RUTA --db RUTA --mode skip|update|replace --dry-run
                    --no-altitude --no-waypoints
                    Ej: python flujos.py ingest-gpx --gpx tracks/ruta.gpx

   corregir-360     Corregir timestamps de videos 360° Insta360 post-ingesta
   (alias:           (CreateDate UTC -> ART, gap del track). Temporario, borrar al estabilizar.
    corregir360)     Opciones: --db RUTA --mode skip|update|replace --dry-run --reubicar --json --verbose/-v
                    Ej: python flujos.py corregir-360 --mode update --reubicar --dry-run

   mover            Mover o copiar archivos a nueva ubicacion y actualizar DB.
   (alias: mover)    Opciones: --new-root RUTA --old-root RUTA --mode mover|copiar --update-db --dry-run --db RUTA
                    Ej: python flujos.py mover --new-root NUEVA_RAIZ --mode mover

   detectar-contenedores  Auditar contenedores de video/audio con ffprobe (alias: contenedores)
   (alias:           (streams faltantes, estado por medio).
    contenedores)    Opciones: --db RUTA --mode skip|update|replace --type TIPO --ffprobe RUTA --dry-run --verbose
                    Ej: python flujos.py detectar-contenedores --mode update --dry-run

   limpiar-descripciones  Limpiar descripciones con eco del prompt (meta-intros) (alias: descripciones).
   (alias:           Opciones: --db RUTA --mode skip|update|replace --dry-run --solo-en --solo-es --verbose --no-backup
    descripciones)  Ej: python flujos.py limpiar-descripciones --mode update

   repetir-contenido      Buscar contenido repetido por coincidencias de audio (alias: repetidos).
   (alias: repetidos)Opciones: --db RUTA --contra RUTA --limite N --umbral 0.80 --min-duracion-segs 4 --top 20 --json --verbose
                    Ej: python flujos.py repetir-contenido --contra C:/audio.mp3 --top 20

   audio-frame      Correlacionar contenido de audio con frames de video (alias: crossref).
   (alias: crossref) Opciones: --db RUTA --archivo RUTA --media-id ID --top-k N --umbral 0.80 --cada-segundos N --frames-dir DIR --modelo CED --json
                    Ej: python flujos.py audio-frame --archivo C:/video.mp4

  --tui       Menu interactivo (tambien sin argumentos).

  --help, --ayuda, -h   Esta ayuda.

Si no se pasa ningun comando, arranca el menu interactivo.
"""


# ── TUI ──────────────────────────────────────────────────────────────────────

def limpiar_pantalla():
    os.system("cls" if sys.platform == "win32" else "clear")


def pausa():
    input("\n  Presiona Enter para continuar...")


def _buscar_gpx_disponibles() -> list[str]:
    """Busca archivos .gpx en tracks/ (y en la raiz del proyecto) para
    ofrecerlos como opciones en el menu de ingesta GPX. Devuelve rutas
    ordenadas alfabeticamente (absolutas si estan fuera de la raiz)."""
    raiz = os.path.dirname(os.path.abspath(__file__))
    carpetas = [os.path.join(raiz, "tracks"), raiz]
    candidatos: list[str] = []
    vistos: set[str] = set()
    for carpeta in carpetas:
        if not os.path.isdir(carpeta):
            continue
        for nombre in sorted(os.listdir(carpeta)):
            if nombre.lower().endswith(".gpx"):
                ruta = os.path.join(carpeta, nombre)
                if ruta not in vistos:
                    vistos.add(ruta)
                    candidatos.append(ruta)
    return candidatos


def mostrar_bienvenida():
    limpiar_pantalla()
    print("███████╗██╗     ██╗   ██╗     ██╗ ██████╗ ███████╗    ██╗   ██╗")
    print("██╔════╝██║     ██║   ██║     ██║██╔═══██╗██╔════╝    ╚██╗ ██╔╝")
    print("█████╗  ██║     ██║   ██║     ██║██║   ██║███████╗     ╚████╔╝")
    print("██╔══╝  ██║     ██║   ██║██   ██║██║   ██║╚════██║      ╚██╔╝")
    print("██║     ███████╗╚██████╔╝╚█████╔╝╚██████╔╝███████║       ██║")
    print("╚═╝     ╚══════╝ ╚═════╝  ╚════╝  ╚═════╝ ╚══════╝       ╚═╝")
    print("██████╗ ██╗ █████╗  ██████╗██████╗  ██████╗ ███╗   ██╗██╗ █████╗ ███████╗")
    print("██╔══██╗██║██╔══██╗██╔════╝██╔══██╗██╔═══██╗████╗  ██║██║██╔══██╗██╔════╝")
    print("██║  ██║██║███████║██║     ██████╔╝██║   ██║██╔██╗ ██║██║███████║███████╗")
    print("██║  ██║██║██╔══██║██║     ██╔══██╗██║   ██║██║╚██╗██║██║██╔══██║╚════██║")
    print("██████╔╝██║██║  ██║╚██████╗██║  ██║╚██████╔╝██║ ╚████║██║██║  ██║███████║")
    print("╚═════╝ ╚═╝╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝╚═╝  ╚═╝╚══════╝")
    print()
    print("  Buenos Aires -> Tucuman")
    print()


def leer_db(db_path: str | None = None) -> str:
    """Resuelve la ruta a la DB. Si se pasa una, la usa; si no, la default."""
    if db_path:
        return os.path.abspath(db_path)
    return os.path.join(os.path.dirname(__file__), "db", "flujos.db")


def resumen_db(conn) -> str:
    """Devuelve un resumen con los totales de la DB (DRY: delega en db.util)."""
    try:
        from db.util import resumen_texto
        return resumen_texto(conn)
    except Exception:
        # Fallback si util no está disponible
        total = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
        imagenes = conn.execute("SELECT COUNT(*) FROM media WHERE type='image'").fetchone()[0]
        videos = conn.execute("SELECT COUNT(*) FROM media WHERE type='video'").fetchone()[0]
        audios = conn.execute("SELECT COUNT(*) FROM media WHERE type='audio'").fetchone()[0]
        textos = conn.execute("SELECT COUNT(*) FROM media WHERE type='text'").fetchone()[0]
        otros = total - imagenes - videos - audios - textos
        return (
            f"  Total:      {total:>6d}\n"
            f"  Imagenes:   {imagenes:>6d}\n"
            f"  Videos:     {videos:>6d}\n"
            f"  Audios:     {audios:>6d}\n"
            f"  Textos:     {textos:>6d}\n"
            f"  Otros:      {otros:>6d}"
        )


def _preguntar_sn(pregunta: str, default: bool = False) -> bool:
    """Confirma una pregunta si/no. default=True muestra (S/n) (acepta cualquier
    tecla excepto 'n' como sí). Devuelve bool."""
    sufijo = " (S/n)" if default else " (s/N)"
    r = input(f"  ?{pregunta}?{sufijo}: ").strip().lower()
    if default:
        return r != "n"
    return r == "s"


def _menu(titulo: str, opciones: dict[str, tuple[str, Callable]], db_path: str | None = None,
          intro: str | None = None, titulo_ancho: int | None = None,
          pre_titulo: Callable[[], None] | None = None,
          etiqueta_salir: str = "Volver",
          on_salir: Callable[[], None] | None = None,
          cerrar_al_ejecutar: bool = False):
    """Menu generico con loop: imprime titulo, opciones ("1) nombre"),
    pide "  Opcion: ", ejecuta la funcion asociada a la clave.
    La clave "0" (volver) esta reservada y SIEMPRE rompe el loop;
    si on_salir esta definido, se ejecuta antes de romper.
    Si la clave no existe imprime "  Opcion invalida." y pausa().
    Las funciones reciben db_path como argumento.
    intro: linea opcional impresa tras el titulo (seguida de una linea en blanco).
    titulo_ancho: si se pasa, envuelve el titulo con ese numero de '=' a cada lado
    (por defecto usa '=== {titulo} ===').
    pre_titulo: callable que reemplaza a limpiar_pantalla + titulo + intro al inicio
    de cada iteracion. Cuando esta presente NO se limpia la pantalla (el callable lo
    hace, ej: mostrar_bienvenida) ni se imprime el titulo ni el intro.
    etiqueta_salir: texto de la ultima opcion "0" (default "Volver").
    on_salir: callable opcional ejecutado al elegir "0" (antes de romper el loop).
    cerrar_al_ejecutar: si es True, tras ejecutar una opcion valida el loop rompe
    (en lugar de volver a mostrar el menu)."""
    while True:
        if pre_titulo:
            pre_titulo()
        else:
            limpiar_pantalla()
            if titulo_ancho:
                print(f"{'=' * titulo_ancho} {titulo} {'=' * titulo_ancho}\n")
            else:
                print(f"=== {titulo} ===\n")
            if intro:
                print(intro)
                print()
        for clave, (etiqueta, _llamada) in opciones.items():
            print(f"  {clave}) {etiqueta}")
        print(f"  0) {etiqueta_salir}\n")

        opc = input("  Opcion: ").strip()
        if opc == "0":
            if on_salir:
                on_salir()
            break
        if opc in opciones:
            _, llamada = opciones[opc]
            llamada(db_path)
            if cerrar_al_ejecutar:
                break
        else:
            print("  Opcion invalida.")
            pausa()


def _menu_paginado(titulo: str, hojas: list[tuple[str, dict]], db_path: str | None = None):
    """Menu paginado: hasta 9 opciones por hoja, navegacion n/p/0.
    hojas: lista de (subtitulo, opciones) donde opciones es dict[clave -> (etiqueta, callable)]
    (igual formato que _menu). Imprime '=== titulo ===', el subtitulo de la hoja actual
    (con su salto de linea incluido) y las opciones '  clave) etiqueta'.
    Navegacion: si hay hoja anterior '  p) << Anterior', si hay siguiente
    '  n) Siguiente >>' (en hojas con ambas, p se lista primero, luego n).
    Siempre '  0) Volver'. En la ultima hoja 'n' es invalida y en la primera 'p' es invalida
    ('  Opcion invalida.' + pausa()). El indice de hoja arranca en la primera."""
    num_hoja = 0
    total = len(hojas)
    while True:
        limpiar_pantalla()
        subtitulo, opciones = hojas[num_hoja]
        print(f"=== {titulo} ===\n")
        print(subtitulo)
        for clave, (etiqueta, _llamada) in opciones.items():
            print(f"  {clave}) {etiqueta}")
        if num_hoja > 0:
            print("  p) << Anterior")
        if num_hoja < total - 1:
            print("  n) Siguiente >>")
        print("  0) Volver\n")

        opc = input("  Opcion: ").strip()
        if opc == "0":
            break
        if opc.lower() in ("n", "next"):
            if num_hoja < total - 1:
                num_hoja += 1
            else:
                print("  Opcion invalida.")
                pausa()
            continue
        if opc.lower() in ("p", "prev"):
            if num_hoja > 0:
                num_hoja -= 1
            else:
                print("  Opcion invalida.")
                pausa()
            continue
        if opc in opciones:
            _, llamada = opciones[opc]
            llamada(db_path)
        else:
            print("  Opcion invalida.")
            pausa()


def _args_sn(args: list[str], flags: list[tuple[bool, str]]) -> list[str]:
    """Agrega a la lista args los flags cuya condicion booleana sea True.
    Cada tupla es (condicion, "--flag"). Devuelve la lista resultante."""
    for condicion, flag in flags:
        if condicion:
            args.append(flag)
    return args


def opcion_preparar(db_path: str | None = None):
    """Menu: Preparar medios (pre-ingesta)."""
    def _limpiar_tandas(db_path):
        from scripts import limpiar_tandas
        ruta = input("  Carpeta a limpiar: ").strip()
        if not ruta:
            print("  Cancelado.")
            pausa()
            return
        if not os.path.isdir(ruta):
            print("  Carpeta no encontrada.")
            pausa()
            return
        dry_run = input("  ?Solo previsualizar (s/N): ").strip().lower() == "s"
        limpiar_tandas.main([ruta] + (["--dry-run"] if dry_run else []))
        pausa()

    _menu("PREPARAR MEDIOS", {
        "1": ("Limpieza de tandas de fotografias", _limpiar_tandas),
    }, db_path)


def opcion_ingesta(db_path: str | None = None):
    """Menu: Ingesta de medios."""
    while True:
        limpiar_pantalla()
        print("=== INGESTA ===\n")
        print("  1) Ingerir multimedia (fotos, sonidos, videos, etc.)")
        print("  2) Ingerir track GPS (GPX)")
        print("  3) Ingerir textos (.md)")
        print("  4) Ingerir chat de Telegram")
        print("  5) Deshacer ingesta")
        print("  0) Volver\n")

        opc = input("  Opcion: ").strip()
        if opc == "1":
            limpiar_pantalla()
            print("=== HACER INGESTA ===\n")
            root = input("  Carpeta raiz a escanear: ").strip()
            if not root:
                print("  Cancelado.")
                pausa()
                continue
            if not os.path.isdir(root):
                print(f"  Error: la carpeta '{root}' no existe.")
                pausa()
                continue

            recursive = _preguntar_sn("Incluir subcarpetas")
            dry_run = _preguntar_sn("Solo previsualizar (dry-run)")

            # Selección de tipos de medio
            print("\n  Tipos de medio a ingerir (Enter = todos):")
            incluir_img = _preguntar_sn("Fotos")
            incluir_vid = _preguntar_sn("Videos")
            incluir_aud = _preguntar_sn("Audios")
            incluir_txt = _preguntar_sn("Textos")
            tipos_seleccionados = []
            if incluir_img: tipos_seleccionados.append("image")
            if incluir_vid: tipos_seleccionados.append("video")
            if incluir_aud: tipos_seleccionados.append("audio")
            if incluir_txt: tipos_seleccionados.append("text")

            # Manejo de archivos sin timestamp
            print("")
            permitir_sin_ts = _preguntar_sn("Ingerir archivos sin timestamp")

            custom_db = input(f"  ?Usar otra DB? (default: {leer_db(db_path)}) [Enter para default]: ").strip()

            tipo_str = ", ".join(tipos_seleccionados) if tipos_seleccionados else "todos"
            print(f"\n  Resumen: root={root}  recursivo={'SI' if recursive else 'NO'}  "
                  f"tipos={tipo_str}  sin_ts={'SI' if permitir_sin_ts else 'NO -> se salta'}"
                  f"  dry_run={'SI' if dry_run else 'NO'}")
            if not _preguntar_sn("Ejecutar ingesta"):
                print("  Cancelado.")
                pausa()
                continue

            print("\n  Ejecutando ingesta...\n")
            from scripts import ingest
            args = ["--root", root]
            _args_sn(args, [
                (recursive, "--recursive"),
                (permitir_sin_ts, "--allow-no-timestamp"),
                (dry_run, "--dry-run"),
            ])
            if tipos_seleccionados:
                args.extend(["--types", ",".join(tipos_seleccionados)])
            if custom_db:
                args.extend(["--db", custom_db])
            elif db_path:
                args.extend(["--db", db_path])
            ingest.main(args)
            pausa()

        elif opc == "2":
            opcion_ingestar_gpx(db_path)

        elif opc == "3":
            opcion_ingestar_textos(db_path)

        elif opc == "4":
            opcion_importar_telegram(db_path)

        elif opc == "5":
            opcion_undo_ingest(db_path)

        elif opc == "0":
            break
        else:
            print("  Opcion invalida.")
            pausa()


def opcion_importar_telegram(db_path: str | None = None):
    """Menu: importar chat de Telegram."""
    limpiar_pantalla()
    print("=== IMPORTAR CHAT DE TELEGRAM ===\n")

    export_path = input("  Ruta al directorio del export (con result.json): ").strip()
    if not export_path:
        print("  Cancelado.")
        pausa()
        return

    export_path = os.path.normpath(export_path)
    json_path = os.path.join(export_path, "result.json")
    if not os.path.isdir(export_path):
        print(f"  ❌ Directorio no encontrado: {export_path}")
        pausa()
        return
    if not os.path.isfile(json_path):
        print(f"  ❌ No se encuentra result.json en: {export_path}")
        pausa()
        return

    # Modo
    modo_str = _elegir_modo_pregunta(db_path, "importación",
                                     descripciones={"s": "— solo mensajes nuevos",
                                                    "u": "— actualiza existentes",
                                                    "r": "— limpia y reimporta todo"})
    if modo_str is None:
        print("  Cancelado.")
        pausa()
        return

    include_system = _preguntar_sn("Incluir mensajes de sistema", default=True)
    ingest_media = _preguntar_sn("Ingerir multimedia en tabla media", default=True)
    dry_run = _preguntar_sn("Solo previsualizar (dry-run)")

    print(f"\n  Resumen: export={export_path}  modo={modo_str}"
          f"  sistema={'SI' if include_system else 'NO'}"
          f"  ingest_media={'SI' if ingest_media else 'NO'}"
          f"  dry_run={'SI' if dry_run else 'NO'}")
    print("  (Los archivos quedan en la carpeta del export; para consolidar usá Mantenimiento → Mover/Copiar o consolidar_medios.py)")
    if not _preguntar_sn("Ejecutar importación"):
        print("  Cancelado.")
        pausa()
        return

    from scripts import import_telegram
    args = ["--export-path", export_path, "--mode", modo_str]
    _args_sn(args, [
        (not include_system, "--no-system"),
        (not ingest_media, "--no-ingest"),
        (dry_run, "--dry-run"),
    ])
    if db_path:
        args.extend(["--db", db_path])
    import_telegram.main(args)
    pausa()


def opcion_ingestar_textos(db_path: str | None = None):
    """Menu: ingerir textos desde archivos .md de una carpeta."""
    limpiar_pantalla()
    print("=== INGERIR TEXTOS ===\n")

    root = input("  Carpeta de textos (default: textos): ").strip() or "textos"
    if not os.path.isdir(root):
        print(f"  Error: la carpeta '{root}' no existe.")
        pausa()
        return

    modo_str = _elegir_modo_pregunta(db_path, "ingesta",
                                     descripciones={"s": "— solo textos nuevos",
                                                    "u": "— actualiza existentes",
                                                    "r": "— limpia y reingresa todo"})
    if modo_str is None:
        print("  Cancelado.")
        pausa()
        return

    dry_run = _preguntar_sn("Solo previsualizar (dry-run)")
    custom_db = input(f"  ?Usar otra DB? (default: {leer_db(db_path)}) [Enter para default]: ").strip()

    print(f"\n  Resumen: root={root}  modo={modo_str}  dry_run={'SI' if dry_run else 'NO'}")
    if not _preguntar_sn("Ejecutar ingesta"):
        print("  Cancelado.")
        pausa()
        return

    from scripts import ingest_textos
    args = ["--root", root, "--mode", modo_str]
    if dry_run:
        args.append("--dry-run")
    if custom_db:
        args.extend(["--db", custom_db])
    elif db_path:
        args.extend(["--db", db_path])
    ingest_textos.main(args)
    pausa()


def opcion_listar(db_path: str | None = None):
    """Submenu: listar distintos aspectos de la DB (paginado: 9 por hoja)."""
    from scripts import query
    db_flag = ["--db", db_path] if db_path else []

    def _preguntar_limit(default: int = 50) -> list[str]:
        lim = input(f"  Límite (Enter = {default}, 0 = sin límite, máx 500): ").strip()
        if lim == "":
            return ["--limit", str(default)]
        if lim == "0":
            return []
        if lim.isdigit() and int(lim) > 0:
            n = max(1, min(int(lim), 500))
            return ["--limit", str(n)]
        print(f"  Valor inválido, usando {default}.")
        return ["--limit", str(default)]

    def _buscar_texto(db):
        t = input("  Texto a buscar: ").strip()
        if t:
            query.main(["--search", t] + db_flag + _preguntar_limit())
        else:
            print("  Cancelado.")

    def _listar_key(db):
        k = input("  Key (ej: ia_keywords, whisper_estado, weather_label, dia_semana): ").strip()
        if k:
            query.main(["--key", k, "--count"] + db_flag + _preguntar_limit())
        else:
            print("  Cancelado.")

    def _distinct_custom(db):
        col = input("  Columna: ").strip()
        w = input("  WHERE (ej: type='image'): ").strip()
        if col and w:
            query.main(["--distinct", col, "--count", "--where", w] + db_flag + _preguntar_limit())
        elif col:
            query.main(["--distinct", col, "--count"] + db_flag + _preguntar_limit())
        else:
            print("  Cancelado.")

    def _consulta_libre(db):
        import shlex
        flags = input("  Flags (ej: --distinct type --count): ").strip()
        if not flags:
            return
        toks = shlex.split(flags)
        if "--db" not in toks and db_flag:
            toks += db_flag
        query.main(toks)

    hojas = [
        ("  -- Básico --\n", {
            "1": ("Tipos de medio", lambda db: query.main(["--distinct", "type", "--count"] + db_flag)),
            "2": ("Autores", lambda db: query.main(["--distinct", "author", "--count"] + db_flag + _preguntar_limit())),
            "3": ("Carpetas", lambda db: query.main(["--distinct", "carpeta", "--count"] + db_flag + _preguntar_limit())),
            "4": ("Colores básicos", lambda db: query.main(["--distinct", "color_1_name_basic", "--count", "--where", "color_1_name_basic IS NOT NULL"] + db_flag)),
            "5": ("Provincias (geocode)", lambda db: query.main(["--distinct", "provincia", "--count", "--where", "provincia IS NOT NULL"] + db_flag + _preguntar_limit())),
            "6": ("Municipios", lambda db: query.main(["--distinct", "municipio", "--count", "--where", "municipio IS NOT NULL"] + db_flag + _preguntar_limit())),
            "7": ("Buscar texto (media + metadata)", _buscar_texto),
            "8": ("Columnas y keys disponibles (--columns)", lambda db: query.main(["--columns"] + db_flag)),
            "9": ("Listar valores de una key de metadata", _listar_key),
        }),
        ("  -- Avanzado --\n", {
            "1": ("Keypoints: listar keys distintas", lambda db: query.main(["--distinct", "key", "--count", "--table", "media_keypoints"] + db_flag)),
            "2": ("Telegram chats (si hay)", lambda db: query.main(["--distinct", "name", "--table", "telegram_chats"] + db_flag)),
            "3": ("Distinct con filtro WHERE custom", _distinct_custom),
            "4": ("Consulta libre (flags directos a query.py)", _consulta_libre),
            "5": ("Revisar GPS en archivos", lambda db: opcion_check_gps(db)),
            "6": ("Detalle completo de registros (todas las columnas)", lambda db: opcion_detalle_db(db)),
        }),
    ]
    _menu_paginado("LISTAR", hojas, db_path)


def opcion_check_db_data(db_path: str | None = None):
    """Wrapper TUI para stats de clima/día/geocode (check_db_data.py)."""
    from scripts import check_db_data
    # Preguntar límite
    try:
        lim_str = input("  Muestras a mostrar [10]: ").strip() or "10"
        lim = max(1, min(int(lim_str), 50))
    except ValueError:
        lim = 10
    db_arg = ["--db", db_path] if db_path else []
    check_db_data.main(db_arg + ["--limit", str(lim)])
    pausa()


def opcion_consultar(db_path: str | None = None):
    """Menu: Consultar base de datos."""
    _menu("CONSULTAR BASE DE DATOS", {
        "1": ("Ver resumen de la DB", opcion_check_db),
        "2": ("Listar...", opcion_listar),
        "3": ("Stats clima / día / geocode", opcion_check_db_data),
        "4": ("Detalle de un medio por ID", opcion_detalle_por_id),
    }, db_path)


def opcion_relocalizar(db_path: str | None = None):
    """Menu para relocalizar medios."""
    limpiar_pantalla()
    print("=== RELOCALIZAR MEDIOS ===\n")

    db_path = leer_db(db_path)
    if os.path.isfile(db_path):
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.execute("SELECT value FROM config WHERE key = 'ingest_root'")
            row = cur.fetchone()
            if row:
                print(f"  Raiz actual en DB: {row[0]}")
        except sqlite3.OperationalError:
            pass
        conn.close()

    # Permitir --old-root (si config está vacío o es incorrecto)
    old_root = input("  Raíz anterior (vacío = usar ingest_root de la DB): ").strip()
    new_root = input("  Nueva raiz: ").strip()
    if not new_root:
        print("  Cancelado.")
        pausa()
        return
    # Sugerir consolidar si hay múltiples raíces
    try:
        conn = sqlite3.connect(db_path)
        raices = set()
        for (fp,) in conn.execute("SELECT filepath_absoluto FROM media"):
            # Detectar raíz heurística básica: 2 niveles desde unidad
            p = os.path.normpath(fp)
            parts = p.split(os.sep)
            # En Windows: C: + Users + Federico + Desktop + Flujos + ... → raíz = hasta item 4
            if len(parts) >= 4:
                raiz_guess = os.sep.join(parts[:4])
                raices.add(raiz_guess)
        conn.close()
        if len(raices) > 3:
            print(f"  ⚠ Detectadas {len(raices)} raíces distintas (ej: {list(raices)[:2]}).")
            print("    Si los medios están dispersos (Telegram/Testeos), usá Gestión de rutas → 3) Consolidar")
            if not _preguntar_sn("Continuar con relocalizar simple de todos modos"):
                print("  Cancelado — usá Consolidar.")
                pausa()
                return
    except Exception:
        pass

    if not os.path.isdir(new_root):
        r = input(f"  La carpeta '{new_root}' no existe. ?Continuar de todos modos? (s/N): ").strip().lower()
        if r != "s":
            print("  Cancelado.")
            pausa()
            return

    dry_run = _preguntar_sn("Solo previsualizar (dry-run)")

    from scripts import relocate
    args = ["--db", db_path, "--new-root", new_root]
    if old_root:
        args += ["--old-root", old_root]
    if dry_run:
        args += ["--dry-run"]
    relocate.main(args)

    pausa()


def opcion_check_db(db_path: str | None = None):
    """Inspección rápida (DRY) + salud básica."""
    limpiar_pantalla()
    print("=== INSPECCION DE BASE DE DATOS ===\n")
    db_path = leer_db(db_path)
    if not os.path.isfile(db_path):
        print("  No se encuentra la base de datos.")
        pausa()
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Resumen por tipo
        print(resumen_db(conn))
        # Totales por tabla (unificado con check_db.py)
        try:
            from db.util import obtener_resumen
            resumen = obtener_resumen(conn)
            print("\n  Por tabla:")
            for t in ["media", "media_metadata", "media_keypoints", "media_embeddings", "tracks", "waypoints", "telegram_messages", "telegram_media"]:
                cnt = resumen.get(t, -1)
                if cnt >= 0 and cnt > 0:
                    print(f"    {t:<22s} {cnt:>6d}")
        except Exception:
            pass
        # Salud: batches, GPS, hash colisiones
        try:
            batch_cnt = conn.execute("SELECT COUNT(DISTINCT ingest_batch_id) FROM media WHERE ingest_batch_id IS NOT NULL").fetchone()[0]
            null_batch = conn.execute("SELECT COUNT(*) FROM media WHERE ingest_batch_id IS NULL").fetchone()[0]
            print(f"\n  Batches: {batch_cnt} (legacy sin batch: {null_batch})")
        except Exception:
            pass
        try:
            gps_con = conn.execute("SELECT COUNT(*) FROM media WHERE latitude IS NOT NULL").fetchone()[0]
            gps_sin = conn.execute("SELECT COUNT(*) FROM media WHERE latitude IS NULL").fetchone()[0]
            print(f"  GPS: con {gps_con} / sin {gps_sin}")
            # Signo inválido (Argentina debe ser negativo)
            bad_lat = conn.execute("SELECT COUNT(*) FROM media WHERE latitude > 0").fetchone()[0]
            bad_lon = conn.execute("SELECT COUNT(*) FROM media WHERE longitude > 0").fetchone()[0]
            if bad_lat or bad_lon:
                print(f"  ⚠ Coordenadas con signo positivo (revisar fix_gps_sign): lat>0={bad_lat} lon>0={bad_lon}")
        except Exception:
            pass
        try:
            # Hash duplicado (debería ser único)
            dup_hash = conn.execute(
                "SELECT file_hash, COUNT(*) c FROM media GROUP BY file_hash HAVING c>1 LIMIT 3"
            ).fetchall()
            if dup_hash:
                print(f"  ⚠ Hash duplicado: {len(dup_hash)} grupos (ej: {dup_hash[0][0][:12]}...)")
        except Exception:
            pass
        print("\n  Ultimos registros:")
        cursor = conn.execute(
            "SELECT id, filename_original, type, author, timestamp_utc FROM media ORDER BY id DESC LIMIT 5"
        )
        for row in cursor:
            print(f"  #{row[0]:>6d} [{row[2]:6s}] {row[1]} - {row[3] or '?'}")
        print("\n  Tip: para detalle por tabla usá 2) Listar → 8) Columnas, o `python scripts/check_db.py --verbose --limit 20`")
    except sqlite3.OperationalError as e:
        print(f"  Error: {e}")
    finally:
        conn.close()

    pausa()


def opcion_exportar(db_path: str | None = None):
    """Menu: Exportar DB a medios (relocalizar)."""
    _menu("EXPORTAR DB A MEDIOS", {
        "1": ("Relocalizar medios (cambiar raiz)", opcion_relocalizar),
    }, db_path, intro="  Actualiza las rutas cuando los archivos se mudan de ubicacion.")


def opcion_gradient():
    """Menu para calcular gradientes de ruta entre puntos GPS consecutivos."""
    limpiar_pantalla()
    print("=== CALCULAR GRADIENTES DE RUTA ===\n")

    print("  1) Calcular gradientes")
    print("  2) Previsualizar (dry-run)")
    print("  3) Previsualizar con detalle (dry-run + verbose)")
    print("  0) Volver\n")

    opc = input("  Opcion: ").strip()

    db_path = leer_db()
    from scripts import gradiente

    if opc == "1":
        modo = _preguntar_modo(db_path)
        if modo is None:
            print("  Cancelado.")
            pausa()
            return
        args = ["--db", db_path]
        if modo != "skip":
            args += ["--mode", modo]
        gradiente.main(args)
    elif opc == "2":
        gradiente.main(["--db", db_path, "--dry-run"])
    elif opc == "3":
        gradiente.main(["--db", db_path, "--dry-run", "--verbose"])
    elif opc == "0":
        return

    pausa()


def opcion_astronomia(db_path: str | None = None):
    """Menu para calcular posición del sol y clasificar twilight."""
    limpiar_pantalla()
    print("=== CALCULAR POSICIÓN DEL SOL (ASTRONOMÍA) ===\n")

    print("  1) Calcular astronomía")
    print("  2) Previsualizar (dry-run)")
    print("  3) Previsualizar con detalle (dry-run + verbose)")
    print("  0) Volver\n")

    opc = input("  Opcion: ").strip()

    db_path = leer_db()
    from scripts import astronomia

    if opc == "1":
        modo = _preguntar_modo(db_path)
        if modo is None:
            print("  Cancelado.")
            pausa()
            return
        args = ["--db", db_path]
        if modo != "skip":
            args += ["--mode", modo]
        astronomia.main(args)
    elif opc == "2":
        astronomia.main(["--db", db_path, "--dry-run"])
    elif opc == "3":
        astronomia.main(["--db", db_path, "--dry-run", "--verbose"])
    elif opc == "0":
        return

    pausa()


def opcion_check_gps(db_path: str | None = None):
    """Submenú GPS: muestras DB, verificación con ExifTool, carpeta directa y conteo total."""
    from scripts import check_gps
    db_path = leer_db(db_path)

    def _muestras_db(db):
        if not os.path.isfile(db):
            print("  No se encuentra la base de datos.")
            pausa()
            return
        conn = sqlite3.connect(db)
        try:
            # Totales
            tot_img = conn.execute("SELECT COUNT(*) FROM media WHERE type='image'").fetchone()[0]
            sin = conn.execute("SELECT COUNT(*) FROM media WHERE type='image' AND latitude IS NULL").fetchone()[0]
            con = tot_img - sin
            print(f"  Imagenes: {tot_img} (con GPS: {con}, sin GPS: {sin})")
            tot_vid = conn.execute("SELECT COUNT(*) FROM media WHERE type='video'").fetchone()[0]
            vid_sin = conn.execute("SELECT COUNT(*) FROM media WHERE type='video' AND latitude IS NULL").fetchone()[0]
            print(f"  Videos: {tot_vid} (con GPS: {tot_vid-vid_sin}, sin GPS: {vid_sin})")
            print()
            cur = conn.execute(
                "SELECT filepath_absoluto FROM media WHERE type='image' AND latitude IS NULL ORDER BY RANDOM() LIMIT 5"
            )
            sin_gps = cur.fetchall()
            if sin_gps:
                print("  Muestras de imágenes sin GPS en DB (5 al azar):")
                for (fp,) in sin_gps:
                    print(f"    {fp}")
            else:
                print("  No hay imágenes sin GPS en la DB.")
        except sqlite3.OperationalError as e:
            print(f"  Error: {e}")
        conn.close()
        pausa()

    def _verificar_exiftool(db):
        # Delega en check_gps.py que sí usa ExifTool y distingue archivo no existe / GPS real
        try:
            n = input("  Cantidad de muestras [5]: ").strip() or "5"
            check_gps.main(["--db", db, "--samples", n])
        except Exception as e:
            print(f"  Error: {e}")
        pausa()

    def _carpeta(db):
        carpeta = input("  Carpeta a inspeccionar (ruta): ").strip()
        if not carpeta:
            print("  Cancelado.")
            pausa()
            return
        try:
            n = input("  Muestras [5]: ").strip() or "5"
            check_gps.main(["--folder", carpeta, "--samples", n])
        except Exception as e:
            print(f"  Error: {e}")
        pausa()

    def _conteo_total(db):
        if not os.path.isfile(db):
            print("  No se encuentra la base de datos.")
            pausa()
            return
        conn = sqlite3.connect(db)
        try:
            for typ in ("image", "video", "audio", "text", "other"):
                tot = conn.execute("SELECT COUNT(*) FROM media WHERE type=?", (typ,)).fetchone()[0]
                if tot:
                    con = conn.execute("SELECT COUNT(*) FROM media WHERE type=? AND latitude IS NOT NULL", (typ,)).fetchone()[0]
                    print(f"  {typ:<8s} {tot:>5d} total — con GPS: {con:>5d} — sin GPS: {tot-con:>5d}")
            # GPS por fuente
            print()
            cur = conn.execute("SELECT geolocation_source, COUNT(*) FROM media WHERE latitude IS NOT NULL GROUP BY geolocation_source")
            for src, cnt in cur.fetchall():
                print(f"    fuente={src or '(NULL)':<20s} {cnt:>5d}")
        except Exception as e:
            print(f"  Error: {e}")
        conn.close()
        pausa()

    _menu("REVISAR GPS", {
        "1": ("Muestras DB (rápido, solo DB)", _muestras_db),
        "2": ("Verificar con ExifTool (archivo real)", _verificar_exiftool),
        "3": ("Inspeccionar carpeta (--folder)", _carpeta),
        "4": ("Conteo total por tipo y fuente", _conteo_total),
    }, db_path, intro="  Revisa GPS en DB y en archivos (ExifTool).")


def opcion_detalle_db(db_path: str | None = None):
    """Muestra todas las columnas de los ultimos registros (con clamp y metadata opcional)."""
    limpiar_pantalla()
    print("=== DETALLE COMPLETO DE REGISTROS ===\n")

    db_path = leer_db(db_path)
    if not os.path.isfile(db_path):
        print("  No se encuentra la base de datos.")
        pausa()
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        # Obtener nombres de columnas
        cols = [row[1] for row in conn.execute("PRAGMA table_info(media)")]
        print(f"  {len(cols)} columnas en media\n")

        # Pedir cantidad con clamp 1..200 y mensaje claro
        n_raw = input("  Cantidad de registros a mostrar (default 10, máx 200): ").strip() or "10"
        try:
            n_req = int(n_raw)
        except ValueError:
            print(f"  '{n_raw}' no es número, usando 10.")
            n_req = 10
        n = max(1, min(n_req, 200))
        if n_req > 200:
            print("  (limitado a 200)")
        elif n_req != n:
            print(f"  (ajustado a {n})")

        cursor = conn.execute(
            "SELECT * FROM media ORDER BY id DESC LIMIT ?",
            (n,),
        )
        rows = cursor.fetchall()

        if not rows:
            print("  No hay registros.")
            conn.close()
            pausa()
            return

        ver_meta = _preguntar_sn("Mostrar también media_metadata y keypoints por cada medio", default=False)

        for row in rows:
            print(f"  ── #{row['id']} ──")
            for col in cols:
                val = row[col]
                if val is not None:
                    val_str = str(val)
                    # Truncar 100, pero preservar rutas completas con indicación
                    if col in ("filepath_absoluto", "filepath_relativo", "sidecar_xml") and len(val_str) > 120:
                        # Mostrar completo para depurar rutas
                        print(f"    {col:<25s} {val_str}")
                    else:
                        if len(val_str) > 100:
                            val_str = val_str[:97] + "..."
                        print(f"    {col:<25s} {val_str}")
            if ver_meta:
                # metadata — mostrar 10 primeras, trunc 100
                metas = conn.execute(
                    "SELECT key, substr(value,1,100) FROM media_metadata WHERE media_id=? ORDER BY key LIMIT 10",
                    (row["id"],),
                ).fetchall()
                total_metas = conn.execute("SELECT COUNT(*) FROM media_metadata WHERE media_id=?", (row["id"],)).fetchone()[0]
                if metas:
                    print(f"    -- metadata ({total_metas} claves, mostrando {len(metas)}) --")
                    for k, v in metas:
                        print(f"      {k:<25s} {v}")
                    if total_metas > len(metas):
                        print(f"      ... ({total_metas - len(metas)} más)")
                kps = conn.execute(
                    "SELECT key, timestamp_offset_secs, substr(value,1,80) FROM media_keypoints WHERE media_id=? ORDER BY timestamp_offset_secs LIMIT 10",
                    (row["id"],),
                ).fetchall()
                total_kp = conn.execute("SELECT COUNT(*) FROM media_keypoints WHERE media_id=?", (row["id"],)).fetchone()[0]
                if kps:
                    print(f"    -- keypoints ({total_kp} total, mostrando {len(kps)}) --")
                    for k, off, v in kps:
                        print(f"      [{k}] +{off:.1f}s: {v}")
            print()

        print(f"  {len(rows)} registros mostrados.")

    except sqlite3.OperationalError as e:
        print(f"  Error: {e}")
    finally:
        conn.close()

    pausa()


def opcion_detalle_por_id(db_path: str | None = None):
    """Detalle de un medio por ID: media + metadata + keypoints + embeddings info."""
    limpiar_pantalla()
    print("=== DETALLE POR ID ===\n")
    db_path = leer_db(db_path)
    if not os.path.isfile(db_path):
        print("  No se encuentra la base de datos.")
        pausa()
        return
    try:
        mid_str = input("  ID del medio: ").strip()
        mid = int(mid_str)
    except ValueError:
        print("  ID inválido.")
        pausa()
        return
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM media WHERE id=?", (mid,)).fetchone()
        if not row:
            print(f"  No existe medio #{mid}")
            conn.close()
            pausa()
            return
        cols = [r[1] for r in conn.execute("PRAGMA table_info(media)")]
        print(f"\n  ── #{row['id']} {row['filename_original']} [{row['type']}] ──")
        for col in cols:
            val = row[col]
            if val is not None:
                sval = str(val)
                # Mostrar completo para campos clave, truncar resto
                if col in ("filepath_absoluto", "sidecar_xml") and len(sval) > 80:
                    print(f"    {col:<25s} {sval}")
                else:
                    if len(sval) > 80:
                        sval = sval[:77] + "..."
                    print(f"    {col:<25s} {sval}")
        # metadata completa
        metas = conn.execute("SELECT key, value FROM media_metadata WHERE media_id=? ORDER BY key", (mid,)).fetchall()
        print(f"\n  metadata: {len(metas)} claves")
        for r in metas:
            v = r["value"] or ""
            if len(v) > 120:
                v = v[:117] + "..."
            print(f"    {r['key']:<30s} = {v}")
        # keypoints
        kps = conn.execute(
            "SELECT id, timestamp_offset_secs, timestamp_absolute, key, substr(value,1,100) FROM media_keypoints WHERE media_id=? ORDER BY timestamp_offset_secs",
            (mid,),
        ).fetchall()
        print(f"\n  keypoints: {len(kps)}")
        for r in kps[:20]:
            print(f"    #{r[0]} [{r[3]}] +{r[1]:.1f}s {r[2]}: {r[4]}")
        if len(kps) > 20:
            print(f"    ... ({len(kps)-20} más)")
        # embeddings
        embs = conn.execute("SELECT modelo, fecha FROM media_embeddings WHERE media_id=?", (mid,)).fetchall()
        if embs:
            print(f"\n  embeddings: {len(embs)}")
            for e in embs:
                print(f"    {e[0]} @ {e[1]}")
        # telegram link
        if row["telegram_message_id"]:
            tg = conn.execute("SELECT chat_id, message_id, text FROM telegram_messages WHERE id=?", (row["telegram_message_id"],)).fetchone()
            if tg:
                print(f"\n  telegram: chat={tg[0]} msg={tg[1]} text={str(tg[2])[:80] if tg[2] else ''}")
    except Exception as e:
        print(f"  Error: {e}")
    finally:
        conn.close()
    pausa()


def _tabla_existe(conn: sqlite3.Connection, tabla: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {tabla} LIMIT 1")
        return True
    except sqlite3.OperationalError:
        return False


def opcion_undo_ingest(db_path: str | None = None):
    """Menu para deshacer una ingesta (medios o GPX)."""
    limpiar_pantalla()
    print("=== DESHACER INGESTA ===\n")

    db_path = leer_db(db_path)
    if not os.path.isfile(db_path):
        print("  No se encuentra la base de datos.")
        pausa()
        return

    conn = sqlite3.connect(db_path)
    try:
        # --- Listar batches de medios ---
        cursor = conn.execute(
            "SELECT ingest_batch_id, MIN(ingested_at), COUNT(*) FROM media "
            "WHERE ingest_batch_id IS NOT NULL "
            "GROUP BY ingest_batch_id ORDER BY MIN(ingested_at) DESC"
        )
        batches = cursor.fetchall()

        # --- Listar tracks GPX ---
        tracks_cursor = conn.execute(
            "SELECT id, name, ingested_at, total_points FROM tracks ORDER BY ingested_at DESC"
        )
        tracks = tracks_cursor.fetchall()

        if not batches and not tracks:
            print("  No hay ingestas (medios ni GPX) para deshacer.")
            conn.close()
            pausa()
            return

        # --- Obtener batch actual ---
        current_batch = conn.execute(
            "SELECT value FROM config WHERE key = 'current_ingest_batch'"
        ).fetchone()
        current_batch_str = current_batch[0] if current_batch else ""

        # --- Mostrar opciones ---
        if batches:
            print("  Medios (por batch):\n")
            for bid, ts, cnt in batches:
                current = "  (actual)" if str(bid) == current_batch_str else ""
                print(f"    b{bid}  -  {ts}  -  {cnt} medios{current}")
            print()

        if tracks:
            print("  Tracks GPX:\n")
            for tid, name, ts, pts in tracks:
                print(f"    t{tid}  -  {ts}  -  \"{name}\"  ({pts} puntos)")
            print()

        print("  Ingrese codigo a deshacer (ej: b5  o  t2) o 0 para cancelar:")
        codigo = input("  > ").strip().lower()

        if codigo == "0" or not codigo:
            print("  Cancelado.")
            conn.close()
            pausa()
            return

        # --- Parsear codigo ---
        if codigo[0] == "b":
            # Deshacer batch de medios
            try:
                bid = int(codigo[1:])
            except ValueError:
                print("  Codigo invalido.")
                conn.close()
                pausa()
                return
            # Validar que existe
            if not any(str(b[0]) == str(bid) for b in batches):
                print(f"  Batch #{bid} no encontrado.")
                conn.close()
                pausa()
                return
            # Preview de cascada
            try:
                b_count = conn.execute("SELECT COUNT(*) FROM media WHERE ingest_batch_id=?", (bid,)).fetchone()[0]
                meta_cnt = conn.execute(
                    "SELECT COUNT(*) FROM media_metadata WHERE media_id IN (SELECT id FROM media WHERE ingest_batch_id=?)",
                    (bid,),
                ).fetchone()[0]
                kp_cnt = conn.execute(
                    "SELECT COUNT(*) FROM media_keypoints WHERE media_id IN (SELECT id FROM media WHERE ingest_batch_id=?)",
                    (bid,),
                ).fetchone()[0]
                emb_cnt = conn.execute(
                    "SELECT COUNT(*) FROM media_embeddings WHERE media_id IN (SELECT id FROM media WHERE ingest_batch_id=?)",
                    (bid,),
                ).fetchone()[0]
                tg_cnt = conn.execute(
                    "SELECT COUNT(*) FROM telegram_media WHERE media_id IN (SELECT id FROM media WHERE ingest_batch_id=?)",
                    (bid,),
                ).fetchone()[0] if _tabla_existe(conn, "telegram_media") else 0
                # Desglose por tipo
                tipos = conn.execute(
                    "SELECT type, COUNT(*) FROM media WHERE ingest_batch_id=? GROUP BY type", (bid,)
                ).fetchall()
                print(f"\n  Batch #{bid}: {b_count} medios")
                for t, c in tipos:
                    print(f"    - {t}: {c}")
                print(f"  En cascada se borrarán: {meta_cnt} metadata, {kp_cnt} keypoints, {emb_cnt} embeddings, {tg_cnt} links telegram")
                print("  (ON DELETE CASCADE / SET NULL)\n")
            except Exception:
                pass
            confirm = input(f"  Esto BORRARÁ los {b_count} medios del batch #{bid} + datos derivados. Confirmar? (s/N): ").strip().lower()
            if confirm != "s":
                print("  Cancelado.")
                conn.close()
                pausa()
                return

            # Backup automático antes de operación destructiva
            conn.commit()
            conn.close()
            bak = _auto_backup(db_path)
            if bak:
                print(f"  ✓ Backup automático: {os.path.basename(bak)}")
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("BEGIN")
                deleted = conn.execute("DELETE FROM media WHERE ingest_batch_id = ?", (bid,)).rowcount
                # Actualizar current_ingest_batch si apuntaba al borrado
                try:
                    cur_val = conn.execute("SELECT value FROM config WHERE key='current_ingest_batch'").fetchone()
                    if cur_val and str(cur_val[0]) == str(bid):
                        # Setear al batch más reciente restante, o borrar si no quedan
                        nxt = conn.execute(
                            "SELECT ingest_batch_id FROM media WHERE ingest_batch_id IS NOT NULL ORDER BY ingested_at DESC LIMIT 1"
                        ).fetchone()
                        if nxt:
                            conn.execute(
                                "INSERT OR REPLACE INTO config (key, value) VALUES ('current_ingest_batch', ?)",
                                (str(nxt[0]),),
                            )
                            print(f"  current_ingest_batch actualizado a {nxt[0]}")
                        else:
                            conn.execute("DELETE FROM config WHERE key='current_ingest_batch'")
                            print("  current_ingest_batch eliminado (no quedan batches)")
                except Exception:
                    pass
                conn.commit()
                if deleted == 0:
                    print(f"  Batch #{bid} ya estaba vacío (0 eliminados).")
                else:
                    print(f"  Eliminados {deleted} medios del batch #{bid} + cascada.")
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    pass
                print(f"  Error al borrar batch: {e}")

        elif codigo[0] == "t":
            # Deshacer track GPX
            try:
                tid = int(codigo[1:])
            except ValueError:
                print("  Codigo invalido.")
                conn.close()
                pausa()
                return

            # Verificar que existe y leer rango temporal
            track = conn.execute(
                "SELECT id, name, start_time, end_time FROM tracks WHERE id = ?", (tid,)
            ).fetchone()
            if not track:
                print(f"  Track #{tid} no encontrado.")
                conn.close()
                pausa()
                return
            track_nombre, track_start, track_end = track[1], track[2], track[3]
            # Contar waypoints
            try:
                wp_cnt = conn.execute("SELECT COUNT(*) FROM waypoints WHERE track_id=?", (tid,)).fetchone()[0]
            except sqlite3.OperationalError:
                wp_cnt = conn.execute("SELECT COUNT(*) FROM waypoints").fetchone()[0] if _tabla_existe(conn, "waypoints") else 0
            # Estimar medios afectados (track_gps dentro del rango)
            try:
                if track_start and track_end:
                    cnt_gps_rango = conn.execute(
                        "SELECT COUNT(*) FROM media WHERE geolocation_source='track_gps' AND timestamp_utc BETWEEN ? AND ?",
                        (track_start, track_end),
                    ).fetchone()[0]
                    cnt_interp_rango = conn.execute(
                        "SELECT COUNT(*) FROM media WHERE geolocation_source='track_interpolado' AND timestamp_utc BETWEEN ? AND ?",
                        (track_start, track_end),
                    ).fetchone()[0]
                else:
                    cnt_gps_rango = conn.execute("SELECT COUNT(*) FROM media WHERE geolocation_source='track_gps'").fetchone()[0]
                    cnt_interp_rango = conn.execute("SELECT COUNT(*) FROM media WHERE geolocation_source='track_interpolado'").fetchone()[0]
                cnt_kp_interp = conn.execute("SELECT COUNT(*) FROM media_keypoints WHERE key='ubicacion_video'").fetchone()[0]
                print(f"\n  Track #{tid} \"{track_nombre}\" — {wp_cnt} waypoints — rango {track_start} → {track_end}")
                print(f"  Afectados estimados: {cnt_gps_rango} medios track_gps en rango, {cnt_interp_rango} track_interpolado, {cnt_kp_interp} keypoints ubicacion_video totales")
                if track_start and track_end:
                    print("  (solo se revertirán los que caen dentro del rango temporal del track)")
                else:
                    print("  (track sin rango, se usaría conteo total — se pedirá confirmación extra)")
            except Exception:
                pass
            print()
            confirm = input(f"  Esto borrará el track \"{track_nombre}\" y sus {wp_cnt} waypoints. Confirmar? (s/N): ").strip().lower()
            if confirm != "s":
                print("  Cancelado.")
                conn.close()
                pausa()
                return

            # Backup automático antes de borrar track
            conn.commit()
            conn.close()
            bak = _auto_backup(db_path)
            if bak:
                print(f"  ✓ Backup automático: {os.path.basename(bak)}")
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("BEGIN")
                # Revertir altitud solo dentro del rango del track (evita wipe global multi-track)
                if track_start and track_end:
                    revertidos = conn.execute(
                        "UPDATE media SET altitude = NULL, geolocation_source = NULL "
                        "WHERE geolocation_source = 'track_gps' AND timestamp_utc BETWEEN ? AND ?",
                        (track_start, track_end),
                    ).rowcount
                else:
                    # Sin rango, no revertir automáticamente; preguntar
                    if _preguntar_sn(f"Revertir altitud de TODOS los {cnt_gps_rango} medios con track_gps (sin rango)"):
                        revertidos = conn.execute(
                            "UPDATE media SET altitude = NULL, geolocation_source = NULL WHERE geolocation_source='track_gps'"
                        ).rowcount
                    else:
                        revertidos = 0
                        print("  Altitud preservada (sin rango).")
                # Limpiar track_interpolado dentro del rango + keypoints/metadata asociados
                interp_limpiados = 0
                kp_borrados = 0
                meta_limpiados = 0
                if track_start and track_end:
                    interp_limpiados = conn.execute(
                        "UPDATE media SET latitude=NULL, longitude=NULL, altitude=NULL, geolocation_source=NULL "
                        "WHERE geolocation_source='track_interpolado' AND timestamp_utc BETWEEN ? AND ?",
                        (track_start, track_end),
                    ).rowcount
                    # keypoints ubicacion_video de medios en rango
                    kp_borrados = conn.execute(
                        "DELETE FROM media_keypoints WHERE key='ubicacion_video' AND media_id IN "
                        "(SELECT id FROM media WHERE timestamp_utc BETWEEN ? AND ?)",
                        (track_start, track_end),
                    ).rowcount
                    meta_limpiados = conn.execute(
                        "DELETE FROM media_metadata WHERE key IN ('ubicacion_video_estado','ubicacion_video_gaps') AND media_id IN "
                        "(SELECT id FROM media WHERE timestamp_utc BETWEEN ? AND ?)",
                        (track_start, track_end),
                    ).rowcount
                    # También limpiar keypoints contexto track_interpolado si existen
                    conn.execute(
                        "DELETE FROM media_keypoints WHERE key LIKE 'contexto_%' AND source='track_interpolado' AND media_id IN "
                        "(SELECT id FROM media WHERE timestamp_utc BETWEEN ? AND ?)",
                        (track_start, track_end),
                    )
                # Borrar track (CASCADE borra waypoints automaticamente)
                conn.execute("DELETE FROM tracks WHERE id = ?", (tid,))
                conn.commit()
                print(f"  Track \"{track_nombre}\" eliminado ({wp_cnt} waypoints).")
                if revertidos:
                    print(f"  Altitud revertida para {revertidos} medios (track_gps en rango).")
                if interp_limpiados:
                    print(f"  Limpiados {interp_limpiados} medios track_interpolado + {kp_borrados} keypoints + {meta_limpiados} metadata en rango.")
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    pass
                print(f"  Error al borrar track: {e}")

        else:
            print("  Codigo invalido. Use b<num> para medios o t<num> para tracks.")
            conn.close()
            pausa()
            return

    except (sqlite3.OperationalError, ValueError) as e:
        print(f"  Error: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass
    pausa()


def _verificar_ollama(modelos: list[str] | None = None) -> bool:
    """Verifica que Ollama esté corriendo, iniciándolo si hace falta.

    Si Ollama no responde, intenta arrancarlo automáticamente con
    `ollama serve` en segundo plano (vía `scripts.ai_media.ollama_client`).

    Args:
        modelos: lista de nombres de modelo a verificar (ej: ["qwen2.5vl:7b"])

    Returns:
        True si Ollama está disponible (y los modelos si se pidieron), False si no.
    """
    import json
    import urllib.request

    from scripts.ai_media.ollama_client import asegurar_ollama, ollama_responde

    estaba_corriendo = ollama_responde()
    if not asegurar_ollama():
        print("  ⚠️  Ollama NO está corriendo y no se pudo iniciarlo.")
        print("     Los pasos de IA (keywords, descripciones, transcripcion)")
        print("     requieren Ollama con los modelos necesarios.")
        print("     Verificá que el binario esté en PATH o ejecutá: ollama serve\n")
        return False
    if not estaba_corriendo:
        print("  ✅ Ollama iniciado automáticamente (ollama serve).\n")

    try:
        req = urllib.request.Request("http://localhost:11434/api/tags",
                                     method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        print("  ⚠️  Ollama no respondió al listar modelos.")
        return False

    if modelos:
        disponibles = [m["name"] for m in data.get("models", [])]
        faltan = [m for m in modelos if m not in disponibles]
        if faltan:
            print(f"  ⚠️  Modelos faltantes en Ollama: {', '.join(faltan)}")
            print("     Ejecutá: ollama pull " + " ".join(faltan))
            print()
            return False

    return True


_PASOS_IA = {"keywords", "descriptions", "combinado", "kw_transcripcion", "kw_texto", "analizar_video"}


def _ejecutar_improve_db(pasos: str | None = None, modo: str = "skip"):
    """Ejecuta improve_db, verificando Ollama si el/los paso(s) lo requieren."""
    from scripts import improve_db

    # Determinar si los pasos requieren IA
    pasos_set: set[str] = set()
    if pasos:
        pasos_set = set(p.strip() for p in pasos.split(","))
    else:
        # Sin --steps, improve_db corre todos los pasos (DEP_ORDER)
        pasos_set = set(improve_db.DEP_ORDER)

    if pasos_set & _PASOS_IA:
        if not _verificar_ollama():
            r = input("  ?Continuar de todos modos? (s/N): ").strip().lower()
            if r != "s":
                print("  Cancelado.")
                return

    args = []
    if pasos:
        args += ["--steps", pasos]
    if modo != "skip":
        args += ["--mode", modo]
    improve_db.main(args)


def _ejecutar_paso_mejora(pasos: str | None, db_path: str | None = None):
    """Pide modo y ejecuta improve_db con los pasos dados."""
    modo = _preguntar_modo(db_path)
    if modo is None:
        print("  Cancelado.")
        pausa()
        return
    _ejecutar_improve_db(pasos=pasos, modo=modo)
    pausa()


def _directorio_backups(db_path: str) -> str:
    """Directorio unificado de backups: siempre db/backups/ junto a la DB."""
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "backups")


def _backup_wal_safe(origen: str, destino: str) -> bool:
    """Copia DB de forma segura con WAL: intenta sqlite backup API, fallback a copy2.
    Hace wal_checkpoint antes de copiar para consistencia."""
    import shutil
    # Intentar backup API (WAL-safe)
    try:
        src = sqlite3.connect(origen)
        try:
            src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
        dst = sqlite3.connect(destino)
        try:
            src.backup(dst)
        finally:
            dst.close()
        src.close()
        # Verificar tamaño
        if os.path.isfile(destino) and os.path.getsize(destino) > 0:
            return True
    except Exception:
        pass
    # Fallback copy2
    try:
        shutil.copy2(origen, destino)
        # Copiar también -wal/-shm si existen (evita inconsistencia si fallback)
        for suf in ("-wal", "-shm"):
            extra = origen + suf
            if os.path.isfile(extra):
                try:
                    shutil.copy2(extra, destino + suf)
                except Exception:
                    pass
        return os.path.isfile(destino)
    except Exception:
        return False


def _auto_backup(db_path: str) -> str | None:
    """Crea un backup automático con timestamp en db/backups/. Retorna la ruta o None."""
    from datetime import datetime
    backup_dir = _directorio_backups(db_path)
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir, f"flujos_{ts}__autobackup.db")
    try:
        ok = _backup_wal_safe(db_path, backup_path)
        if ok:
            _podar_backups(backup_dir)
            return backup_path
        print(f"  ⚠ Error al crear backup automático: fallo copia")
        return None
    except Exception as e:
        print(f"  ⚠ Error al crear backup automático: {e}")
        return None


def _crear_backup_manual(db_path: str) -> str | None:
    """Crea backup con timestamp en db/backups/. Retorna la ruta o None."""
    from datetime import datetime
    backup_dir = _directorio_backups(db_path)
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir, f"flujos_{ts}__manual.db")
    try:
        ok = _backup_wal_safe(db_path, backup_path)
        if ok:
            size_mb = os.path.getsize(backup_path) / (1024 * 1024)
            print(f"  ✓ Backup: {os.path.basename(backup_path)} ({size_mb:.1f} MB) → {backup_dir}")
            _podar_backups(backup_dir)
            return backup_path
        print(f"  Error creando backup: fallo copia")
        return None
    except Exception as e:
        print(f"  Error creando backup: {e}")
        return None


def _podar_backups(backup_dir: str, max_backups: int = 25, max_total_mb: int = 800) -> None:
    """Retención simple: mantiene como máximo max_backups archivos .db más recientes
    y poda si el total supera max_total_mb. No borra backups con nombres legacy fuera de patron."""
    import re
    try:
        patron = re.compile(r"^flujos_.*\.db$")
        archivos = []
        for f in os.listdir(backup_dir):
            if patron.match(f):
                ruta = os.path.join(backup_dir, f)
                if os.path.isfile(ruta):
                    archivos.append((ruta, os.path.getmtime(ruta), os.path.getsize(ruta)))
        if not archivos:
            return
        archivos.sort(key=lambda x: x[1], reverse=True)  # más reciente primero
        # Podar por cantidad
        if len(archivos) > max_backups:
            for ruta, _, _ in archivos[max_backups:]:
                try:
                    os.remove(ruta)
                    print(f"  (retención: eliminado backup antiguo {os.path.basename(ruta)})")
                except Exception:
                    pass
            archivos = archivos[:max_backups]
        # Podar por tamaño total
        total = sum(s for _, _, s in archivos)
        if total > max_total_mb * 1024 * 1024:
            # Eliminar los más viejos hasta bajar del límite
            archivos_asc = sorted(archivos, key=lambda x: x[1])
            for ruta, _, sz in archivos_asc:
                if total <= max_total_mb * 1024 * 1024:
                    break
                try:
                    os.remove(ruta)
                    total -= sz
                    print(f"  (retención tamaño: eliminado {os.path.basename(ruta)})")
                except Exception:
                    pass
    except Exception:
        pass


def _preguntar_modo(db_path: str | None = None):
    """Pregunta modo de ejecución y lo devuelve como string.
    Retorna None si el usuario cancela la operación.
    Si db_path se provee y el modo es 'replace', crea backup automático."""
    print("  Modo:")
    print("    s) Skip — solo pendientes (default)")
    print("    u) Update — actualizar existentes")
    print("    r) Replace — borrar y regenerar")
    print("    c) Cancelar")
    m = input("  Modo (s/u/r/c) [s]: ").strip().lower()
    if m == "u":
        return "update"
    elif m == "r":
        if db_path and os.path.isfile(db_path):
            backup = _auto_backup(db_path)
            if backup:
                print(f"  ✓ Backup automático: {os.path.basename(backup)}")
        return "replace"
    elif m == "c":
        return None
    return "skip"


def _elegir_modo_pregunta(db_path: str | None, descripcion: str,
                          descripciones: dict[str, str] | None = None) -> str | None:
    """Pregunta modo de ejecución (skip/update/replace) para menus que
    traen descripcion propia personalizada. Devuelve string o None si cancelar.
    descripciones: dict opcional maperando opcion->texto p.ej {"s": "skip — solo mensajes nuevos"}.
    Si no se pasa descripciones, imprime los textos genericos."""
    print(f"\n  Modo de {descripcion}:")
    if descripciones:
        for tecla, texto in (("s", "skip"), ("u", "update"), ("r", "replace")):
            print(f"    {tecla}) {texto} {descripciones.get(tecla, '')}".rstrip())
    else:
        print("    s) skip")
        print("    u) update")
        print("    r) replace / c) cancelar")
    m = input("  Modo [s]: ").strip().lower() or "s"
    if m == "c":
        return None
    return {"s": "skip", "u": "update", "r": "replace"}.get(m, "skip")


def opcion_improve_db(db_path: str | None = None):
    """Menu para ejecutar pasos de mejora sobre la DB — agrupado por tipo de medio.

    1) Todos y 2) Manual cubren la union completa de DEP_ORDER (~22 pasos) y sirven
    para dejar la maquina toda la noche en skip (saltea sin red/componente y avisa).
    3..7 agrupan por tipo de medio con repeticion permitida (ej transcribe en Audios y Videos).
    7) Enriquecimiento / Curaduria = refinar, limpiar, timestamps/gps, geocode, gradiente,
    clima, dia semana, astronomia."""
    db_path = leer_db(db_path)

    def _opcion_pasos_manuales(db):
        from scripts import improve_db
        print("\n  Pasos disponibles (improve_db --list):")
        for n in improve_db.DEP_ORDER:
            print(f"    {n}")
        print()
        pasos = input("  Pasos (separados por coma, ej: colors,keywords) o Enter=todos: ").strip()
        if not pasos:
            pasos = ",".join(improve_db.DEP_ORDER)
        modo = _preguntar_modo(db)
        if modo is None:
            print("  Cancelado.")
            pausa()
            return
        # Validar antes de ejecutar
        invalidos = [p.strip() for p in pasos.split(",") if p.strip() not in improve_db.REGISTRY]
        if invalidos:
            print(f"  Pasos invalidos: {', '.join(invalidos)}")
            print("  Usa los nombres de la lista anterior.")
            pausa()
            return
        _ejecutar_improve_db(pasos=pasos, modo=modo)
        pausa()

    def _todos(db):
        # Todos = todos los pasos en DEP_ORDER, con reporte de salteados
        _ejecutar_paso_mejora(None, db)

    # --- Submenús por tipo de medio ---
    def _menu_imagenes(db):
        _menu("IMAGENES", {
            "1": ("Colores dominantes", lambda d: _ejecutar_paso_mejora("colors", d)),
            "2": ("Keywords con IA", lambda d: _ejecutar_paso_mejora("keywords", d)),
            "3": ("Descripcion con IA", lambda d: _ejecutar_paso_mejora("descriptions", d)),
            "4": ("Keywords+Descripcion (1 vision+1 traduccion)", lambda d: _ejecutar_paso_mejora("combinado", d)),
            "5": ("Metadatos de video (ExifTool 360)", lambda d: _ejecutar_paso_mejora("video_metadata", d)),
        }, db_path, intro="  Pasos que operan sobre imagenes (vision minicpm + color):")

    def _menu_audios(db):
        _menu("AUDIOS", {
            "1": ("Transcripcion (faster-whisper)", lambda d: _ejecutar_paso_mejora("transcribe", d)),
            "2": ("Keypoints de transcripciones", lambda d: _ejecutar_paso_mejora("keypoints", d)),
            "3": ("Audio tagging (sonidos ambientales)", lambda d: _ejecutar_paso_mejora("audio_tagging", d)),
            "4": ("Keywords del sentido (transcripciones)", lambda d: _ejecutar_paso_mejora("kw_transcripcion", d)),
        }, db_path, intro="  Pasos sobre audios (tambien aplican a videos con audio):")

    def _menu_videos(db):
        _menu("VIDEOS", {
            "1": ("Transcripcion (faster-whisper)", lambda d: _ejecutar_paso_mejora("transcribe", d)),
            "2": ("Audio tagging (sonidos ambientales)", lambda d: _ejecutar_paso_mejora("audio_tagging", d)),
            "3": ("Analizar video por escenas (ffmpeg+minicpm)", lambda d: _ejecutar_paso_mejora("analizar_video", d)),
            "4": ("Keypoints por escena (desde video_analysis)", lambda d: _ejecutar_paso_mejora("keypoints_video", d)),
            "5": ("Keypoints de contexto (track GPX)", lambda d: _ejecutar_paso_mejora("keypoints_contexto", d)),
            "6": ("Metadatos de video (ExifTool 360)", lambda d: _ejecutar_paso_mejora("video_metadata", d)),
        }, db_path, intro="  Pasos sobre videos (duplicacion permitida con Audios):")

    def _menu_textos(db):
        _menu("TEXTOS", {
            "1": ("Keywords del sentido (textos .md)", lambda d: _ejecutar_paso_mejora("kw_texto", d)),
            "2": ("Refinar keywords (texto)", lambda d: _ejecutar_paso_mejora("refinar", d)),
        }, db_path, intro="  Pasos sobre textos ingresados (.md):")

    def _menu_enriquecimiento(db):
        _menu("ENRIQUECIMIENTO / CURADURIA", {
            "1": ("Refinar keywords (imagenes)", lambda d: _ejecutar_paso_mejora("refinar", d)),
            "2": ("Limpiar descripciones (eco del prompt)", lambda d: _ejecutar_paso_mejora("limpiar_descripciones", d)),
            "3": ("Inferir timestamps", lambda d: _ejecutar_paso_mejora("timestamps", d)),
            "4": ("Inferir GPS", lambda d: _ejecutar_paso_mejora("gps", d)),
            "5": ("Geocodificacion (Georef → prov/municipio)", lambda d: _ejecutar_paso_mejora("geocode", d)),
            "6": ("Gradientes de ruta", lambda d: _ejecutar_paso_mejora("gradiente", d)),
            "7": ("Clima historico (Open-Meteo)", lambda d: _ejecutar_paso_mejora("weather", d)),
            "8": ("Dia de la semana", lambda d: _ejecutar_paso_mejora("dia_semana", d)),
            "9": ("Posicion del sol / twilight (NOAA)", lambda d: _ejecutar_paso_mejora("astronomia", d)),
        }, db_path, intro="  Curaduria y enriquecimiento transversal (requiere GPS/tiempo; geocode/clima saltean sin red y avisan):")

    _menu("MEJORAR BASE DE DATOS", {
        "1": ("Todos los pasos (overnight, skip)", _todos),
        "2": ("Elegir pasos manualmente", _opcion_pasos_manuales),
        "3": ("Imagenes", _menu_imagenes),
        "4": ("Audios", _menu_audios),
        "5": ("Videos", _menu_videos),
        "6": ("Textos", _menu_textos),
        "7": ("Enriquecimiento / Curaduria", _menu_enriquecimiento),
    }, db_path, intro="  1 y 2 cubren TODOS los pasos (DEP_ORDER ampliado). 3..7 por tipo de medio\n  con repeticion permitida; 7 = Enriquecimiento / Curaduria.")


def opcion_weather():
    """Submenu: condiciones climaticas desde Open-Meteo."""
    limpiar_pantalla()
    print("=== CONDICIONES CLIMATICAS ===\n")

    print("  1) Obtener datos climaticos")
    print("  2) Previsualizar (dry-run)")
    print("  0) Volver\n")

    opc = input("  Opcion: ").strip()

    db_path = leer_db()
    import subprocess
    script = os.path.join(os.path.dirname(__file__), "scripts", "fetch_weather.py")
    db_flag = ["--db", db_path]

    if opc == "1":
        modo = _preguntar_modo(db_path)
        if modo is None:
            print("  Cancelado.")
            pausa()
            return
        if modo != "skip":
            subprocess.run([sys.executable, script] + db_flag + ["--mode", modo])
        else:
            subprocess.run([sys.executable, script] + db_flag)
    elif opc == "2":
        subprocess.run([sys.executable, script] + db_flag + ["--dry-run"])
    elif opc == "0":
        return

    pausa()


def opcion_dia_semana():
    """Submenu: calcular día de la semana de cada medio."""
    limpiar_pantalla()
    print("=== DIA DE LA SEMANA ===\n")

    print("  1) Calcular dia de la semana")
    print("  2) Previsualizar (dry-run)")
    print("  0) Volver\n")

    opc = input("  Opcion: ").strip()

    db_path = leer_db()
    import subprocess
    script = os.path.join(os.path.dirname(__file__), "scripts", "dia_semana.py")
    db_flag = ["--db", db_path]

    if opc == "1":
        modo = _preguntar_modo(db_path)
        if modo is None:
            print("  Cancelado.")
            pausa()
            return
        if modo != "skip":
            subprocess.run([sys.executable, script] + db_flag + ["--mode", modo])
        else:
            subprocess.run([sys.executable, script] + db_flag)
    elif opc == "2":
        subprocess.run([sys.executable, script] + db_flag + ["--dry-run"])
    elif opc == "0":
        return

    pausa()


def opcion_refinar_keywords(db_path: str | None = None):
    """
    Refina las keywords de IA: normaliza (léxico) y unifica sinónimos del
    dominio (diccionario). La capa semántica con embeddings fue eliminada
    (Ago 2026): introducía falsos sinónimos. Submenu de familia de keywords
    (imagenes, transcripciones, textos); por cada familia, elegir modo
    (update/dry-run) pasando --clave al script refinar_keywords.py.
    """
    import subprocess
    script = os.path.join(os.path.dirname(__file__), "scripts", "ai_media", "refinar_keywords.py")
    db_flag = ["--db", db_path or leer_db()]

    def _correr_refinar(db, clave, update):
        subprocess.run([sys.executable, script] + db_flag
                       + (["--clave", clave, "--mode", "update"] if update
                          else ["--clave", clave, "--dry-run"]))
        pausa()

    def _submenu_refinar(db, clave, titulo):
        _menu(f"REFINAR {titulo}", {
            "1": ("Refinar todos (update)", lambda d: _correr_refinar(d, clave, True)),
            "2": ("Previsualizar (dry-run)", lambda d: _correr_refinar(d, clave, False)),
        }, db_path, cerrar_al_ejecutar=True)

    _menu("REFINAR KEYWORDS", {
        "1": ("Imagenes (ia_keywords)", lambda db: _submenu_refinar(db, "ia_keywords", "IMAGENES (ia_keywords)")),
        "2": ("Transcripciones (ia_keywords_transcripcion)", lambda db: _submenu_refinar(db, "ia_keywords_transcripcion", "TRANSCRIPCIONES (ia_keywords_transcripcion)")),
        "3": ("Textos (ia_keywords_texto)", lambda db: _submenu_refinar(db, "ia_keywords_texto", "TEXTOS (ia_keywords_texto)")),
    }, db_path, intro="  Normaliza y unifica las keywords por familia (léxico + diccionario):")


def opcion_keywords_transcripciones(db_path: str | None = None):
    """
    Extrae keywords del SENTIDO desde transcripciones (audio/video) o desde
    textos ingresados (.md). Submenu de origen primero; por cada origen, el
    flujo de modos (skip/update/replace/dry-run) pasando --origen al script
    keywords_transcripciones.py.
    """
    import subprocess
    script = os.path.join(os.path.dirname(__file__), "scripts", "ai_media", "keywords_transcripciones.py")
    db_flag = ["--db", db_path or leer_db()]

    def _correr_origen(db, origen, modo):
        if modo is None:
            subprocess.run([sys.executable, script] + db_flag
                           + ["--origen", origen, "--dry-run"])
        else:
            subprocess.run([sys.executable, script] + db_flag
                           + ["--origen", origen, "--mode", modo])
        pausa()

    def _submenu_origen(db, origen, titulo):
        _menu(f"KEYWORDS DESDE {titulo}", {
            "1": ("Procesar (solo pendientes)", lambda d: _correr_origen(d, origen, "skip")),
            "2": ("Re-procesar todos (update)", lambda d: _correr_origen(d, origen, "update")),
            "3": ("Limpiar y regenerar (replace)", lambda d: _correr_origen(d, origen, "replace")),
            "4": ("Previsualizar (dry-run)", lambda d: _correr_origen(d, origen, None)),
        }, db_path, cerrar_al_ejecutar=True)

    _menu("KEYWORDS DESDE TEXTOS Y TRANSCRIPCIONES", {
        "1": ("Desde transcripciones (audio/video)", lambda db: _submenu_origen(db, "transcripcion", "TRANSCRIPCIONES (audio/video)")),
        "2": ("Desde textos (.md ingresados)", lambda db: _submenu_origen(db, "texto", "TEXTOS (.md)")),
    }, db_path, intro="  Extrae keywords del SENTIDO de las transcripciones de audio/video\n  o de los textos ingresados (.md). Usa Ollama (gemma3:latest).")


def opcion_audio_tagging(db_path: str | None = None):
    """
    Reconoce sonidos ambientales en audios/videos con el modelo CED-mini de
    sherpa-onnx (local, sin Ollama). Guarda en media_metadata claves
    'ia_keywords_sonido' (ES) e 'ia_sonido_raw' (JSON con probabilidades).
    """
    import subprocess
    script = os.path.join(os.path.dirname(__file__), "scripts", "ai_media", "audio_tagging.py")
    db_flag = ["--db", db_path or leer_db()]

    limpiar_pantalla()
    print("=== AUDIO TAGGING (sonidos ambientales) ===\n")
    print("  Detecta sonidos en audios/videos (tráfico, pájaros, viento, agua,\n"
          "  motores, voces...) con el modelo CED-mini de sherpa-onnx (100% local).\n")
    print("  1) Procesar (solo pendientes)")
    print("  2) Re-procesar todos (update)")
    print("  3) Limpiar y regenerar (replace)")
    print("  4) Previsualizar (dry-run)")
    print("  0) Volver\n")

    opc = input("  Opcion: ").strip()

    if opc in ("1", "2", "3"):
        modos = {"1": "skip", "2": "update", "3": "replace"}
        subprocess.run([sys.executable, script] + db_flag + ["--mode", modos[opc]])
    elif opc == "4":
        subprocess.run([sys.executable, script] + db_flag + ["--dry-run"])
    elif opc == "0":
        return

    pausa()


def opcion_analizar_video(db_path: str | None = None):
    """
    Analiza videos con IA: scene detection + muestreo por escena + keywords.

    Llama a analyze_video.py (--file para un video individual, --db para los
    pendientes de la DB o --dry-run para previsualizar).
    """
    import subprocess
    script = os.path.join(os.path.dirname(__file__), "scripts", "ai_media", "analyze_video.py")

    def _analizar_individual(db):
        ruta = input("  Ruta al video: ").strip()
        if not ruta:
            print("  Cancelado.")
            pausa()
            return
        if not os.path.isfile(ruta):
            print("  Archivo no encontrado.")
            pausa()
            return
        subprocess.run([sys.executable, script, "--file", ruta])
        pausa()

    def _analizar_pendientes(db):
        subprocess.run([sys.executable, script, "--db", db or leer_db()])
        pausa()

    def _analizar_dry(db):
        subprocess.run([sys.executable, script, "--db", db or leer_db(), "--dry-run"])
        pausa()

    _menu("ANALIZAR VIDEO (escenas + IA)", {
        "1": ("Analizar un video individual", _analizar_individual),
        "2": ("Analizar todos los pendientes de la DB", _analizar_pendientes),
        "3": ("Previsualizar (dry-run)", _analizar_dry),
    }, db_path, intro=(
        "Detecta cambios de escena (ffmpeg), muestrea ~10 imágenes por\n"
        "  escena, elige las más nítidas y las analiza con minicpm-v4.6\n"
        "  (keywords + descripción en una sola llamada por escena)."
    ))


def opcion_keypoints_contexto(db_path: str | None = None):
    """
    Escribe keypoints de contexto (devenir geográfico) en media_keypoints.

    Interpola la posición de videos/audios contra el track GPX (F1),
    marca transiciones baratas de elevación/astronomía/movimiento (F2),
    enriquece con Georef + clima con cache (F3) y escribe keypoints
    no redundantes (F4). Llama a scripts/keypoints_contexto.py.
    """
    import subprocess
    script = os.path.join(os.path.dirname(__file__), "scripts", "keypoints_contexto.py")

    def _ejecutar(modo: str):
        def _run(db):
            subprocess.run([sys.executable, script, "--db", db or leer_db(), "--mode", modo])
            pausa()
        return _run

    def _dry(db):
        subprocess.run([sys.executable, script, "--db", db or leer_db(), "--dry-run"])
        pausa()

    _menu("KEYPOINTS DE CONTEXTO (devenir geografico)", {
        "1": ("Procesar (solo pendientes)", _ejecutar("skip")),
        "2": ("Re-procesar todos (update)", _ejecutar("update")),
        "3": ("Limpiar y regenerar (replace)", _ejecutar("replace")),
        "4": ("Previsualizar (dry-run)", _dry),
    }, db_path, intro=(
        "Reconstruye la posicion de cada video/audio contra el track GPX\n"
        "  (interpolacion lineal por timestamp): cambios de elevacion, dia/\n"
        "  crepusculo/noche, movimiento, municipio/provincia y clima."
    ))


def opcion_geocode(db_path: str | None = None):
    """Menu para geocodificación inversa de coordenadas GPS."""
    limpiar_pantalla()
    print("=== LOCALIZACION (Geocodificar GPS) ===\n")

    print("  1) Ejecutar geocodificacion")
    print("  2) Previsualizar (dry-run)")
    print("  0) Volver\n")

    opc = input("  Opcion: ").strip()

    db_path = db_path or leer_db()
    from scripts import geocode

    if opc == "1":
        modo = _preguntar_modo(db_path)
        if modo is None:
            print("  Cancelado.")
            pausa()
            return
        args = ["--db", db_path]
        if modo != "skip":
            args += ["--mode", modo]
        geocode.main(args)
    elif opc == "2":
        geocode.main(["--db", db_path, "--dry-run"])
    elif opc == "0":
        return

    pausa()


def opcion_consolidar_medios(db_path: str | None = None):
    """Menu: consolidar medios de múltiples raíces en estructura unificada."""
    limpiar_pantalla()
    print("=== CONSOLIDAR MEDIOS (múltiples raíces → unificada) ===\n")
    print("  Detecta TODAS las raíces absolutas de la DB y las reubica en una")
    print("  estructura unificada (telegram/, testeos/, etc.) preservando subcarpetas.")
    print("  Usa mover_media por raíz; actualiza ingest_root al final.\n")
    print("  1) Previsualizar plan (dry-run)")
    print("  2) Consolidar (mover — borra originales)")
    print("  3) Consolidar (copiar — conserva originales, pregunta si actualiza DB)")
    print("  0) Volver\n")
    opc = input("  Opcion: ").strip()
    db_path = leer_db(db_path)
    from scripts import consolidar_medios
    if opc == "1":
        consolidar_medios.main(["--db", db_path, "--dry-run"])
    elif opc == "2":
        new_root = input("  Nueva raíz unificada (ej: G:/Flujos/Medios): ").strip()
        if not new_root:
            print("  Cancelado.")
            pausa()
            return
        consolidar_medios.main(["--db", db_path, "--new-root", new_root, "--mode", "mover"])
    elif opc == "3":
        new_root = input("  Nueva raíz unificada (ej: G:/Flujos/Medios): ").strip()
        if not new_root:
            print("  Cancelado.")
            pausa()
            return
        # Dejar que el script pregunte interactivamente si la DB apunta a originales o nuevos (evita doble prompt)
        consolidar_medios.main(["--db", db_path, "--new-root", new_root, "--mode", "copiar"])
    elif opc == "0":
        return
    pausa()


def opcion_gestion_rutas(db_path: str | None = None):
    """Submenú: gestión de rutas (relocate / mover / consolidar) con explicación de matriz de decisión."""
    def _relocalizar_explicado(db):
        print("\n  → Usá Relocalizar cuando los archivos YA fueron movidos con el")
        print("    explorador y solo hay que actualizar filepath_absoluto en la DB.")
        print("    No toca disco, solo DB (SQL substr prefijo).\n")
        opcion_relocalizar(db)

    def _ayuda_cual(db):
        limpiar_pantalla()
        print("=== ¿CUÁL USAR? ===\n")
        print("  Archivos YA movidos manualmente → Relocalizar (1)")
        print("  Archivos AÚN no movidos → Mover/Copiar (2)")
        print("  Medios dispersos en varias raíces (Telegram, Testeos, etc.) → Consolidar (3)")
        print("  Duda: ver docs/flujo_de_medios.md y README.md\n")
        pausa()

    _menu("GESTIÓN DE RUTAS", {
        "1": ("Relocalizar (solo DB) — ya moviste con Explorer", _relocalizar_explicado),
        "2": ("Mover/Copiar (FS+DB) — que el script mueva", opcion_mover_media),
        "3": ("Consolidar (múltiples raíces → unificada)", opcion_consolidar_medios),
        "4": ("Ayuda: ¿cuál usar?", _ayuda_cual),
    }, db_path, intro=(
        "  Matriz de decisión para actualizar rutas:\n"
        "  • Relocalizar = solo DB (rápido, sin FS).\n"
        "  • Mover/Copiar = FS + DB (mueve/copia archivos reales).\n"
        "  • Consolidar = multi-raíz → una raíz unificada (usa Mover por raíz)."
    ))


def _ver_backups(db_path: str | None = None):
    """Muestra lista de backups con tamaño/fecha y opción de podar."""
    db_path = leer_db(db_path)
    backups = listar_backups(db_path)
    print(f"\n  Backups en {_directorio_backups(db_path)}: {len(backups)} (máx 25 / 800 MB)\n")
    if not backups:
        print("  (no hay backups)")
    else:
        for i, (ruta, name, size) in enumerate(backups, 1):
            size_mb = size / (1024 * 1024)
            try:
                from datetime import datetime
                fecha = datetime.fromtimestamp(os.path.getmtime(ruta)).strftime("%Y-%m-%d %H:%M")
            except Exception:
                fecha = "?"
            print(f"  {i:2d}) {name:<45s} {size_mb:6.1f} MB  {fecha}")
        total_mb = sum(s for _, _, s in backups) / (1024 * 1024)
        print(f"\n  Total: {total_mb:.1f} MB en {len(backups)} archivos")
        if len(backups) > 20 or total_mb > 600:
            print("  Sugerencia: hay muchos backups, considerá podar manualmente los más viejos en db/backups/")
    pausa()


def opcion_mantenimiento(db_path: str | None = None):
    """Menu: mantenimiento general de la DB (backup, restore, exportar, etc)."""
    _menu_paginado("MANTENIMIENTO DB", [
        ("  -- Mantenimiento general --\n", {
            "1": ("Gestión de rutas (relocalizar / mover / consolidar)", opcion_gestion_rutas),
            "2": ("Backfill end_time", opcion_backfill_end_time),
            "3": ("Backup DB (solo backup, sin borrar)", opcion_backup_db),
            "4": ("Restore DB desde backup", opcion_restore_db),
            "5": ("Resetear DB (backup + limpiar)", opcion_reset_db),
            "6": ("Exportar DB a CSV", opcion_exportar_csv),
            "7": ("Auditar contenedores (streams faltantes)", opcion_auditar_contenedores),
            "8": ("Ver / podar backups", _ver_backups),
        }),
        ("  -- Auditoría de medios --\n", {
            "1": ("Buscar contenido repetido (audio)", opcion_repetir_contenido),
            "2": ("Correlacionar audio con frames", opcion_crossref_audio_frame),
        }),
        ("  -- Limpieza de datos --\n", {
            "1": ("Limpiar descripciones (eco del prompt)", opcion_limpiar_descripciones),
        }),
    ], db_path)


def opcion_auditar_contenedores(db_path: str | None = None):
    """Menu: auditar contenedores de video/audio con ffprobe."""
    from scripts import detectar_contenedores

    def _ejecutar(db):
        db = db or leer_db()
        modo = _preguntar_modo(db)
        if modo is None:
            print("  Cancelado.")
            pausa()
            return
        args = ["--db", db]
        if modo != "skip":
            args += ["--mode", modo]
        detectar_contenedores.main(args)
        pausa()

    def _dry(db):
        detectar_contenedores.main(["--db", db or leer_db(), "--dry-run"])
        pausa()

    _menu("AUDITAR CONTENEDORES", {
        "1": ("Ejecutar auditoría (anotar estado en DB)", _ejecutar),
        "2": ("Previsualizar (dry-run)", _dry),
    }, db_path)


def opcion_limpiar_descripciones(db_path: str | None = None):
    """Menu: limpiar descripciones con eco del prompt (meta-intros)."""
    from scripts import limpiar_descripciones

    def _ejecutar(db):
        limpiar_descripciones.main(["--db", db or leer_db()])
        pausa()

    def _dry(db):
        limpiar_descripciones.main(["--db", db or leer_db(), "--dry-run"])
        pausa()

    _menu("LIMPIAR DESCRIPCIONES", {
        "1": ("Ejecutar limpieza (con backup)", _ejecutar),
        "2": ("Previsualizar (dry-run)", _dry),
    }, db_path)


def opcion_repetir_contenido(db_path: str | None = None):
    """Menu: buscar contenido repetido por coincidencias de audio."""
    from scripts import repetir_contenido

    def _contra(db):
        contra = input("  Ruta del archivo a comparar contra el resto: ").strip()
        if not contra:
            print("  Cancelado.")
            pausa()
            return
        args = ["--db", db or leer_db(), "--contra", contra]
        if _preguntar_sn("Ejecutar"):
            repetir_contenido.main(args)
        pausa()

    def _todos(db):
        args = ["--db", db or leer_db()]
        if _preguntar_sn("Ejecutar todos contra todos"):
            repetir_contenido.main(args)
        pausa()

    _menu("CONTENIDO REPETIDO (AUDIO)", {
        "1": ("Comparar un archivo contra el resto", _contra),
        "2": ("Todos contra todos", _todos),
    }, db_path)


def opcion_crossref_audio_frame(db_path: str | None = None):
    """Menu: correlacionar contenido de audio con frames de video."""
    from scripts import audio_frame_crossref

    def _ejecutar(db):
        archivo = input("  Archivo de video/audio a analizar (ruta): ").strip()
        if not archivo:
            print("  Cancelado.")
            pausa()
            return
        args = ["--db", db or leer_db(), "--archivo", archivo]
        frames_dir = input("  Carpeta para extraer frames (Enter = no extraer): ").strip()
        if frames_dir:
            args += ["--frames-dir", frames_dir]
        if _preguntar_sn("Ejecutar"):
            audio_frame_crossref.main(args)
        pausa()

    _menu("AUDIO <-> FRAMES", {
        "1": ("Correlacionar audio con frames", _ejecutar),
    }, db_path)


def opcion_ingestar_gpx(db_path: str | None = None):
    """Menu: ingerir un archivo GPX (track GPS)."""
    limpiar_pantalla()
    print("=== INGERIR TRACK GPS (GPX) ===\n")

    disponibles = _buscar_gpx_disponibles()
    if disponibles:
        print("  Archivos .gpx encontrados:")
        for i, ruta in enumerate(disponibles, 1):
            print(f"    {i}) {ruta}")
        print("    0) Ingresar ruta manual")
        print()
        eleccion = input(f"  Seleccionar track [1]: ").strip()
        if eleccion == "0":
            gpx_path = input("  Ruta al archivo .gpx: ").strip()
        elif eleccion == "":
            gpx_path = disponibles[0]
        else:
            try:
                idx = int(eleccion)
                if 1 <= idx <= len(disponibles):
                    gpx_path = disponibles[idx - 1]
                else:
                    print(f"  Opcion invalida: {eleccion}")
                    pausa()
                    return
            except ValueError:
                print(f"  Opcion invalida: {eleccion}")
                pausa()
                return
    else:
        gpx_path = input("  Ruta al archivo .gpx: ").strip()

    if not gpx_path:
        print("  No se ingreso ninguna ruta.")
        pausa()
        return
    if not os.path.isfile(gpx_path):
        print(f"  ❌ Archivo no encontrado: {gpx_path}")
        pausa()
        return

    # Modo de backfill de altitud
    modo_str = _preguntar_modo(db_path)
    if modo_str is None:
        print("  Cancelado.")
        pausa()
        return

    omitir_wpts = _preguntar_sn("Omitir waypoints")
    omitir_alt = _preguntar_sn("Omitir backfill de altitud")
    dry_run = _preguntar_sn("Solo previsualizar (dry-run)")

    print(f"\n  Resumen: gpx={gpx_path}  modo={modo_str}"
          f"  waypoints={'NO' if omitir_wpts else 'SI'}"
          f"  altitud={'NO' if omitir_alt else 'SI'}"
          f"  dry_run={'SI' if dry_run else 'NO'}")
    if not _preguntar_sn("Ejecutar"):
        print("  Cancelado.")
        pausa()
        return

    print("\n  Ejecutando ingesta GPX...\n")
    from scripts import ingest_gpx
    args = ["--gpx", gpx_path]
    args.extend(["--mode", modo_str])
    _args_sn(args, [
        (omitir_wpts, "--no-waypoints"),
        (omitir_alt, "--no-altitude"),
        (dry_run, "--dry-run"),
    ])
    if db_path:
        args.extend(["--db", db_path])
    ingest_gpx.main(args)
    pausa()


def opcion_mover_media(db_path: str | None = None):
    """Menu para mover o copiar archivos de medios y actualizar la DB."""
    limpiar_pantalla()
    print("=== MOVER / COPIAR MEDIOS ===\n")
    print("  1) Mover archivos (actualiza DB automáticamente)")
    print("  2) Copiar archivos (sólo copia, no actualiza DB)")
    print("  3) Copiar archivos y actualizar DB")
    print("  0) Volver\n")

    opc = input("  Opcion: ").strip()
    db_path = leer_db(db_path)

    if opc not in ("1", "2", "3"):
        return

    new_root = input("  Nueva raíz de archivos: ").strip()
    if not new_root:
        print("  Debe especificar una raíz.")
        pausa()
        return

    old_root = input(
        "  Raíz anterior (dejar vacío para leer de DB): "
    ).strip() or None

    dry_run = input("  ¿Previsualizar sin escribir? (s/n) [n]: ").strip().lower() == "s"

    from scripts import mover_media

    if opc == "1":
        args = ["--new-root", new_root, "--mode", "mover", "--db", db_path]
        if old_root:
            args += ["--old-root", old_root]
        if dry_run:
            args.append("--dry-run")
        mover_media.main(args)
    elif opc == "2":
        args = ["--new-root", new_root, "--mode", "copiar", "--db", db_path]
        if old_root:
            args += ["--old-root", old_root]
        if dry_run:
            args.append("--dry-run")
        mover_media.main(args)
    elif opc == "3":
        args = [
            "--new-root",
            new_root,
            "--mode",
            "copiar",
            "--update-db",
            "--db",
            db_path,
        ]
        if old_root:
            args += ["--old-root", old_root]
        if dry_run:
            args.append("--dry-run")
        mover_media.main(args)

    pausa()


def opcion_ayuda():
    """Submenu de ayuda con detalle por comando."""
    def _ayuda_general(_db):
        limpiar_pantalla()
        print(AYUDA)
        pausa()

    def _ayuda_ingest(_db):
        from scripts import ingest
        ingest.main(["--help"])
        pausa()

    def _ayuda_query(_db):
        from scripts import query
        query.main(["--help"])
        pausa()

    def _ayuda_relocate(_db):
        from scripts import relocate
        relocate.main(["--help"])
        pausa()

    def _ayuda_improve_db(_db):
        limpiar_pantalla()
        print("============ IMPROVE-DB ============\n")
        print("  Ejecuta pasos de mejora sobre la base de datos (~22 pasos).")
        print("  1) Todos y 2) Manual cubren TODOS los pasos (overnight en skip).")
        print("  3..7 agrupan por tipo de medio con repeticion permitida.")
        print("  Uso: python flujos.py improve-db [--steps X,Y] [--mode skip|update|replace] [--db RUTA] [--no-mostrar] [--workers N]\n")
        print("  Pasos (improve_db --list para detalle):")
        print("    colors, keywords, descriptions, combinado, transcribe, keypoints,")
        print("    timestamps, gps, video_metadata, audio_tagging, kw_transcripcion,")
        print("    kw_texto, refinar, geocode, gradiente, weather, dia_semana,")
        print("    astronomia, analizar_video, keypoints_video, keypoints_contexto,")
        print("    limpiar_descripciones")
        print()
        print("  7) Enriquecimiento / Curaduria = refinar, limpiar, timestamps, gps,")
        print("     geocode, gradiente, clima, dia semana, astronomia.")
        print("  Todos saltea sin red/componente y avisa que quedo pendiente.")
        print("  Flags: --list para listar pasos; --workers N (default 1).")
        pausa()

    def _ayuda_geocode(_db):
        limpiar_pantalla()
        print("============ GEOCODE ============\n")
        print("  Geocodifica coordenadas GPS (lat,lon) a provincia/localidad")
        print("  usando la API Georef Argentina (batch).\n")
        print("  Uso: python flujos.py geocode [--limit N] [--mode skip|update|replace] [--dry-run] [--coords LAT,LON] [--db RUTA]\n")
        print("  Tambien desde consola:")
        print("    python scripts/geocode.py --coords -34.6037,-58.3816 --mode update")
        pausa()

    def _ayuda_gradient(_db):
        limpiar_pantalla()
        print("============ GRADIENT ============\n")
        print("  Calcula pendientes y esfuerzo fisico entre puntos GPS")
        print("  consecutivos, ordenados por timestamp.\n")
        print("  Columnas que actualiza:\n")
        print("    distance_from_prev_m    Distancia Haversine (m)")
        print("    elevation_gain_m        Cambio de elevacion (m)")
        print("    gradient_pct            Pendiente porcentual")
        print("    cumul_distance_m        Distancia acumulada (m)")
        print("    cumul_elevation_gain_m  Ganancia elevacion acumulada (m)\n")
        print("  Uso: python flujos.py gradient [--mode skip|update|replace] [--dry-run] [--verbose/-v] [--quiet/-q] [--db RUTA]\n")
        print("  Tambien desde consola:")
        print("    python scripts/gradiente.py --mode update --dry-run --verbose")
        pausa()

    def _ayuda_astronomia(_db):
        limpiar_pantalla()
        print("============ ASTRONOMIA ============\n")
        print("  Calcula la posición del sol (elevación, azimut) y clasifica")
        print("  el momento del día usando el algoritmo NOAA Solar Calculator.\n")
        print("  Columnas que actualiza:\n")
        print("    sun_elevation      Altura del sol sobre horizonte (°)")
        print("    sun_azimuth        Dirección del sol (0°=N, 90°=E)")
        print("    sun_distance_au    Distancia al sol en UA")
        print("    twilight_period    Clasificación: día, golden_hour, blue_hour,")
        print("                       crepúsculo civil/naútico/astronómico, noche\n")
        print("  Uso: python flujos.py astronomia [--mode skip|update|replace] [--dry-run] [--verbose/-v] [--db RUTA]\n")
        print("  Requiere: latitude, longitude y timestamp_utc en la DB.")
        print("  Algoritmo: NOAA Solar Calculator (Python puro, 0 dependencias)\n")
        print("  Precision: ~0.01°\n")
        pausa()

    def _ayuda_check(_db):
        limpiar_pantalla()
        print("============ CHECK-DB ============\n")
        print("  Inspecciona la base de datos (resumen por tipo/tablas, batches, GPS).")
        print("  Uso: python flujos.py check-db [--db RUTA] [--verbose/-v] [--limit N]\n")
        print("  Alias: check-data / check-clima / check-geo (stats clima/dia/geocode)")
        print("    Uso: python flujos.py check-data [--db RUTA] [--limit N]\n")
        print("============ CHECK-GPS ============\n")
        print("  Revisa que archivos tienen GPS (via ExifTool).")
        print("  Uso: python flujos.py check-gps [--db RUTA] [--exiftool RUTA] [--samples N] [--folder CARPETA]\n")
        print("  Para un analisis completo: python scripts/check_gps.py --folder D:/Fotos")
        pausa()

    def _ayuda_corregir360(_db):
        limpiar_pantalla()
        print("============ CORREGIR-360 ============\n")
        print("  Corrige timestamps de videos 360° Insta360 post-ingesta.")
        print("  Insta360 guarda CreateDate en UTC; ingest lo trato como ART (-3).")
        print("  Corrige a timestamp_utc real y opcionalmente reubica en GPX.\n")
        print("  Uso: python flujos.py corregir-360 [--mode skip|update|replace] [--dry-run] [--reubicar] [--json] [--verbose/-v] [--db RUTA]\n")
        print("  Tambien desde consola:")
        print("    python scripts/corregir_timestamp_360.py --mode update --reubicar --dry-run")
        print("  Docs: docs/correccion_timestamp_360.md")
        pausa()

    _menu_paginado("AYUDA", [
        ("Comandos basicos (1/2)\n", {
            "1": ("Ayuda general", _ayuda_general),
            "2": ("ingest  - Ingestion de medios", _ayuda_ingest),
            "3": ("query   - Consultas a la base de datos", _ayuda_query),
            "4": ("relocate - Relocalizar medios", _ayuda_relocate),
            "5": ("improve-db - Mejorar base de datos", _ayuda_improve_db),
            "6": ("geocode - Geocodificar coordenadas GPS", _ayuda_geocode),
        }),
        ("Comandos avanzados (2/2)\n", {
            "1": ("gradient - Calcular gradientes de ruta", _ayuda_gradient),
            "2": ("astronomia - Posicion del sol y twilight", _ayuda_astronomia),
            "3": ("check-db / check-gps / check-data", _ayuda_check),
            "4": ("corregir-360 - Corregir timestamps 360", _ayuda_corregir360),
        }),
    ])


def tui():
    """Menu interactivo principal."""
    def _cabecera():
        mostrar_bienvenida()  # NO limpiar dos veces: mostrar_bienvenida ya hace limpiar_pantalla()
        db_path = leer_db()
        if os.path.isfile(db_path):
            conn = sqlite3.connect(db_path)
            try:
                print(resumen_db(conn))
                print("  Para ver el detalle completo: Menu 4 > Ver resumen\n")
            except sqlite3.OperationalError:
                print("  (Base de datos vacia o sin schema)\n")
            conn.close()
        else:
            print("  (Base de datos no encontrada - ejecuta 'Ingesta' primero)\n")

    def _chau():
        limpiar_pantalla()
        print("  Chau.")

    _menu("", {
        "1": ("Preparar medios", lambda db: opcion_preparar(db)),
        "2": ("Ingesta", lambda db: opcion_ingesta(db)),
        "3": ("Mejorar base de datos", lambda db: opcion_improve_db(db)),
        "4": ("Consultar base de datos", lambda db: opcion_consultar(db)),
        "5": ("Mantenimiento DB", lambda db: opcion_mantenimiento(db)),
        "6": ("Visualizaciones", lambda db: opcion_visualizaciones(db)),
        "7": ("Scripts temporarios (correcciones puntuales)", lambda db: opcion_scripts_temporarios(db)),
        "9": ("Ayuda", lambda db: opcion_ayuda()),
    }, db_path=leer_db(), pre_titulo=_cabecera, etiqueta_salir="Salir", on_salir=_chau)


# ── Visualizaciones ───────────────────────────────────────────────────────────

def opcion_visualizaciones(db_path: str | None = None):
    """Menu: visualizaciones de la ruta y los datos (mapas, deploy web, TD...)."""
    _menu("VISUALIZACIONES", {
        "1": ("Mapas", opcion_mapas),
        "2": ("Exportar visualización web (deploy)", opcion_exportar_visualizacion),
        "3": ("TouchDesigner (puente OSC)", opcion_touchdesigner),
        "4": ("Galerías de keypoints (HTML)", opcion_galerias_keypoints),
    }, db_path, intro=(
        "  Hub en deploy/index.html → 3 entradas: Panel de viaje (panel/),\n"
        "  Keypoints transcripción y Keypoints contexto (keypoints/*).\n"
        "  El snapshot deploy/db/visualizacion.db ahora incluye la tabla\n"
        "  keypoints (posiciones materializadas). Ver docs/deploy.md."
    ))


def opcion_mapa_unificado(db_path: str | None = None):
    """Menu: mapa unificado offline/online con clusters y expansión."""
    limpiar_pantalla()
    print("=== MAPA UNIFICADO (offline/online) ===\n")
    print("  Un punto por cada 'algo' con GPS: media + contexto + waypoints (opcional).")
    print("  Transcripción se despliega por segmentos en el mismo mapa (1 a la vez).")
    print("  Clusters multicolor (1 tipo=sólido, mixto=conic-gradient).\n")
    print("  Offline = db/flujos.db + HTML autocontenido (TD file://)")
    print("  Online  = deploy/db/visualizacion.db + CDN\n")
    modo = input("  Modo [offline/online] [offline]: ").strip().lower() or "offline"
    if modo not in ("offline", "online"):
        modo = "offline"
    output = input(f"  Archivo de salida [mapas/mapa_unificado.html]: ").strip() or "mapas/mapa_unificado.html"
    con_contexto = not _preguntar_sn("Omitir puntos de contexto")
    con_waypoints = _preguntar_sn("Incluir waypoints (capa opcional)")
    sin_cluster = _preguntar_sn("Desactivar clusters")
    sin_segmentos = _preguntar_sn("Omitir segmentos de transcripción")
    dry = _preguntar_sn("Solo dry-run (conteos)")
    custom_db = input(f"  ?Usar otra DB? (default según modo) [Enter para default]: ").strip()
    print(f"\n  Resumen: modo={modo} output={output} contexto={'SI' if con_contexto else 'NO'} waypoints={'SI' if con_waypoints else 'NO'}")
    if not _preguntar_sn("Generar mapa"):
        print("  Cancelado.")
        pausa()
        return
    from scripts import mapa_unificado
    args = ["--modo", modo, "--output", output]
    if not con_contexto:
        args.append("--no-contexto")
    if con_waypoints:
        args.append("--con-waypoints")
    if sin_cluster:
        args.append("--sin-cluster")
    if sin_segmentos:
        args.append("--sin-segmentos")
    if dry:
        args.append("--dry-run")
    if custom_db:
        args.extend(["--db", custom_db])
    elif db_path and modo == "offline":
        args.extend(["--db", db_path])
    mapa_unificado.main(args)
    pausa()


def opcion_mapas(db_path: str | None = None):
    """Menu: mapas de la ruta y los municipios (Folium)."""
    _menu("MAPAS", {
        "1": ("Mapa de ruta (Folium)", lambda db: opcion_mapa()),
        "2": ("Mapas por municipio (Folium)", opcion_mapas_municipio),
        "3": ("Mapa unificado (offline/online, clusters, segmentos)", opcion_mapa_unificado),
    }, db_path)


def opcion_exportar_visualizacion(db_path: str | None = None):
    """Menu: exportar el snapshot web (visualizacion.db).

    exportar_visualizacion.py: snapshot SQLite de flujos.db; deploy genérico por
    defecto a deploy/, con copia de medios y transcode opcional. El spec del motor
    de loop ya no se genera acá: TD lo genera en runtime (puente_td.py → td/spec_fluir.json).
    """
    base = os.path.dirname(__file__)
    exportador = os.path.join(base, "scripts", "exportar_visualizacion.py")

    def _deploy_dir_custom() -> str | None:
        ruta = input("  Carpeta de deploy (Enter = deploy/ por defecto): ").strip()
        if not ruta:
            return os.path.join(base, "deploy")
        if not os.path.isdir(ruta):
            print(f"  Error: la carpeta '{ruta}' no existe.")
            pausa()
            return None
        return ruta

    def _preguntar_transcode() -> bool:
        """Pregunta si transcodificar videos grandes/360° (default: sí)."""
        return _preguntar_sn("Transcodificar videos grandes/360° a MP4 web", default=True)

    def _deploy_default(db):
        cmd = [sys.executable, exportador]
        if _preguntar_transcode():
            cmd.append("--transcode")
        subprocess.run(cmd)
        pausa()

    def _deploy_custom(db):
        ruta = _deploy_dir_custom()
        if not ruta:
            return
        cmd = [sys.executable, exportador, "--deploy-dir", ruta]
        if _preguntar_transcode():
            cmd.append("--transcode")
        subprocess.run(cmd)
        pausa()

    def _snapshot(db):
        subprocess.run([sys.executable, exportador, "--snapshot-local"])
        pausa()

    def _deploy_dry(db):
        subprocess.run([sys.executable, exportador, "--dry-run"])
        pausa()

    _menu("EXPORTAR VISUALIZACION WEB (deploy)", {
        "1": ("Deploy a deploy/ (pregunta si transcodificar)", _deploy_default),
        "2": ("Deploy a otra carpeta (pregunta si transcodificar)", _deploy_custom),
        "3": ("Re-exportar snapshot local (deploy/db, sin copiar medios)", _snapshot),
        "4": ("Previsualizar deploy (dry-run)", _deploy_dry),
    }, db_path, intro=(
        "Exporta un snapshot de flujos.db para la web portable (ver docs/deploy.md).\n"
        "  deploy/ es hub + panel + keypoints: index.html (hub) → panel/index.html\n"
        "  (lienzo), keypoints/transcripciones/ y keypoints/contexto/ (50 al azar\n"
        "  por carga, player+mapa+fotos lazy vía api/keypoints.php + fotos_cercanas.php).\n"
        "  El snapshot deploy/db/visualizacion.db incluye ahora keypoints con\n"
        "  posición materializada (media o interpolación GPX). Copia a media/ y\n"
        "  pregunta si transcodificar 360°/grandes a MP4 web (default sí)."
    ))


def opcion_touchdesigner(db_path: str | None = None):
    """Menú: puente BD → TouchDesigner vía OSC (elecciones, Fluir, probar OSC).

    Expone los modos actuales de puente_td.py y osc_probe.py en el TUI:
    envían las nubes de elecciones a TD por 9000 o escuchan la ráfaga del
    "Fluir" por 9001 (respuesta por 9002). Los modos legacy (colores, nube,
    imágenes de un color, loop completo) se eliminaron porque apuntaban a ops
    que ya no existen en el .toe.
    Requiere TouchDesigner corriendo con osc_in1 (9000) / osc_out1 (9001).
    """
    base = os.path.dirname(__file__)
    puente = os.path.join(base, "scripts", "td", "puente_td.py")
    probe = os.path.join(base, "scripts", "td", "osc_probe.py")

    def _correr(script: str, *args: str) -> None:
        """Ejecuta un script con salida UTF-8; Ctrl+C detiene el hijo sin cerrar flujos."""
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        proc = subprocess.Popen([sys.executable, script, *args], env=env, creationflags=flags)
        try:
            proc.wait()
        except KeyboardInterrupt:
            print("\n  (Ctrl+C: deteniendo script...)")
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
        pausa()

    def _preguntar_loop_secs() -> list[str]:
        """Pregunta la duración del arco del loop (default 300 s); explica qué implica."""
        print("  Duración del arco del loop (0..N segundos): es la ventana temporal")
        print("  donde se reparten los medios (el keypoint de cada pieza cae dentro).")
        print("  Más segundos = más aire entre piezas; menos = más ritmo. Enter = 300 s.")
        texto = input("  Duración del loop en segundos [300]: ").strip()
        try:
            return ["--loop-secs", str(float(texto))]
        except ValueError:
            return []

    def _elecciones(db):
        grupos = input("  Grupos (coma, Enter = todos) [ej: horas,tags]: ").strip()
        args = ["elecciones", "--db", db_path]
        if grupos:
            args += ["--grupo", grupos]
        _correr(puente, *args)

    def _fluir_una_vez(db):
        _correr(puente, "fluir", "--una-vez", "--db", db_path, *_preguntar_loop_secs())

    def _fluir_continua(db):
        _correr(puente, "fluir", "--db", db_path, *_preguntar_loop_secs())

    def _probar_osc(db):
        puerto = input("  Puerto [9001]: ").strip()
        segundos = input("  Segundos de ventana (0 = hasta Enter) [0]: ").strip()
        args = []
        if puerto:
            try:
                args.append(str(int(puerto)))
            except ValueError:
                pass
        if segundos and args:
            try:
                args.append(str(float(segundos)))
            except ValueError:
                pass
        _correr(probe, *args)

    def _menu_fluir(db):
        _menu("FLUIR (OSC 9001 <- TD → 9002)", {
            "1": ("Una ráfaga (prueba rápida)", _fluir_una_vez),
            "2": ("Modo instalación: escucha continua (Enter para detener)", _fluir_continua),
        }, db_path, intro=(
            "  Recibe la ráfaga del botón 'Fluir' de TouchDesigner por 9001,\n"
            "  la acumula por grupo, genera el spec del loop y lo envía por 9002.\n"
            "  Cada ráfaga arma un loop de 300 s por defecto: los medios se\n"
            "  reparten dentro de esa ventana temporal (0..300 s). El modo\n"
            "  instalación escucha sin límite de tiempo: queda activo hasta\n"
            "  que presiones Enter para detenerlo."
        ), cerrar_al_ejecutar=True)

    _menu("TOUCHDESIGNER (puente OSC)", {
        "1": ("Enviar elecciones (horas, municipios, colores, tags...)", _elecciones),
        "2": ("Modo 'Fluir' (recibir ráfaga de TD y generar loop)", _menu_fluir),
        "3": ("Probar OSC (eco)", _probar_osc),
    }, db_path, intro=(
        "  Puente BD → TouchDesigner vía OSC. Requiere TouchDesigner corriendo\n"
        "  con osc_in1 (9000) / osc_out1 (9001). Los modos envían las nubes de\n"
        "  elecciones a TD o escuchan la ráfaga del 'Fluir' (9001 → loop por 9002).\n"
        "  Los modos legacy (colores, nube, imágenes de un color, loop completo)\n"
        "  se quitaron porque apuntaban a ops que ya no existen en el .toe."
    ))


def opcion_mapa():
    """Menu para generar mapa HTML interactivo con Folium."""
    limpiar_pantalla()
    print("=== MAPA DE RUTA (Folium) ===\n")
    print("  Genera un mapa HTML interactivo desde el track GPX y los GPS de la BD.\n")
    print("  La ruta principal se dibuja con el track GPX; los medios quedan como\n")
    print("  marcadores. Se reportan discrepancias media vs track (tolerancia 1000 m).\n")
    print("  El mapa se guarda como archivo HTML en el directorio actual\n")
    print("  (o en la ruta que se indique).\n")
    print("  Opciones:")
    print("    --output PATH  Ruta de salida (default: mapa_ruta.html)")
    print("    --no-markers   Sin marcadores en los puntos")
    print("    --road-colors  Colorear segmentos por pendiente")
    print("    --tolerancia-metros N  Tolerancia para reportar discrepancias (default 1000)")


    output = input("  Archivo de salida [mapas/mapa_ruta.html]: ").strip() or "mapas/mapa_ruta.html"
    road_colors = _preguntar_sn("Colorear segmentos por pendiente")
    no_markers = _preguntar_sn("Omitir marcadores")
    tolerancia = input("  Tolerancia de discrepancias en metros [1000]: ").strip()
    custom_db = input(f"  ?Usar otra DB? (default: {leer_db()}) [Enter para default]: ").strip()

    print(f"\n  Resumen: output={output}  road_colors={'SI' if road_colors else 'NO'}  "
          f"no_markers={'SI' if no_markers else 'NO'}  "
          f"tolerancia={tolerancia or 1000}m")
    if not _preguntar_sn("Generar mapa"):
        print("  Cancelado.")
        pausa()
        return

    print("\n  Generando mapa...\n")

    from scripts import mapa_ruta
    args = ["--output", output]
    _args_sn(args, [
        (road_colors, "--road-colors"),
        (no_markers, "--no-markers"),
    ])
    if tolerancia.strip():
        args.extend(["--tolerancia-metros", tolerancia.strip()])
    if custom_db:
        args.extend(["--db", custom_db])
    mapa_ruta.main(args)

    pausa()


# ── Mapas por municipio ───────────────────────────────────────────────────────

def opcion_mapas_municipio(db_path: str | None = None):
    """Menu para generar un mapa HTML por municipio recorrido, con variantes."""
    limpiar_pantalla()
    print("=== MAPAS POR MUNICIPIO (Folium) ===\n")
    print("  Genera un mapa HTML por cada municipio recorrido, con variantes.\n")
    print("  Cada archivo sigue la lógica: mapa_municipio_<municipio>_<variante>.html")
    print("  (sin acentos: 'Río Hondo' -> 'Rio_Hondo')")
    print("  Los tiles de la vista inicial se incrustan en cada HTML (sin red al abrir en TD).\n")
    print("  Variantes:")
    print("    ruta       Puntos del municipio + línea que los conecta")
    print("    puntos     Solo los marcadores del municipio, sin línea")
    print("    contexto   Puntos destacados sobre la ruta completa")
    print("    gradiente  Segmentos coloreados por pendiente\n")

    output = input("  Carpeta de salida [mapas]: ").strip() or "mapas"
    solo_faltantes = _preguntar_sn("Generar solo los que faltan", default=True)
    mode = "skip" if solo_faltantes else "update"

    variantes = []
    for var, desc in (
        ("ruta", "Puntos + ruta"),
        ("puntos", "Solo puntos"),
        ("contexto", "Puntos + contexto ruta completa"),
        ("gradiente", "Puntos + gradiente"),
    ):
        if _preguntar_sn(f"Incluir variante '{var}' ({desc})", default=True):
            variantes.append(var)
    if not variantes:
        print("  Debes elegir al menos una variante.")
        pausa()
        return

    municipio = input("  Filtrar por municipio (Enter = todos): ").strip()
    custom_db = input(f"  ?Usar otra DB? (default: {leer_db()}) [Enter para default]: ").strip()

    print(f"\n  Resumen: output={output}  modo={'solo faltantes' if mode == 'skip' else 'todos'}  "
          f"variantes={','.join(variantes)}  municipio={municipio or 'TODOS'}")
    if not _preguntar_sn("Generar mapas"):
        print("  Cancelado.")
        pausa()
        return

    print("\n  Generando mapas...\n")

    from scripts import mapas_municipio
    args = ["--output", output, "--mode", mode, "--variantes", ",".join(variantes)]
    if municipio:
        args.extend(["--municipio", municipio])
    if custom_db:
        args.extend(["--db", custom_db])
    mapas_municipio.main(args)

    pausa()


# ── Galerías de keypoints ───────────────────────────────────────────────────

def _ejecutar_galeria_keypoints(tipo: str, db_path: str | None = None):
    """Genera una galería HTML de keypoints (transcripción o contexto) con N al azar."""
    limpiar_pantalla()
    titulo = "transcripción" if tipo == "transcripcion" else "contexto"
    print(f"=== GALERÍA DE KEYPOINTS — {titulo.upper()} ===\n")
    print("  Genera un HTML con N keypoints al azar; click → reproducción")
    print("  del audio/video, mapa con la localización y slideshow de las")
    print("  10 fotos más cercanas (distancia geográfica, fallback temporal).\n")
    try:
        n_str = input("  Cantidad de keypoints al azar [50]: ").strip() or "50"
        n = int(n_str)
        if n <= 0:
            raise ValueError
    except ValueError:
        print("  Cantidad inválida.")
        pausa()
        return
    default_out = f"pruebas/keypoints_{tipo}.html"
    output = input(f"  Archivo de salida [{default_out}]: ").strip() or default_out
    custom_db = input(f"  ?Usar otra DB? (default: {leer_db(db_path)}) [Enter para default]: ").strip()
    print(f"\n  Resumen: tipo={tipo}  n={n}  output={output}")
    if not _preguntar_sn("Generar galería"):
        print("  Cancelado.")
        pausa()
        return
    print("\n  Generando galería...\n")
    from scripts import generar_galeria_keypoints
    args = ["--tipo", tipo, "--n", str(n), "--output", output]
    if custom_db:
        args.extend(["--db", custom_db])
    elif db_path:
        args.extend(["--db", db_path])
    generar_galeria_keypoints.main(args)
    pausa()


def opcion_galerias_keypoints(db_path: str | None = None):
    """Menu: galerías HTML de keypoints al azar."""
    _menu("GALERÍAS DE KEYPOINTS", {
        "1": ("Keypoints de transcripción (50 al azar)", lambda db: _ejecutar_galeria_keypoints("transcripcion", db)),
        "2": ("Keypoints de contexto (50 al azar)", lambda db: _ejecutar_galeria_keypoints("contexto", db)),
    }, db_path, intro=(
        "  Local: genera pruebas/keypoints_*.html con file:// (solo local,\n"
        "  tras relocate hay que regenerar). Portable: el deploy ya sirve las\n"
        "  mismas galerías en deploy/keypoints/*/ (PHP + RANDOM por carga,\n"
        "  player via servir_medio.php, fotos lazy). Reexportar deploy tras\n"
        "  tocar keypoints para actualizar visualizacion.db."
    ))


def opcion_scripts_temporarios(db_path: str | None = None):
    """Menu temporario: correcciones puntuales no estabilizadas."""
    def _corregir_360(db):
        db = db or leer_db()
        print("\n  Corregir timestamps 360 (CreateDate UTC→ART)")
        print("  Lee QuickTime:CreateDate del archivo; recalcula timestamp_utc/original.")
        print("")
        modo = _preguntar_modo(db)
        if modo is None:
            print("  Cancelado.")
            pausa()
            return
        dry = _preguntar_sn("Solo previsualizar (dry-run)")
        reubicar = _preguntar_sn("Re-ubicar contra GPX tras corregir (--reubicar)")
        from scripts import corregir_timestamp_360
        args = ["--db", db, "--mode", modo]
        if dry:
            args.append("--dry-run")
        if reubicar:
            args.append("--reubicar")
        corregir_timestamp_360.main(args)
        pausa()

    _menu("SCRIPTS TEMPORARIOS", {
        "1": ("Corregir timestamps 360 (CreateDate UTC→ART)", _corregir_360),
    }, db_path, intro=(
        "  Correcciones puntuales, no parte del pipeline estable.\n"
        "  Quitar al estabilizar ingesta (ver docs/correccion_timestamp_360.md)."
    ))


# ── Backfill end_time ─────────────────────────────────────────────────────────

def opcion_backfill_end_time(db_path: str | None = None):
    """Calcula end_time para registros existentes que no lo tienen.

    end_time = timestamp_utc (+ duration_secs para videos/audios), calculado
    en Python: el datetime() de SQLite no parsea ISO con 'T'/'Z' y devolvía
    end_time == timestamp_utc. Re-ejecutable: en modo skip/update solo toca
    registros con end_time NULL.
    """
    from datetime import datetime, timedelta, timezone

    db_path = leer_db(db_path)
    if not os.path.isfile(db_path):
        print("  No se encuentra la base de datos.")
        return

    def _as_aware_utc(v: str) -> datetime:
        """Parsea timestamp_utc y lo fuerza a aware UTC.

        El timestamp_utc de la DB está normalizado a UTC, pero por robustez
        se normaliza la 'Z' y, si quedó naive, se asume UTC.
        """
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    conn = sqlite3.connect(db_path)
    try:
        # Primero verificar si la columna existe
        cols = [row[1] for row in conn.execute("PRAGMA table_info(media)")]
        if "end_time" not in cols:
            print("  La columna end_time no existe en la DB.")
            print("  Ejecutá primero una ingesta o el schema.sql.")
            return

        # Preguntar modo
        modo = _preguntar_modo(db_path)
        if modo is None:
            print("  Cancelado.")
            return

        # M3: reparar también los end_time que el bug viejo dejó incorrectos
        # (end_time == timestamp_utc en videos/audios con duration_secs)
        reparar = _preguntar_sn(
            "Reparar end_time incorrectos (end_time == timestamp_utc con duración)",
            default=False,
        )
        condicion_pendiente = "end_time IS NULL"
        if reparar:
            condicion_pendiente = (
                "(end_time IS NULL OR "
                "(end_time = timestamp_utc AND duration_secs IS NOT NULL))"
            )

        if modo == "replace":
            # Backup automático antes de limpiar masivo
            conn.commit()
            conn.close()
            bak = _auto_backup(db_path)
            if bak:
                print(f"  ✓ Backup automático: {os.path.basename(bak)}")
            conn = sqlite3.connect(db_path)
            print("  Modo replace: limpiando end_time existentes...")
            conn.execute("UPDATE media SET end_time = NULL WHERE timestamp_utc IS NOT NULL")
            conn.commit()

        # Contar cuántos faltan (o están incorrectos)
        pendientes = conn.execute(
            f"SELECT COUNT(*) FROM media "
            f"WHERE {condicion_pendiente} AND timestamp_utc IS NOT NULL"
        ).fetchone()[0]

        if pendientes == 0:
            print("  Todos los registros ya tienen end_time válido.")
            return

        print(f"  Calculando end_time para {pendientes} registros...")

        # Calcular end_time en Python (datetime.fromisoformat soporta 'T' y 'Z'):
        #   punto:    end_time = timestamp_utc
        #   segmento: end_time = timestamp_utc + duration_secs
        updated_punto = 0
        updated_seg = 0
        errores = 0
        filas = conn.execute(f"""
            SELECT id, timestamp_utc, duration_secs
            FROM media
            WHERE {condicion_pendiente}
              AND timestamp_utc IS NOT NULL
        """).fetchall()

        for mid, ts_utc, dur in filas:
            try:
                dt_base = _as_aware_utc(ts_utc)
                if dur is not None:
                    dt_end = dt_base + timedelta(seconds=float(dur))
                    updated_seg += 1
                else:
                    dt_end = dt_base
                    updated_punto += 1
            except (ValueError, TypeError):
                errores += 1
                continue
            conn.execute(
                "UPDATE media SET end_time = ? WHERE id = ?",
                (dt_end.isoformat(), mid),
            )

        conn.commit()
        print(f"    Puntos (fotos/textos): {updated_punto} actualizados.")
        print(f"    Segmentos (videos/audios): {updated_seg} actualizados.")
        if errores:
            print(f"    Errores de parseo (sin actualizar): {errores}")
        print(f"\n  Total actualizados: {updated_punto + updated_seg}")

    except sqlite3.OperationalError as e:
        print(f"  Error: {e}")
    finally:
        conn.close()


# ── Backup / Restore DB ──────────────────────────────────────────────────────

def listar_backups(db_path: str | None = None) -> list[tuple[str, str, int]]:
    """Lista archivos de backup en db/backups/ (y legacy db/ para compatibilidad).
    Returns:
        Lista de (ruta_completa, nombre_archivo, tamaño_bytes) ordenados por mtime descendente.
    """
    import re
    db_path = leer_db(db_path)
    db_dir = os.path.dirname(db_path)
    backup_dir = _directorio_backups(db_path)
    backups: list[tuple[str, str, int]] = []
    vistos: set[str] = set()
    # Directorio unificado primero
    for d in (backup_dir, db_dir):
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            # Acepta naming unificado y todos los legacy (flujos_*, flujos_backup_*, flujos_auto_*, 2026*...)
            if f.endswith(".db") and (
                f.startswith("flujos_") or f.startswith("flujos_backup_")
                or f.startswith("flujos_auto") or f.startswith("flujos_autobackup")
                or re.match(r"^\d{8}_.*\.db$", f)
            ):
                ruta = os.path.join(d, f)
                if ruta in vistos or not os.path.isfile(ruta):
                    continue
                vistos.add(ruta)
                try:
                    backups.append((ruta, f, os.path.getsize(ruta)))
                except Exception:
                    pass
    # Ordenar por mtime descendente (más reciente primero)
    try:
        backups.sort(key=lambda x: os.path.getmtime(x[0]), reverse=True)
    except Exception:
        backups.sort(key=lambda x: x[0], reverse=True)
    return backups


def opcion_backup_db(db_path: str | None = None):
    """Solo backup de la DB (sin reset)."""
    db_path = leer_db(db_path)
    if not os.path.isfile(db_path):
        print("  No hay base de datos para respaldar.")
        pausa()
        return

    conn = sqlite3.connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    except sqlite3.OperationalError:
        total = 0
    conn.close()

    # Mostrar backups existentes y retención
    backups_previos = listar_backups(db_path)
    print(f"\n  DB actual:    {db_path}")
    print(f"  Registros:    {total}")
    print(f"  Backups en {_directorio_backups(db_path)}: {len(backups_previos)} (máx 25 / 800 MB)")
    if backups_previos:
        total_mb = sum(s for _, _, s in backups_previos) / (1024 * 1024)
        print(f"  Tamaño total backups: {total_mb:.1f} MB")
    print()
    if not _preguntar_sn("Crear backup"):
        print("  Cancelado.")
        pausa()
        return

    ruta = _crear_backup_manual(db_path)
    if ruta:
        size_mb = os.path.getsize(ruta) / (1024 * 1024) if os.path.isfile(ruta) else 0
        print(f"  ✅ Backup creado: {os.path.basename(ruta)} ({size_mb:.1f} MB)")
        print(f"     Ubicación: {ruta}")
    else:
        print("  Backup no creado.")

    pausa()


def opcion_restore_db(db_path: str | None = None):
    """Restaura la DB desde un backup (WAL-safe)."""
    db_path = leer_db(db_path)

    backups = listar_backups(db_path)
    if not backups:
        print(f"  No hay backups disponibles en {_directorio_backups(db_path)} (ni legacy en {os.path.dirname(db_path)}).")
        pausa()
        return

    print(f"  Backups disponibles (en {_directorio_backups(db_path)} + legacy):\n")
    for i, (ruta, name, size) in enumerate(backups, 1):
        size_mb = size / (1024 * 1024)
        try:
            mtime = os.path.getmtime(ruta)
            from datetime import datetime
            fecha = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        except Exception:
            fecha = "?"
        print(f"  {i}) {name}  ({size_mb:.1f} MB)  {fecha}  → {ruta}")

    print("  0) Cancelar\n")

    try:
        sel = int(input("  ?Cual restaurar? (numero): ").strip())
    except ValueError:
        sel = 0

    if sel < 1 or sel > len(backups):
        print("  Cancelado.")
        pausa()
        return

    backup_path = backups[sel - 1][0]
    backup_name = backups[sel - 1][1]

    print(f"\n  Esto REEMPLAZARÁ la DB actual con: {backup_name}")
    print(f"  Origen: {backup_path}")
    print(f"  Destino: {db_path}")
    if not _preguntar_sn("Confirmar restauracion"):
        print("  Cancelado.")
        pausa()
        return

    import shutil
    try:
        # Backup de seguridad de la DB actual antes de pisar
        try:
            _auto_backup(db_path)
        except Exception:
            pass
        # Checkpoint WAL de la DB actual si existe
        if os.path.isfile(db_path):
            try:
                c = sqlite3.connect(db_path)
                c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                c.close()
            except Exception:
                pass
        # Restaurar con backup API (WAL-safe)
        ok = _backup_wal_safe(backup_path, db_path)
        if not ok:
            # Fallback ya hecho en _backup_wal_safe; verificar
            if not os.path.isfile(db_path):
                raise OSError("Fallo al restaurar backup")
        # Limpiar -wal/-shm huérfanos del destino (el backup API ya los maneja)
        for suf in ("-wal", "-shm"):
            p = db_path + suf
            if os.path.isfile(p):
                try:
                    # Si el backup no tenía WAL, el -wal viejo es basura
                    if not os.path.isfile(backup_path + suf):
                        os.remove(p)
                except Exception:
                    pass
        print(f"  ✅ DB restaurada desde: {backup_name}")
        # Verificar integridad rápida
        try:
            c = sqlite3.connect(db_path)
            c.execute("PRAGMA integrity_check").fetchone()
            c.close()
        except Exception:
            pass
    except Exception as e:
        print(f"  ❌ Error restaurando backup: {e}")

    pausa()


# ── Reset DB ─────────────────────────────────────────────────────────────────

def opcion_reset_db(db_path: str | None = None):
    """Hace backup de la DB actual y crea una nueva desde cero."""
    db_path = leer_db(db_path)

    if not os.path.isfile(db_path):
        print("  No hay base de datos que respaldar.")
        if not _preguntar_sn("Crear una DB vacia igual"):
            print("  Cancelado.")
            return
        print("  Creando DB vacia...")
        from scripts.ingest import init_db
        init_db(db_path)
        print(f"  DB creada: {db_path}")
        return

    # Contar registros
    conn = sqlite3.connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    except sqlite3.OperationalError:
        total = 0
    conn.close()

    print(f"\n  Base de datos actual: {db_path}")
    print(f"  Registros en media:   {total}")

    # Confirmar
    print()
    if not _preguntar_sn("Hacer backup y borrar"):
        print("  Cancelado.")
        return

    # Backup
    ruta = _crear_backup_manual(db_path)
    if ruta is None:
        if not _preguntar_sn("Continuar igual"):
            return

    # Borrar y crear nueva
    try:
        os.remove(db_path)
        print("  DB anterior eliminada.")
    except Exception as e:
        print(f"  Error eliminando DB: {e}")
        return

    from scripts.ingest import init_db
    init_db(db_path)
    print(f"  Nueva DB creada: {db_path}")
    print("  Lista para ingestar.")


def opcion_exportar_csv(db_path: str | None = None):
    """Exporta la DB completa a archivos CSV."""
    db_path = leer_db(db_path)
    if not os.path.isfile(db_path):
        print("  No hay base de datos para exportar.")
        pausa()
        return

    from scripts import exportar_csv

    # Mostrar resumen previo
    conn = sqlite3.connect(db_path)
    resumen = exportar_csv.obtener_resumen(conn)
    conn.close()

    print("\n=== EXPORTAR DB A CSV ===\n")
    print(f"  DB: {db_path}")
    print()
    for tabla in exportar_csv.TABLAS_VISIBLES:
        count = resumen.get(tabla, -1)
        if count >= 0:
            print(f"    {tabla:20s}  {count:>6d} registros")
        else:
            print(f"    {tabla:20s}  (no existe)")
    print()

    # Preguntar tablas
    print("  Opciones:")
    print("    1) Exportar todas las tablas")
    print("    2) Exportar solo media (tabla principal)")
    print("    3) Exportar solo media_metadata (tags, clima, etc.)")
    print("    4) Elegir tablas manualmente")
    print("    0) Cancelar\n")

    opc = input("  Opcion: ").strip()
    if opc == "0":
        print("  Cancelado.")
        pausa()
        return

    tablas = None
    if opc == "2":
        tablas = ["media"]
    elif opc == "3":
        tablas = ["media_metadata"]
    elif opc == "4":
        print("\n  Tablas disponibles:")
        for i, t in enumerate(exportar_csv.TABLAS_VISIBLES, 1):
            count = resumen.get(t, -1)
            estado = f"{count} registros" if count >= 0 else "(no existe)"
            print(f"    {i}) {t:20s}  {estado}")
        sel = input("\n  Numeros separados por coma (ej: 1,3,5): ").strip()
        if sel:
            indices = [int(x.strip()) for x in sel.split(",") if x.strip().isdigit()]
            tablas = [exportar_csv.TABLAS_VISIBLES[i - 1] for i in indices if 1 <= i <= len(exportar_csv.TABLAS_VISIBLES)]
        if not tablas:
            print("  Cancelado.")
            pausa()
            return

    # Preguntar directorio
    dir_default = "db/exports/"
    r = input(f"\n  Directorio de salida (Enter = {dir_default}{{timestamp}}/): ").strip()
    output_dir = r if r else None

    # Confirmar
    print()
    if tablas:
        print(f"  Tablas: {', '.join(tablas)}")
    else:
        print("  Tablas: todas")
    print(f"  Salida: {output_dir or dir_default}<timestamp>/")
    if not _preguntar_sn("Exportar"):
        print("  Cancelado.")
        pausa()
        return

    exportar_csv.exportar_todo(db_path, output_dir, tablas)
    pausa()


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) == 1 or (len(sys.argv) == 2 and sys.argv[1] in ("--tui", "--interactive")):
        tui()
        return

    if sys.argv[1] in ("--help", "--ayuda", "-h"):
        print(AYUDA)
        return

    comando = sys.argv[1]
    resto = sys.argv[2:]

    # Extraer --db de los args para comandos que no pasan resto a sub-scripts
    def _extract_db(args: list[str]) -> tuple[str | None, list[str]]:
        if "--db" in args:
            idx = args.index("--db")
            if idx + 1 < len(args):
                db_val = args[idx + 1]
                del args[idx + 1]
                del args[idx]
                return db_val, args
        return None, args

    if comando == "ingest":
        from scripts import ingest
        ingest.main(resto)

    elif comando == "query":
        from scripts import query
        query.main(resto)

    elif comando == "relocate":
        from scripts import relocate
        relocate.main(resto)

    elif comando == "check-db":
        db_val, _ = _extract_db(resto)
        opcion_check_db(db_val)

    elif comando == "check-gps":
        db_val, _ = _extract_db(resto)
        opcion_check_gps(db_val)

    elif comando in ("check-data", "check-clima", "check-geo"):
        from scripts import check_db_data
        check_db_data.main(resto)

    elif comando in ("undo-ingest", "undo"):
        db_val, _ = _extract_db(resto)
        opcion_undo_ingest(db_val)

    elif comando in ("backfill-end-time", "backfill"):
        db_val, _ = _extract_db(resto)
        opcion_backfill_end_time(db_val)

    elif comando == "improve-db":
        from scripts import improve_db
        improve_db.main(resto)

    elif comando in ("reset-db", "reset"):
        db_val, _ = _extract_db(resto)
        opcion_reset_db(db_val)

    elif comando in ("backup-db", "backup"):
        db_val, _ = _extract_db(resto)
        opcion_backup_db(db_val)

    elif comando in ("restore-db", "restore"):
        db_val, _ = _extract_db(resto)
        opcion_restore_db(db_val)

    elif comando == "geocode":
        from scripts import geocode
        geocode.main(resto)

    elif comando == "gradient":
        from scripts import gradiente
        gradiente.main(resto)

    elif comando == "astronomia":
        from scripts import astronomia
        astronomia.main(resto)

    elif comando == "mover":
        from scripts import mover_media
        mover_media.main(resto)

    elif comando in ("detectar-contenedores", "contenedores"):
        from scripts import detectar_contenedores
        detectar_contenedores.main(resto)

    elif comando in ("limpiar-descripciones", "descripciones"):
        from scripts import limpiar_descripciones
        limpiar_descripciones.main(resto)

    elif comando in ("repetir-contenido", "repetidos"):
        from scripts import repetir_contenido
        repetir_contenido.main(resto)

    elif comando in ("audio-frame", "crossref"):
        from scripts import audio_frame_crossref
        audio_frame_crossref.main(resto)

    elif comando in ("analizar-video", "analizar"):
        from scripts.ai_media import analyze_video
        analyze_video.main(resto)

    elif comando in ("keypoints-contexto", "keypoints"):
        from scripts import keypoints_contexto
        keypoints_contexto.main(resto)

    elif comando == "mapa":
        from scripts import mapa_ruta
        mapa_ruta.main(resto)

    elif comando in ("mapa-municipios", "mapas"):
        from scripts import mapas_municipio
        mapas_municipio.main(resto)

    elif comando in ("export-csv", "csv"):
        from scripts import exportar_csv
        exportar_csv.main(resto)

    elif comando in ("import-telegram", "tg"):
        from scripts import import_telegram
        import_telegram.main(resto)

    elif comando in ("ingest-textos", "textos"):
        from scripts import ingest_textos
        ingest_textos.main(resto)

    elif comando in ("ingest-gpx", "gpx"):
        from scripts import ingest_gpx
        ingest_gpx.main(resto)

    elif comando in ("corregir-360", "corregir360"):
        from scripts import corregir_timestamp_360
        corregir_timestamp_360.main(resto)

    elif comando in ("mapa-unificado", "unificado"):
        from scripts import mapa_unificado
        mapa_unificado.main(resto)

    else:
        print(f"Comando desconocido: {comando}")
        print(AYUDA)
        sys.exit(1)


if __name__ == "__main__":
    main()
