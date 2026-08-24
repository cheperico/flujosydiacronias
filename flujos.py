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

# Forzar UTF-8 en consola Windows para poder usar caracteres Unicode
if sys.platform == "win32":
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

  check-db    Mostrar todos los registros de la base de datos.

  check-gps   Revisar que archivos tienen GPS en el sistema de archivos.

  geocode     Geocodificar coordenadas GPS (lat,lon) a provincia/localidad
              usando la API Georef Argentina (batch).
              Ej: python flujos.py geocode --limit 100

  gradient    Calcular gradientes de ruta entre puntos GPS consecutivos.
              Calcula distancia, pendiente y esfuerzo fisico acumulado.
              Ej: python flujos.py gradient --dry-run --verbose

  astronomia  Calcular posicion del sol y clasificar twilight (NOAA).
              Calcula elevacion, azimut y momento del dia para cada
              registro con GPS + timestamp.
              Ej: python flujos.py astronomia --dry-run --verbose

  undo-ingest       Deshacer una ingesta por batch ID.

  backfill-end-time Calcular end_time para registros existentes
                    que no lo tengan (migracion).

  improve-db        Ejecutar pasos de mejora sobre la DB (colores,
                    keywords, transcripcion, keypoints, timestamps, GPS).

  analizar-video    Analizar videos con IA: scene detection + muestreo por
                    escena + keywords (minicpm-v4.6).
                    Ej: python flujos.py analizar-video --dry-run

  keypoints-contexto Keypoints de contexto (devenir geografico) contra los
                    tracks GPX: elevacion, astronomia, movimiento, ubicacion
                    y clima en media_keypoints.
                    Ej: python flujos.py keypoints-contexto --dry-run

  mapa              Generar un mapa HTML interactivo con Folium
                    a partir del track GPX y los GPS de la BD.
                    Ej: python flujos.py mapa --road-colors

  mapa-municipios   Genera un mapa HTML por municipio recorrido, con variantes
                    (ruta, puntos, contexto, gradiente). Nombre:
                    mapa_municipio_<municipio>_<variante>.html (sin acentos:
                    'Río Hondo' -> 'Rio_Hondo')
                    Ej: python flujos.py mapa-municipios --variantes ruta,puntos

  export-csv        Exporta todas las tablas de la DB a archivos CSV.
                    Ej: python flujos.py export-csv
                    Ej: python flujos.py export-csv --table media
                    Ej: python flujos.py export-csv --output ./mis_exports

  reset-db          Hace backup de la DB actual y crea una nueva
                     desde cero (schema limpio).

  backup-db         Solo backup (sin borrar): copia la DB actual con timestamp.

  restore-db        Restaura la DB desde un backup previo.

  import-telegram  Importar un export de Telegram (chats, mensajes, multimedia).
                   Ej: python flujos.py import-telegram -e RUTA_AL_EXPORT

  ingest-textos    Ingerir textos .md de la carpeta textos/ como medios type='text'.
                   Ej: python flujos.py ingest-textos

  mover            Mover o copiar archivos a nueva ubicacion y actualizar DB.
                   Ej: python flujos.py mover --new-root NUEVA_RAIZ --mode mover

  detectar-contenedores  Auditar contenedores de video/audio con ffprobe
                   (streams faltantes, estado por medio).
                   Ej: python flujos.py detectar-contenedores --dry-run

  limpiar-descripciones  Limpiar descripciones con eco del prompt (meta-intros).
                   Ej: python flujos.py limpiar-descripciones --dry-run

  repetir-contenido      Buscar contenido repetido por coincidencias de audio.
                   Ej: python flujos.py repetir-contenido --contra C:/audio.mp3

  audio-frame      Correlacionar contenido de audio con frames de video.
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
    """Devuelve un resumen con los totales de la DB."""
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
    destino = input("  ?Copiar media a carpeta canónica? (ej: D:/Medios) [Enter = no]: ").strip()

    print(f"\n  Resumen: export={export_path}  modo={modo_str}"
          f"  sistema={'SI' if include_system else 'NO'}"
          f"  ingest_media={'SI' if ingest_media else 'NO'}"
          f"  dry_run={'SI' if dry_run else 'NO'}"
          f"  destino={destino or '(no copiar)'}")
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
    if destino:
        args.extend(["--destino", destino])
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
    """Submenu: listar distintos aspectos de la DB."""
    from scripts import query
    db_flag = ["--db", db_path] if db_path else []

    while True:
        limpiar_pantalla()
        print("=== LISTAR ===\n")
        print("  1) Tipos de medio")
        print("  2) Autores")
        print("  3) Carpetas")
        print("  4) Colores basicos")
        print("  5) Provincias (geocode)")
        print("  6) Buscar texto")
        print("  7) Consulta libre (flags directos a query.py)")
        print("  8) Revisar GPS en archivos")
        print("  9) Detalle completo de registros (todas las columnas)")
        print("  0) Volver\n")

        opc = input("  Opcion: ").strip()
        if opc == "1":
            query.main(["--distinct", "type", "--count"] + db_flag)
        elif opc == "2":
            query.main(["--distinct", "author", "--count"] + db_flag)
        elif opc == "3":
            query.main(["--distinct", "carpeta", "--count"] + db_flag)
        elif opc == "4":
            query.main(["--distinct", "color_1_name_basic", "--count", "--where",
                        "color_1_name_basic IS NOT NULL"] + db_flag)
        elif opc == "5":
            query.main(["--distinct", "provincia", "--count", "--where",
                        "provincia IS NOT NULL"] + db_flag)
        elif opc == "6":
            texto = input("  Texto a buscar: ").strip()
            if texto:
                query.main(["--search", texto] + db_flag)
        elif opc == "7":
            flags = input("  Flags (ej: --distinct type --count): ").strip()
            if flags:
                import shlex
                query.main(shlex.split(flags))
        elif opc == "8":
            opcion_check_gps(db_path)
        elif opc == "9":
            opcion_detalle_db(db_path)
        elif opc == "0":
            break
        else:
            print("  Opcion invalida.")
        if opc not in ("9", "8", "0"):
            pausa()


def opcion_consultar(db_path: str | None = None):
    """Menu: Consultar base de datos."""
    _menu("CONSULTAR BASE DE DATOS", {
        "1": ("Ver resumen de la DB", opcion_check_db),
        "2": ("Listar...", opcion_listar),
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

    new_root = input("  Nueva raiz: ").strip()
    if not new_root:
        print("  Cancelado.")
        pausa()
        return

    if not os.path.isdir(new_root):
        r = input(f"  La carpeta '{new_root}' no existe. ?Continuar de todos modos? (s/N): ").strip().lower()
        if r != "s":
            print("  Cancelado.")
            pausa()
            return

    dry_run = _preguntar_sn("Solo previsualizar (dry-run)")

    from scripts import relocate
    relocate.main(["--new-root", new_root] + (["--dry-run"] if dry_run else []))

    pausa()


def opcion_check_db(db_path: str | None = None):
    limpiar_pantalla()
    print("=== INSPECCION DE BASE DE DATOS ===\n")
    db_path = leer_db(db_path)
    if not os.path.isfile(db_path):
        print("  No se encuentra la base de datos.")
        pausa()
        return

    conn = sqlite3.connect(db_path)
    try:
        print(resumen_db(conn))
    except sqlite3.OperationalError as e:
        print(f"  Error: {e}")
    conn.close()

    print("\n  Ultimos registros:")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT id, filename_original, type, author, timestamp_utc FROM media ORDER BY id DESC LIMIT 5"
        )
        for row in cursor:
            print(f"  #{row[0]:>6d} [{row[2]:6s}] {row[1]} - {row[3] or '?'}")
        conn.close()
    except sqlite3.OperationalError as e:
        print(f"  Error: {e}")

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
    limpiar_pantalla()
    print("=== REVISAR GPS EN ARCHIVOS ===\n")
    db_path = leer_db(db_path)
    if not os.path.isfile(db_path):
        print("  No se encuentra la base de datos.")
        pausa()
        return

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT filepath_absoluto FROM media WHERE type='image' AND latitude IS NULL ORDER BY RANDOM() LIMIT 5"
        )
        sin_gps = cursor.fetchall()
        if sin_gps:
            print("  Muestras de imagenes sin GPS en DB (5 al azar):")
            print()
            for (fp,) in sin_gps:
                print(f"    {fp}")
        else:
            print("  No hay imagenes sin GPS en la DB.")
    except sqlite3.OperationalError as e:
        print(f"  Error: {e}")
    conn.close()

    print()
    print("  Para un analisis completo, usa: python flujos.py check-gps --db ruta")
    pausa()


def opcion_detalle_db(db_path: str | None = None):
    """Muestra todas las columnas de los ultimos registros."""
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

        # Pedir cantidad
        try:
            n = int(input("  Cantidad de registros a mostrar (default 10): ").strip() or "10")
        except ValueError:
            n = 10

        cursor = conn.execute(
            f"SELECT * FROM media ORDER BY id DESC LIMIT {n}"
        )
        rows = cursor.fetchall()

        if not rows:
            print("  No hay registros.")
            conn.close()
            pausa()
            return

        for row in rows:
            print(f"  ── #{row['id']} ──")
            for col in cols:
                val = row[col]
                if val is not None:
                    val_str = str(val)
                    if len(val_str) > 60:
                        val_str = val_str[:57] + "..."
                    print(f"    {col:<25s} {val_str}")
            print()

        print(f"  {len(rows)} registros mostrados.")

    except sqlite3.OperationalError as e:
        print(f"  Error: {e}")
    finally:
        conn.close()

    pausa()


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

            confirm = input(f"  Esto borrara TODOS los medios del batch #{bid}. Confirmar? (s/N): ").strip().lower()
            if confirm != "s":
                print("  Cancelado.")
                conn.close()
                pausa()
                return

            deleted = conn.execute("DELETE FROM media WHERE ingest_batch_id = ?", (bid,)).rowcount
            conn.commit()
            print(f"  Eliminados {deleted} medios del batch #{bid}.")

        elif codigo[0] == "t":
            # Deshacer track GPX
            try:
                tid = int(codigo[1:])
            except ValueError:
                print("  Codigo invalido.")
                conn.close()
                pausa()
                return

            # Verificar que existe
            track = conn.execute(
                "SELECT id, name FROM tracks WHERE id = ?", (tid,)
            ).fetchone()
            if not track:
                print(f"  Track #{tid} no encontrado.")
                conn.close()
                pausa()
                return

            track_nombre = track[1]
            confirm = input(f"  Esto borrara el track \"{track_nombre}\" y sus waypoints. Confirmar? (s/N): ").strip().lower()
            if confirm != "s":
                print("  Cancelado.")
                conn.close()
                pausa()
                return

            # Revertir altitud de medios que obtuvieron altitud de este track
            # (marcamos como NULL los que tengan geolocation_source='track_gps')
            revertidos = conn.execute(
                "UPDATE media SET altitude = NULL, geolocation_source = NULL "
                "WHERE geolocation_source = 'track_gps'"
            ).rowcount

            # Borrar track (CASCADE borra waypoints automaticamente)
            conn.execute("DELETE FROM tracks WHERE id = ?", (tid,))
            conn.commit()
            print(f"  Track \"{track_nombre}\" eliminado.")
            if revertidos:
                print(f"  Altitud revertida para {revertidos} medios (geolocation_source='track_gps').")

        else:
            print("  Codigo invalido. Use b<num> para medios o t<num> para tracks.")
            conn.close()
            pausa()
            return

    except (sqlite3.OperationalError, ValueError) as e:
        print(f"  Error: {e}")
    finally:
        conn.close()
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


_PASOS_IA = {"keywords", "descriptions"}


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


def _auto_backup(db_path: str) -> str | None:
    """Crea un backup automático con timestamp. Retorna la ruta del backup o None."""
    import shutil
    from datetime import datetime
    backup_dir = os.path.join(os.path.dirname(__file__), "db", "backups")
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir, f"flujos_autobackup_{ts}.db")
    try:
        shutil.copy2(db_path, backup_path)
        return backup_path
    except Exception as e:
        print(f"  ⚠ Error al crear backup automático: {e}")
        return None


def _crear_backup_manual(db_path: str) -> str | None:
    """Crea backup con timestamp junto a la DB. Retorna la ruta o None."""
    import shutil
    from datetime import datetime
    db_dir = os.path.dirname(db_path)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"flujos_backup_{ts}.db"
    backup_path = os.path.join(db_dir, backup_name)
    try:
        shutil.copy2(db_path, backup_path)
        return backup_path
    except Exception as e:
        print(f"  Error creando backup: {e}")
        return None


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
    """Menu para ejecutar pasos de mejora sobre la DB (hojas paginadas).
    Regla de navegacion: hasta 9 opciones por hoja (1-9); cuando se superan,
    se crea una hoja nueva. n = Siguiente >>, p = << Anterior, 0 = Volver.
    En hojas con Anterior y Siguiente, p se lista primero, luego n.
    Distribucion: Hoja 1 (IA y color, 9 opc: audio 7-9 con Audio tagging
    primero, seguido de Transcripcion y Keypoints), Hoja 2 (etiquetado +
    inferencia y ubicacion, 9 opc: Keywords desde textos y transcripciones 1 y Refinar
    keywords 2 = fin de la seccion de etiquetado, antes de timestamps/GPS),
    Hoja 3 (Analisis de video, 2 opc: Analizar video con escenas + IA y
    Keypoints de contexto).
    El menu tiene 3 hojas (embeddings retirado del TUI: rediseno pendiente)."""
    db_path = leer_db(db_path)

    def _opcion_pasos_manuales(db):
        pasos = input("  Pasos (separados por coma, ej: colors,keywords): ").strip()
        if pasos:
            modo = _preguntar_modo(db)
            if modo is None:
                print("  Cancelado.")
                pausa()
                return
            _ejecutar_improve_db(pasos=pasos, modo=modo)
        pausa()

    _menu_paginado("MEJORAR BASE DE DATOS", [
        ("  -- Pasos de IA y color --\n", {
            "1": ("Todos los pasos (skip)", lambda db: _ejecutar_paso_mejora(None, db)),
            "2": ("Elegir pasos manualmente", _opcion_pasos_manuales),
            "3": ("Colores dominantes", lambda db: _ejecutar_paso_mejora("colors", db)),
            "4": ("Keywords con IA", lambda db: _ejecutar_paso_mejora("keywords", db)),
            "5": ("Descripcion con IA", lambda db: _ejecutar_paso_mejora("descriptions", db)),
            "6": ("Keywords + Descripcion (pasada unica, mas lenta)", lambda db: _ejecutar_paso_mejora("keywords,descriptions", db)),
            "7": ("Audio tagging (sonidos ambientales)", opcion_audio_tagging),
            "8": ("Transcripcion (audios/videos)", lambda db: _ejecutar_paso_mejora("transcribe", db)),
            "9": ("Keypoints de transcripciones", lambda db: _ejecutar_paso_mejora("keypoints", db)),
        }),
        ("  -- Etiquetado + inferencia y ubicacion --\n", {
            "1": ("Keywords desde textos y transcripciones", opcion_keywords_transcripciones),
            "2": ("Refinar keywords (normalizar + sinonimos)", opcion_refinar_keywords),
            "3": ("Inferir timestamps", lambda db: _ejecutar_paso_mejora("timestamps", db)),
            "4": ("Inferir GPS", lambda db: _ejecutar_paso_mejora("gps", db)),
            "5": ("Calcular gradientes de ruta", lambda db: opcion_gradient()),
            "6": ("Localizacion (provincia, municipio, localidad)", lambda db: opcion_geocode()),
            "7": ("Condiciones climaticas", lambda db: opcion_weather()),
            "8": ("Dia de la semana", lambda db: opcion_dia_semana()),
            "9": ("Posicion del sol (astronomia)", lambda db: opcion_astronomia()),
        }),
        ("  -- Analisis de video --\n", {
            "1": ("Analizar video (escenas + IA)", opcion_analizar_video),
            "2": ("Keypoints de contexto (devenir geografico)", opcion_keypoints_contexto),
        }),
    ], db_path)


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


def opcion_mantenimiento(db_path: str | None = None):
    """Menu: mantenimiento general de la DB (backup, restore, exportar, etc)."""
    _menu_paginado("MANTENIMIENTO DB", [
        ("  -- Mantenimiento general --\n", {
            "1": ("Relocalizar medios (cambio de raiz)", opcion_relocalizar),
            "2": ("Calcular posición del sol (astronomía)", opcion_astronomia),
            "3": ("Backfill end_time", opcion_backfill_end_time),
            "4": ("Backup DB (solo backup, sin borrar)", opcion_backup_db),
            "5": ("Restore DB desde backup", opcion_restore_db),
            "6": ("Resetear DB (backup + limpiar)", opcion_reset_db),
            "7": ("Exportar DB a CSV", opcion_exportar_csv),
            "8": ("Mover/Copiar medios", opcion_mover_media),
            "9": ("Auditar contenedores (streams faltantes)", opcion_auditar_contenedores),
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
        print("  Ejecuta pasos de mejora sobre la base de datos.")
        print("  Uso: python flujos.py improve-db [--steps X,Y] [--mode skip|update|replace]\n")
        print("  Pasos disponibles:")
        print("    colors        Extraer colores dominantes")
        print("    keywords      Etiquetar con IA")
        print("    descriptions  Describir con IA")
        print("    transcribe    Transcribir audios/videos")
        print("    keypoints     Poblar keypoints desde transcripciones")
        print("    timestamps    Inferir timestamps faltantes")
        print("    gps           Inferir GPS")
        print()
        print("  --list  para listar todos los pasos.")
        pausa()

    def _ayuda_geocode(_db):
        limpiar_pantalla()
        print("============ GEOCODE ============\n")
        print("  Geocodifica coordenadas GPS (lat,lon) a provincia/localidad")
        print("  usando la API Georef Argentina (batch).\n")
        print("  Uso: python flujos.py geocode [--limit N] [--dry-run]\n")
        print("  Tambien desde consola:")
        print("    python scripts/geocode.py --coords -34.6037,-58.3816")
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
        print("  Uso: python flujos.py gradient [--dry-run] [--verbose]\n")
        print("  Tambien desde consola:")
        print("    python scripts/gradiente.py --dry-run --verbose")
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
        print("  Uso: python flujos.py astronomia [--dry-run] [--verbose]\n")
        print("  Requiere: latitude, longitude y timestamp_utc en la DB.")
        print("  Algoritmo: NOAA Solar Calculator (Python puro, 0 dependencias)\n")
        print("  Precision: ~0.01°\n")
        pausa()

    def _ayuda_check(_db):
        limpiar_pantalla()
        print("============ CHECK-DB ============\n")
        print("  Inspecciona todos los registros de la base de datos.")
        print("  Uso: python flujos.py check-db\n")
        print("============ CHECK-GPS ============\n")
        print("  Revisa que archivos tienen GPS en el sistema de archivos.")
        print("  Uso: python flujos.py check-gps\n")
        print("  Para un analisis completo: python scripts/check_gps.py")
        pausa()

    _menu("AYUDA", {
        "1": ("Ayuda general", _ayuda_general),
        "2": ("ingest  - Ingestion de medios", _ayuda_ingest),
        "3": ("query   - Consultas a la base de datos", _ayuda_query),
        "4": ("relocate - Relocalizar medios", _ayuda_relocate),
        "5": ("improve-db - Mejorar base de datos", _ayuda_improve_db),
        "6": ("geocode - Geocodificar coordenadas GPS", _ayuda_geocode),
        "7": ("gradient - Calcular gradientes de ruta", _ayuda_gradient),
        "8": ("astronomia - Posición del sol y twilight", _ayuda_astronomia),
        "9": ("check-db / check-gps", _ayuda_check),
    }, intro="  Elija un comando para ver su ayuda detallada:", titulo_ancho=12)


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
        "9": ("Ayuda", lambda db: opcion_ayuda()),
    }, db_path=leer_db(), pre_titulo=_cabecera, etiqueta_salir="Salir", on_salir=_chau)


# ── Visualizaciones ───────────────────────────────────────────────────────────

def opcion_visualizaciones(db_path: str | None = None):
    """Menu: visualizaciones de la ruta y los datos (mapas, deploy web, TD...)."""
    _menu("VISUALIZACIONES", {
        "1": ("Mapas", opcion_mapas),
        "2": ("Exportar visualización web (deploy)", opcion_exportar_visualizacion),
        "3": ("TouchDesigner (puente OSC)", opcion_touchdesigner),
    }, db_path)


def opcion_mapas(db_path: str | None = None):
    """Menu: mapas de la ruta y los municipios (Folium)."""
    _menu("MAPAS", {
        "1": ("Mapa de ruta (Folium)", lambda db: opcion_mapa()),
        "2": ("Mapas por municipio (Folium)", opcion_mapas_municipio),
    }, db_path)


def opcion_exportar_visualizacion(db_path: str | None = None):
    """Menu: exportar el snapshot web (visualizacion.db) y el spec del loop.

    Paso 1: exportar_visualizacion.py (snapshot SQLite de flujos.db; deploy
    genérico por defecto a deploy/, con copia de medios y transcode opcional).
    Paso 2: loop_db.py --salida pruebas/spec.json (spec del motor de loop portable).
    """
    base = os.path.dirname(__file__)
    exportador = os.path.join(base, "scripts", "exportar_visualizacion.py")
    loop_db = os.path.join(base, "scripts", "ai_media", "loop_db.py")

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

    def _spec_loop(db):
        # Forzar UTF-8 en Windows por los caracteres de caja del log.
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        subprocess.run([sys.executable, loop_db,
                        "--horas", "7", "16", "13", "18",
                        "--salida", os.path.join(base, "pruebas", "spec.json")],
                       env=env)
        pausa()

    def _deploy_dry(db):
        subprocess.run([sys.executable, exportador, "--dry-run"])
        pausa()

    _menu("EXPORTAR VISUALIZACION WEB (deploy)", {
        "1": ("Deploy a deploy/ (pregunta si transcodificar)", _deploy_default),
        "2": ("Deploy a otra carpeta (pregunta si transcodificar)", _deploy_custom),
        "3": ("Re-exportar snapshot local (deploy/db, sin copiar medios)", _snapshot),
        "4": ("Regenerar spec del loop (pruebas/spec.json)", _spec_loop),
        "5": ("Previsualizar deploy (dry-run)", _deploy_dry),
    }, db_path, intro=(
        "Exporta un snapshot de flujos.db para una visualizacion web\n"
        "  (ver docs/deploy.md). El deploy (default: deploy/ en la raiz del\n"
        "  proyecto) copia los medios y pregunta si transcodificar videos\n"
        "  grandes/360° a MP4/H.264 web (default: sí). --snapshot-local es\n"
        "  el modo dev local (sin copiar medios)."
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
    print("  (sin acentos: 'Río Hondo' -> 'Rio_Hondo')\n")
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
    """Lista archivos de backup en el directorio de la DB.
    Returns:
        Lista de (ruta_completa, nombre_archivo, tamaño_bytes) ordenados por fecha descendente.
    """
    db_path = leer_db(db_path)
    db_dir = os.path.dirname(db_path)
    backups = []
    for f in os.listdir(db_dir):
        if f.startswith("flujos_backup_") and f.endswith(".db"):
            ruta = os.path.join(db_dir, f)
            backups.append((ruta, f, os.path.getsize(ruta)))
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
    total = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    conn.close()

    print(f"\n  DB actual:    {db_path}")
    print(f"  Registros:    {total}")
    print()
    if not _preguntar_sn("Crear backup"):
        print("  Cancelado.")
        pausa()
        return

    ruta = _crear_backup_manual(db_path)
    if ruta:
        print(f"  ✅ Backup creado: {os.path.basename(ruta)}")
    else:
        print("  Backup no creado.")

    pausa()


def opcion_restore_db(db_path: str | None = None):
    """Restaura la DB desde un backup."""
    db_path = leer_db(db_path)
    db_dir = os.path.dirname(db_path)

    backups = listar_backups(db_path)
    if not backups:
        print("  No hay backups disponibles en el directorio de la DB.")
        pausa()
        return

    print("  Backups disponibles:\n")
    for i, (ruta, name, size) in enumerate(backups, 1):
        size_mb = size / (1024 * 1024)
        print(f"  {i}) {name}  ({size_mb:.1f} MB)")

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
    if not _preguntar_sn("Confirmar restauracion"):
        print("  Cancelado.")
        pausa()
        return

    import shutil
    try:
        # Cerrar cualquier conexión (no podemos forzarlo, pero asumimos que no hay)
        shutil.copy2(backup_path, db_path)
        print(f"  ✅ DB restaurada desde: {backup_name}")
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

    else:
        print(f"Comando desconocido: {comando}")
        print(AYUDA)
        sys.exit(1)


if __name__ == "__main__":
    main()
