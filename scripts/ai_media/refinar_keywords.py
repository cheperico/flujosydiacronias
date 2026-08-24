#!/usr/bin/env python3
"""
refinar_keywords.py — Refina y unifica las keywords generadas por IA.

Toma los valores de `media_metadata.ia_keywords` ya generados y los limpia
en 2 capas:

  1. LÉXICA (siempre, sin IA)
     - minúsculas, limpieza de caracteres raros
     - plural → singular (bicicletas → bicicleta)
     - quitar artículos / ruido ("la", "el", "un", "una")
     - descartar keywords inválidas (len < 3, valores tipo prompt, etc.)

  2. DICCIONARIO (siempre, determinístico)
     - Sinónimos explícitos del dominio (bici → bicicleta, moto → motocicleta)
     - El canónico es el término más "estándar" del grupo

(Nota: la capa SEMÁNTICA con embeddings fue ELIMINADA en Ago 2026. Los
embeddings de paraphrase-multilingual generaban falsos sinónimos que
degradaban términos específicos del dominio, por ejemplo ciclismo→deporte,
nublado→soleado, parche→parque. La traducción con translategemma ya produce
keywords limpias y consistentes, así que la unificación por embeddings no
aportaba y podía meter errores.)

Después de refinar, reescribe `media_metadata.ia_keywords` con los valores
canónicos, deduplicados. (El género fotográfico se eliminó: las keywords son
libres y NO se fuerza ningún género comodín, por lo que "otras"/"otro" ya no
se insertan.)

Con `--clave ia_keywords_transcripcion` refina la otra familia de keywords
(generadas desde transcripciones de audio/video en keywords_transcripciones.py).

Uso:
    python scripts/ai_media/refinar_keywords.py                 # léxico + diccionario
    python scripts/ai_media/refinar_keywords.py --dry-run       # previsualizar sin escribir
    python scripts/ai_media/refinar_keywords.py --mode update   # reprocesa todos
    python scripts/ai_media/refinar_keywords.py --clave ia_keywords_transcripcion

Modos (igual que el resto del pipeline):
    skip    → solo registros con ia_keywords ya presentes (default)
    update  → reprocesa todos los registros con ia_keywords
    replace → limpia y regenera (equivalente a update para este script)
"""

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
from collections import Counter

log = logging.getLogger(__name__)

# Permitir ejecución standalone: agregar raíz del proyecto al path
# (el TUI lo ejecuta como script suelto, donde 'scripts' no es un paquete
# importable; sin esto `from scripts.ai_media.checkpoint import ...` falla con
# ModuleNotFoundError). Mismo patrón que los demás scripts de ai_media/.
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(
        0,
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    )

# ── Diccionario de sinónimos del dominio (bici, viaje, Argentina) ───────────
# Clave = canónico, valor = lista de variantes que se unifican al canónico.
# Incluye variantes EN del pipeline IA (los modelos de visión responden en
# inglés; la traducción a ES puede dejar alguna palabra sin traducir, y este
# diccionario la recupera en el refinamiento).
SINONIMOS: dict[str, list[str]] = {
    "bicicleta": ["bici", "bicicletas", "bike", "bicycle", "bicycles", "bici de montaña", "mtb", "mountain bike", "cycling"],
    "motocicleta": ["moto", "motocicletas", "moto de enduro", "motomel", "motorcycle"],
    "automóvil": ["auto", "autos", "coche", "coches", "camioneta", "camionetas", "vehículo", "vehiculo", "car", "vehicle"],
    "ruta": ["carretera", "ruta nacional", "ruta 9", "road"],
    "autopista": ["autovía", "autovia", "doble carril", "highway", "freeway", "motorway"],
    "montaña": ["montañas", "cerro", "cerros", "sierra", "sierras", "cordillera", "cordilleras", "mountain", "mountains"],
    "atardecer": ["puesta de sol", "ocaso", "anochecer", "atardeceres", "sunset"],
    "amanecer": ["salida del sol", "alba", "aurora", "amaneceres", "sunrise"],
    "ciudad": ["ciudades", "pueblo", "pueblos", "zona urbana", "city", "town"],
    "naturaleza": ["campo", "paisaje natural", "nature", "outdoor"],
    "bosque": ["bosques", "selva", "arboleda", "forest"],
    "árbol": ["arboles", "arbol", "árboles", "plantas", "tree", "branch", "rama", "ramas", "branches"],
    # "caballo"/"caballos" retirados de animales (Ago 2026): específicos y
    # con sentido propio, quedan como tags independientes.
    "animales": ["animal", "vaca", "vacas", "perro", "perros",
                 "gato", "gatos", "oveja", "ovejas", "burro", "burros", "ganado", "animals"],
    "comida": ["gastronomía", "comidas", "plato", "platos", "almuerzo", "cena", "desayuno", "asado", "food"],
    "personas": ["persona", "hombres", "mujeres",
                 "caminante", "caminantes", "viajero", "viajeros", "baqueano", "people"],
    "deporte": ["deportes", "competición", "carrera", "sport"],
    "ciclismo": ["ciclista", "ciclistas", "cycling", "cyclist", "cyclists", "pedaleando"],
    # "aventura" retirada de viaje (Ago 2026): significativa por separado.
    "viaje": ["trayecto", "recorrido", "ruta viajera", "trip", "adventure", "road trip", "viaje en ruta"],
    "fotografía": ["foto", "fotos", "imagen", "imágenes", "retrato fotográfico", "photography"],
    # "urbanismo" ELIMINADO (Ago 2026): sus variantes eran semánticamente
    # erradas (urbanismo = planificación urbana, no edificios). Los términos
    # de construcción pasan a la familia de "arquitectura"; "urbanismo"
    # queda como tag libre válido.
    # Familia de arquitectura ampliada (plan_keywords §3.2): absorbe los
    # términos de edificios del viejo "urbanismo" + la familia adjetival.
    "arquitectura": ["edificaciones", "fachada", "fachadas", "architecture",
                     "edificio", "edificios", "rascacielos", "buildings",
                     "arquitectónico", "arquitectonico"],
    "noche": ["nocturna", "nocturno", "noche estrellada", "night"],
    "cielo": ["cielos", "nubes", "cielo azul", "horizonte", "sky", "clouds", "cloud"],
    "lluvia": ["lluvioso", "tormenta", "tormentas", "llovizna", "rain"],
    "frío": ["frio", "helada", "escarcha", "cold"],
    "calor": ["sol intenso", "sequía", "caluroso", "heat"],
    "música": ["musica", "banda", "recital", "show", "concierto", "tocar", "music"],
    "arte": ["pintura", "mural", "murales", "graffiti", "art"],
    "abuela": ["abuelita", "anciana", "abuelo", "anciano"],
    "niño": ["niños", "nene", "nenes", "chico", "chicos", "pequeño", "kids"],
    "amigo": ["amigos", "compañero", "compañeros", "compañera", "friends"],
    "felicidad": ["alegría", "sonrisa", "sonrisas", "risa", "risas", "smile"],
    "cansancio": ["fatiga", "agotamiento", "tired"],
    # "equipaje" retirado de mochila (Ago 2026): significativo por separado.
    "mochila": ["mochilas", "alforjas", "alforja", "bolso", "bolsos", "backpack", "pannier"],
    "carpas": ["carpa", "campamento", "acampar", "campaña", "camping", "camp"],
    "comida_argentina": ["empanadas", "asado", "milanesa", "locro", "mate", "dulce de leche"],
    "mate": ["yerba", "termo"],
    "casco": ["helmet", "cascos"],
    "reparación": ["repair", "reparar", "fix", "mantenimiento"],
    "tela": ["cloth", "fabric", "telas", "tela roja"],
    "equipamiento": ["gear", "equipamento"],
    "viento": ["wind"],
    "camino_de_tierra": ["gravel", "ripio", "grava"],
    "banquina": ["roadside"],
    "senderismo": ["trail", "sendero", "hiking"],
    "esfuerzo": ["effort", "esfuerzos"],
    "paisaje": ["landscape", "scenery", "vista"],
    # Familias reales detectadas en los datos (plan_keywords §3.3, Ago 2026):
    # colapsan variantes descriptivas hacia el sustantivo base.
    "abandono": ["abandonado"],
    "aislamiento": ["aislado"],
    "agricultura": ["agrícola"],
    "agua": ["agua fangosa", "agua turbia", "agua potable"],
    "acera": ["acera colorida"],
    "urbano": ["ambiente urbano", "arquitectura urbana"],
    "apoyo": ["apoyo mutuo", "apoyo social", "apoyo grupal",
              "ayuda", "ayuda mutua", "asistencia"],
    # Variantes de nombres propios leídos de la escena (visión) — un mismo lugar
    # aparece con varias formas según cómo lo leyó minicpm y lo tradujo
    # translategemma (análisis de frecuencia-1, Ago 2026).
    "bell ville": ["villa bell"],
    "monte buey": ["monte bué", "monte bley", "cerro boyero"],
    "melincué": ["melincue", "laguna de melincüe"],
    "chacabuco": ["chacabú"],
    "james craik": ["james craig"],
    "ruta sanmartiniana": ["ruta santamarianense"],
    "bottasso": ["botta"],
    # Lugares de 3+ palabras (plan_keywords §3.1): canónicos con variantes
    # vacías SOLO para que _es_frase_basura no los descarte como regurgitación.
    "carmen de areco": [],
    "san andrés de giles": [],
    "san josé de la dormida": [],   # localidad real (Dpto. Ischilín, Córdoba)
    "mar del plata": [],
    # Provincia mal transcripta por gemma: la variante con error se corrige
    # hacia el nombre correcto.
    "santiago del estero": ["santiago de lestero"],
    # Nombres propios que NO se deben singularizar ni descartar como basura.
    "bicivilizados": ["bicivilizado"],                 # nombre del programa de radio (feed 435)
    "dardo s. dorronzoro": ["dardo", "s. dorronzoro"],  # monumento en Luján
    "cadáver": ["cadaver"],                            # tag sensible, unificado (evita deriva ortográfica)
}

# Palabras tan genéricas que no aportan (se descartan si no son género)
STOPWORDS = {
    "la", "el", "los", "las", "un", "una", "unos", "unas", "de", "del", "y",
    "en", "con", "por", "para", "que", "es", "se", "su", "al", "lo", "a",
    "the", "and", "of", "to", "in", "imagen", "foto", "fotografía", "fotografía",
    "este", "esta", "eso", "esa", "una escena", "escena", "otro", "otra",
    "image", "photo", "scene", "outdoor", "object", "objects", "color", "colors",
    "person", "people", "colours",
    # Artefactos de gemma y genéricos sueltos (plan_keywords §3.4-§3.5):
    # "agobo" y "amágimador" son palabras imposibles generadas por el modelo;
    # "actividad" sola es demasiado genérica.
    "actividad", "agobo", "amágimador",
}

# Patrones de ruido del modelo (cuando regurgitó el prompt en vez de keywords)
PATRONES_BASURA = [
    r"g[eé]nero fotogr[aá]fico",
    r"elige una",
    r"deben describir",
    r"no incluyas",
    r"ejemplo",
    r"ning[uú]n otro texto",
    r"separa",
    r"palabra clave",
    r"this is",
    r"spanish",
    r"primera palabra",
    r"^\.\.\.$",
    r"no inventes",
    r"separ[aá]las",
    r"no incluesas",
    r"^sa_\d+$",           # nombres de archivo Sony (sa_20001)
    r"^dsc\d+",            # nombres de archivo DSC
    r"^\d+x\d+$",          # resoluciones
    r"^\d+\s*[a-z]",
]

def normalizar_palabra(palabra: str) -> str:
    """Capa léxica: limpia una keyword individual."""
    p = palabra.strip().lower().strip("'\",.;:!?¿¡()[]").strip()
    # Quitar cualquier resto de prompt entre paréntesis
    if "(" in p:
        p = p[:p.index("(")].strip()
    if "..." in p:
        p = p.split("...")[0].strip()
    # Quitar artículos iniciales ("la montaña" → "montaña")
    p = re.sub(r"^(la|el|los|las|un|una|unos|unas)\s+", "", p)
    # Quitar comillas restantes
    p = p.strip("'\",.;:")
    return p


def _es_frase_basura(palabra: str) -> bool:
    """
    Detecta frases de 3+ palabras que no son sinónimos conocidos del dominio.

    El modelo a veces regurgita frases completas como keyword
    (ej: "pájaro de ánus morrison", "del tiempo no de de la").
    Se excluyen las variantes multi-palabra de SINONIMOS
    (ej: "bici de montaña" es válida y se mapea a "bicicleta") y también los
    CANÓNICOS (un canónico es por definición una keyword legítima del dominio,
    ej: "dardo s. dorronzoro").
    """
    if palabra.count(" ") < 2:  # 2 palabras o menos: no aplica
        return False
    p = palabra.lower()
    for canonico, variantes in SINONIMOS.items():
        if p == canonico.lower():
            return False
        if p in [v.lower() for v in variantes]:
            return False
    return True


def es_basura(palabra: str) -> bool:
    """Detecta si una keyword es ruido (regurgitación del prompt, etc.)."""
    p = palabra.lower()
    for pat in PATRONES_BASURA:
        if re.search(pat, p):
            return True
    if len(p) < 3:
        return True
    if p in STOPWORDS:
        return True
    if _es_frase_basura(p):
        return True
    return False


def singularizar(palabra: str) -> str:
    """Plural → singular simple (montañas → montaña, perros → perro, autos → auto).

    CONSERVADOR:
    - NO toca palabras terminadas en "-es" (árboles, flores, viajes,
      atardeceres) porque la regla no es segura sin morfología — los casos
      comunes del dominio ya están cubiertos por SINONIMOS (se aplica antes).
    - NO toca palabras terminadas en vocal ACENTUADA + "s" (-ás, -és, -ís):
      ahí la "s" NO marca plural y recortarla destroza la palabra
      (bug corregido Ago 2026: estrés→estr, interés→interé, país→paí).
    """
    if not palabra or len(palabra) < 4:
        return palabra
    # -s tras vocal simple SIN acento: montañas→montaña, perros→perro.
    # Las vocales acentuadas quedan fuera: vocal acentuada + s nunca es plural.
    if (palabra.endswith("s") and len(palabra) > 3
            and palabra[-2] in "aeiou"
            and not palabra.endswith("es")):
        return palabra[:-1]
    return palabra


def aplicar_sinonimos(palabra: str) -> str:
    """Capa diccionario: devuelve el canónico si la palabra es una variante."""
    p = palabra.lower()
    for canonico, variantes in SINONIMOS.items():
        if canonico and p == canonico.lower():
            return canonico
        if p in [v.lower() for v in variantes if v]:
            return canonico
    return palabra


def refinar_lista_keywords(keywords: list[str]) -> list[str]:
    """
    Aplica capas léxica + diccionario a una lista de keywords.
    Las keywords son libres: NO se fuerza ningún género comodín (se eliminó el
    concepto de género fotográfico y con él la inserción de "otras").

    Args:
        keywords: Lista de keywords extraídas por el modelo.

    Returns:
        Lista de keywords refinadas y deduplicadas.
    """
    if not keywords:
        return []

    # Normalizar todas
    norm = [normalizar_palabra(k) for k in keywords]
    # Filtrar basura
    norm = [n for n in norm if n and not es_basura(n)]

    # Singularizar y aplicar sinónimos
    norm = [singularizar(r) for r in norm]
    norm = [aplicar_sinonimos(r) for r in norm]
    norm = [r for r in norm if r and not es_basura(r)]

    # Quitar duplicados preservando orden
    vistos = set()
    uniq = []
    for r in norm:
        if r not in vistos:
            vistos.add(r)
            uniq.append(r)

    # Máx 7 keywords (el prompt pide 5, dejar margen)
    return uniq[:7]


def obtener_keywords_db(conn: sqlite3.Connection, clave: str = "ia_keywords") -> dict[int, list[str]]:
    """Lee todas las keywords de la clave dada en la DB. Devuelve {media_id: [keywords]}."""
    filas = conn.execute(
        "SELECT media_id, value FROM media_metadata WHERE key = ?", (clave,)
    ).fetchall()
    resultado: dict[int, list[str]] = {}
    for mid, valor in filas:
        if not valor:
            continue
        # El valor puede ser string separado por comas o JSON array
        try:
            lista = json.loads(valor)
            if isinstance(lista, list):
                resultado[mid] = [str(x) for x in lista]
                continue
        except (json.JSONDecodeError, TypeError):
            pass
        # Formato string: "a, b, c"
        partes = [p.strip().strip("'\"").rstrip(".,;") for p in valor.split(",") if p.strip()]
        resultado[mid] = partes
    return resultado


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Refina y unifica keywords de IA (léxico + diccionario)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", default=None, help="Ruta a la base de datos (default: db/flujos.db)")
    parser.add_argument("--mode", default="skip", choices=["skip", "update", "replace"],
                        help="skip: solo los que tienen keywords (default) | update: todos | replace: igual que update")
    parser.add_argument("--clave", default="ia_keywords",
                        help="Clave de media_metadata a refinar (default: ia_keywords). "
                             "Ej: ia_keywords_transcripcion")
    parser.add_argument("--dry-run", action="store_true", help="Previsualizar cambios sin escribir")
    parser.add_argument("--verbose", action="store_true", help="Log detallado")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Resolver DB
    db_path = args.db
    if db_path is None:
        default_db = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "db", "flujos.db",
        )
        if os.path.isfile(default_db):
            db_path = default_db
        else:
            print("  No se encontró db/flujos.db. Especificá --db.")
            sys.exit(1)

    if not os.path.isfile(db_path):
        print(f"  ERROR: No existe la DB: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Envolver el trabajo real con manejo de interrupción: al cortar con
    # Ctrl+C se commitean los pendientes y se sale con mensaje claro
    # (manejar_interrupcion), sin traceback.
    from scripts.ai_media.checkpoint import manejar_interrupcion
    with manejar_interrupcion(conn=conn, etiqueta="refinar_keywords"):
        _ejecutar(conn, args)


def _ejecutar(conn, args) -> None:
    """
    Ejecuta el refinamiento completo: léxico + diccionario + escritura.

    Separado de main() para poder envolverlo en manejar_interrupcion sin
    re-indentar el cuerpo (mismo nivel de indentación de función).
    """
    # Leer keywords
    datos = obtener_keywords_db(conn, args.clave)
    if not datos:
        print(f"  No hay '{args.clave}' en la DB. Nada que refinar.")
        conn.close()
        return

    log.info("  Registros con %s: %d", args.clave, len(datos))

    # --- Paso 1: extraer todas las keywords únicas y sus frecuencias ---
    todas = []
    for lista in datos.values():
        todas.extend(lista)
    counter = Counter(t.lower() for t in todas)
    log.info("  Keywords totales (con duplicados): %d | únicas: %d",
             len(todas), len(counter))

    # --- Paso 2: aplicar capa léxica + diccionario a nivel de cada registro ---
    refinadas: dict[int, list[str]] = {}
    for mid, lista in datos.items():
        refinadas[mid] = refinar_lista_keywords(lista)

    # --- Paso 3: comparar y mostrar cambios ---
    cambios = 0
    sin_cambios = 0
    for mid in list(refinadas.keys()):
        if refinadas[mid] == datos.get(mid, []):
            sin_cambios += 1
        else:
            cambios += 1

    log.info("  Registros con cambios: %d | sin cambios: %d", cambios, sin_cambios)

    if args.dry_run:
        print("\n  [DRY-RUN] Cambios propuestos (máx 10):")
        mostrados = 0
        for mid, nueva in refinadas.items():
            if nueva != datos.get(mid, []):
                print(f"\n  media {mid}:")
                print(f"    ANTES: {datos.get(mid, [])}")
                print(f"    DESPUÉS: {nueva}")
                mostrados += 1
                if mostrados >= 10:
                    break
        print(f"\n  Total con cambios: {cambios}")
        conn.close()
        return

    # --- Paso 4: escribir en DB ---
    if not args.dry_run and cambios:
        # Checkpoint por lote: commit cada 20 registros en vez de uno solo
        # al final (si se corta con Ctrl+C, el progreso queda guardado y se
        # retoma con --mode update).
        from scripts.ai_media.checkpoint import Checkpoint
        cp = Checkpoint(conn, cada=20, etiqueta="refinar_keywords")
        try:
            for mid, nueva in refinadas.items():
                valor_nuevo = ", ".join(nueva)
                conn.execute(
                    "UPDATE media_metadata SET value = ? WHERE media_id = ? AND key = ?",
                    (valor_nuevo, mid, args.clave),
                )
                cp.contar()
            cp.finalizar()
        except Exception as e:
            log.error("  Error escribiendo en DB: %s", e)
            conn.close()
            sys.exit(1)

        log.info("  ✅ Keywords refinadas: %d registros actualizados", cambios)

        # Mostrar ejemplo de un registro con cambios
        for mid, nueva in refinadas.items():
            if nueva != datos.get(mid, []):
                print(f"\n  Ejemplo media {mid}:")
                print(f"    ANTES: {datos.get(mid, [])}")
                print(f"    DESPUÉS: {nueva}")
                break

    elif not cambios:
        print("  Nada que cambiar (ya estaban refinadas).")

    conn.close()


if __name__ == "__main__":
    main()
