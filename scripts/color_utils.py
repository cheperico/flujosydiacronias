"""
color_utils.py — Utilidades para extracción y naming de colores.

Dependencias: Pillow, webcolors
"""

import webcolors

# -----------------------------------------------------------------------
# Mapa CSS3 {hex: nombre}: compatible con webcolors >= 24 (API nueva)
# y con versiones anteriores (que exponían CSS3_HEX_TO_NAMES directo).
# En webcolors 25.x la constante dict fue removida; ahora se usa
# webcolors.names(spec=webcolors.CSS3) + name_to_hex().
# -----------------------------------------------------------------------

def _construir_mapa_css3() -> dict[str, str]:
    """Devuelve {hex_normalizado: nombre} para los 147 colores CSS3."""
    try:
        # API nueva (>= 24): construir el dict desde names + name_to_hex
        if hasattr(webcolors, "names") and hasattr(webcolors, "name_to_hex"):
            return {
                webcolors.name_to_hex(nombre, spec=webcolors.CSS3): nombre
                for nombre in webcolors.names(spec=webcolors.CSS3)
            }
    except Exception:
        pass
    # API vieja: dict directo
    if hasattr(webcolors, "CSS3_HEX_TO_NAMES"):
        return dict(webcolors.CSS3_HEX_TO_NAMES)
    # Último recurso: paleta mínima hardcodeada (no debería ocurrir)
    return {
        "#000000": "black", "#ffffff": "white", "#ff0000": "red",
        "#00ff00": "green", "#0000ff": "blue", "#ffff00": "yellow",
        "#ffa500": "orange", "#800080": "purple", "#a52a2a": "brown",
        "#808080": "gray",
    }


CSS3_HEX_TO_NAMES = _construir_mapa_css3()

# -----------------------------------------------------------------------
# Mapping: nombre CSS inglés → español
# -----------------------------------------------------------------------

CSS_COLORS_ES = {
    # Rojos
    "indianred": "rojo indio",
    "lightcoral": "coral claro",
    "salmon": "salmón",
    "darksalmon": "salmón oscuro",
    "lightsalmon": "salmón claro",
    "crimson": "carmesí",
    "red": "rojo",
    "firebrick": "rojo ladrillo",
    "darkred": "rojo oscuro",
    # Rosas
    "pink": "rosa",
    "lightpink": "rosa claro",
    "hotpink": "rosa intenso",
    "deeppink": "rosa profundo",
    "mediumvioletred": "rojo violeta medio",
    "palevioletred": "rojo violeta pálido",
    # Naranjas
    "coral": "coral",
    "tomato": "tomate",
    "orangered": "rojo anaranjado",
    "darkorange": "naranja oscuro",
    "orange": "naranja",
    # Amarillos
    "gold": "dorado",
    "yellow": "amarillo",
    "lightyellow": "amarillo claro",
    "lemonchiffon": "chiffon limón",
    "lightgoldenrodyellow": "amarillo dorado claro",
    "papayawhip": "papaya",
    "moccasin": "mocasín",
    "peachpuff": "melocotón",
    "palegoldenrod": "vara de oro pálida",
    "khaki": "caqui",
    "darkkhaki": "caqui oscuro",
    # Morados / Violetas
    "lavender": "lavanda",
    "thistle": "cardo",
    "plum": "ciruela",
    "violet": "violeta",
    "orchid": "orquídea",
    "magenta": "magenta",
    "mediumorchid": "orquídea medio",
    "mediumpurple": "púrpura medio",
    "blueviolet": "violeta azulado",
    "darkviolet": "violeta oscuro",
    "darkorchid": "orquídea oscura",
    "darkmagenta": "magenta oscuro",
    "purple": "púrpura",
    "indigo": "añil",
    "slateblue": "azul pizarra",
    "darkslateblue": "azul pizarra oscuro",
    "mediumslateblue": "azul pizarra medio",
    # Verdes
    "greenyellow": "verde amarillento",
    "chartreuse": "chartreuse",
    "lawngreen": "verde césped",
    "lime": "lima",
    "limegreen": "verde lima",
    "palegreen": "verde pálido",
    "lightgreen": "verde claro",
    "mediumspringgreen": "verde primavera medio",
    "springgreen": "verde primavera",
    "mediumseagreen": "verde mar medio",
    "seagreen": "verde mar",
    "forestgreen": "verde bosque",
    "green": "verde",
    "darkgreen": "verde oscuro",
    "yellowgreen": "verde amarillento",
    "olivedrab": "oliva apagado",
    "olive": "oliva",
    "darkolivegreen": "verde oliva oscuro",
    "mediumaquamarine": "aguamarina medio",
    "darkseagreen": "verde mar oscuro",
    "lightseagreen": "verde mar claro",
    "darkcyan": "cian oscuro",
    "teal": "verde azulado",
    # Azules
    "aqua": "aguamarina",
    "cyan": "cian",
    "lightcyan": "cian claro",
    "paleturquoise": "turquesa pálido",
    "aquamarine": "aguamarina",
    "turquoise": "turquesa",
    "mediumturquoise": "turquesa medio",
    "darkturquoise": "turquesa oscuro",
    "cadetblue": "azul cadete",
    "steelblue": "azul acero",
    "lightsteelblue": "azul acero claro",
    "powderblue": "azul polvo",
    "lightblue": "azul claro",
    "skyblue": "azul cielo",
    "lightskyblue": "azul cielo claro",
    "deepskyblue": "azul cielo profundo",
    "dodgerblue": "azul dodger",
    "cornflowerblue": "azul aciano",
    "mediumslateblue": "azul pizarra medio",
    "royalblue": "azul real",
    "blue": "azul",
    "mediumblue": "azul medio",
    "darkblue": "azul oscuro",
    "navy": "azul marino",
    "midnightblue": "azul medianoche",
    # Marrones
    "cornsilk": "seda de maíz",
    "blanchedalmond": "almendra blanqueada",
    "bisque": "bisque",
    "navajowhite": "blanco navajo",
    "wheat": "trigo",
    "burlywood": "madera",
    "tan": "bronceado",
    "rosybrown": "marrón rosáceo",
    "sandybrown": "marrón arena",
    "goldenrod": "vara de oro",
    "darkgoldenrod": "vara de oro oscuro",
    "peru": "perú",
    "chocolate": "chocolate",
    "saddlebrown": "marrón montura",
    "sienna": "siena",
    "brown": "marrón",
    "maroon": "granate",
    # Blancos / Cremas
    "snow": "nieve",
    "honeydew": "rocío de miel",
    "mintcream": "crema de menta",
    "azure": "azur",
    "aliceblue": "azul alicia",
    "ghostwhite": "blanco fantasma",
    "whitesmoke": "humo blanco",
    "seashell": "concha marina",
    "beige": "beige",
    "oldlace": "encaje viejo",
    "floralwhite": "blanco floral",
    "ivory": "marfil",
    "antiquewhite": "blanco antiguo",
    "linen": "lino",
    "lavenderblush": "rubor lavanda",
    "mistyrose": "rosa brumoso",
    # Grises
    "gainsboro": "gainsboro",
    "lightgray": "gris claro",
    "lightgrey": "gris claro",
    "silver": "plata",
    "darkgray": "gris oscuro",
    "darkgrey": "gris oscuro",
    "gray": "gris",
    "grey": "gris",
    "dimgray": "gris tenue",
    "dimgrey": "gris tenue",
    "lightslategray": "gris pizarra claro",
    "lightslategrey": "gris pizarra claro",
    "slategray": "gris pizarra",
    "slategrey": "gris pizarra",
    "darkslategray": "gris pizarra oscuro",
    "darkslategrey": "gris pizarra oscuro",
    # Blancos
    "white": "blanco",
    # Negros
    "black": "negro",
}

# -----------------------------------------------------------------------
# Mapping: color CSS → color básico (11 categorías)
# -----------------------------------------------------------------------

BASIC_COLORS = {
    "rojo": ["indianred", "lightcoral", "salmon", "darksalmon", "lightsalmon",
             "crimson", "red", "firebrick", "darkred",
             "coral", "tomato", "orangered"],
    "naranja": ["darkorange", "orange"],
    "amarillo": ["gold", "yellow", "lightyellow", "lemonchiffon",
                 "lightgoldenrodyellow", "papayawhip", "moccasin",
                 "peachpuff", "palegoldenrod", "khaki", "darkkhaki",
                 "greenyellow", "chartreuse", "lawngreen", "yellowgreen",
                 "cornsilk", "blanchedalmond", "bisque", "navajowhite",
                 "wheat", "burlywood", "tan", "goldenrod", "darkgoldenrod"],
    "verde": ["lime", "limegreen", "palegreen", "lightgreen",
              "mediumspringgreen", "springgreen",
              "mediumseagreen", "seagreen", "forestgreen", "green", "darkgreen",
              "mediumaquamarine", "darkseagreen", "lightseagreen",
              "darkcyan", "teal", "aqua", "cyan", "lightcyan",
              "paleturquoise", "aquamarine", "turquoise", "mediumturquoise",
              "darkturquoise",
              "olivedrab", "olive", "darkolivegreen"],
    "azul": ["cadetblue", "steelblue", "lightsteelblue", "powderblue",
             "lightblue", "skyblue", "lightskyblue", "deepskyblue",
             "dodgerblue", "cornflowerblue", "royalblue", "blue",
             "mediumblue", "darkblue", "navy", "midnightblue",
             "azure", "aliceblue", "ghostwhite"],
    "violeta": ["lavender", "thistle", "plum", "violet", "orchid",
                 "mediumorchid", "mediumpurple", "blueviolet", "darkviolet",
                 "darkorchid", "darkmagenta", "purple", "indigo", "fuchsia",
                 "slateblue", "darkslateblue", "mediumslateblue",
                 "magenta", "lavenderblush"],
    "rosa": ["pink", "lightpink", "hotpink", "deeppink",
             "mediumvioletred", "palevioletred",
             "mistyrose", "rosybrown"],
    "marrón": ["sandybrown", "peru", "chocolate", "saddlebrown",
               "sienna", "brown", "maroon"],
    "blanco": ["snow", "honeydew", "mintcream", "whitesmoke", "seashell",
               "beige", "oldlace", "floralwhite", "ivory", "antiquewhite",
               "linen", "white"],
    "gris": ["gainsboro", "lightgray", "lightgrey",
             "silver", "darkgray", "darkgrey",
             "gray", "grey",
             "dimgray", "dimgrey",
             "lightslategray", "lightslategrey",
             "slategray", "slategrey",
             "darkslategray", "darkslategrey"],
    "negro": ["black"],
}

# Invertir: de nombre CSS a básico
_CSS_TO_BASIC = {}
for basic_name, css_names in BASIC_COLORS.items():
    for css_name in css_names:
        _CSS_TO_BASIC[css_name] = basic_name

# Categorías "no-color": colores que queremos evitar si hay una alternativa real
_CATEGORIAS_NO_COLOR = {"gris", "negro", "blanco"}

UMBRAL_SATURACION_NEUTRO = 0.15  # por debajo: el color se trata como neutro (gris/blanco/negro)

# -----------------------------------------------------------------------
# Funciones principales
# -----------------------------------------------------------------------

def hex_to_rgb(hex_color: str) -> tuple:
    """Convierte hex a RGB. Acepta #RRGGBB o RRGGBB."""
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def rgb_to_hex(r: int, g: int, b: int) -> str:
    """Convierte RGB a hex."""
    return f"#{r:02x}{g:02x}{b:02x}"


def _redmean(r1: int, g1: int, b1: int, r2: int, g2: int, b2: int) -> float:
    """
    Distancia de color Redmean (una aproximación perceptual simple).
    Pesa cada canal según la sensibilidad del ojo humano:
      - Verde: máximo peso (el ojo es más sensible al verde)
      - Rojo: peso medio, ajustado por luminosidad
      - Azul: mínimo peso (especialmente en colores oscuros)

    Fórmula: sqrt((2+r̄/256)*Δr² + 4*Δg² + (2+(255-r̄)/256)*Δb²)
    donde r̄ = (r1+r2)/2
    """
    r_avg = (r1 + r2) / 2
    dr = r1 - r2
    dg = g1 - g2
    db = b1 - b2
    return (
        (2 + r_avg / 256) * dr * dr +
        4 * dg * dg +
        (2 + (255 - r_avg) / 256) * db * db
    ) ** 0.5


def closest_css_color(hex_color: str) -> str:
    """
    Encuentra el nombre CSS más cercano para un color hex dado.
    Usa distancia Redmean, que aproxima mejor la percepción humana
    que la distancia euclidiana RGB simple.

    Además, aplica un sesgo anti-gris: si el mejor match cae en una
    categoría "no-color" (gris, negro, blanco) pero hay un color real
    dentro de 1.3× de distancia, prefiere el color real. Esto evita
    que marrones oscuros o verdes desaturados caigan en "gris" por
    diferencias mínimas en RGB.
    """
    r, g, b = hex_to_rgb(hex_color)
    min_dist = float("inf")
    closest = None

    for css_hex, css_name in CSS3_HEX_TO_NAMES.items():
        cr, cg, cb = hex_to_rgb(css_hex)
        dist = _redmean(r, g, b, cr, cg, cb)
        if dist < min_dist:
            min_dist = dist
            closest = css_name

    if not closest:
        return "gray"

    # Sesgo anti-gris: si el closest es "no-color", buscar el mejor "color real"
    # que esté dentro de 1.5× de la distancia mínima
    cat_closest = _CSS_TO_BASIC.get(closest, "gris")
    if cat_closest in _CATEGORIAS_NO_COLOR:
        # Guard defensivo: si la entrada ya es casi neutra, no promocionar
        # a un color real; devolver el gris/negro/blanco más cercano tal cual.
        if _saturacion_hsv(r, g, b) < UMBRAL_SATURACION_NEUTRO:
            return closest
        mejor_color = None
        mejor_dist = float("inf")
        for css_hex, css_name in CSS3_HEX_TO_NAMES.items():
            cat = _CSS_TO_BASIC.get(css_name, "gris")
            if cat in _CATEGORIAS_NO_COLOR:
                continue
            cr, cg, cb = hex_to_rgb(css_hex)
            dist = _redmean(r, g, b, cr, cg, cb)
            if dist < mejor_dist:
                mejor_dist = dist
                mejor_color = css_name
        # Si hay un color real dentro de 1.3× de la distancia al gris, usarlo
        if mejor_color and mejor_dist < min_dist * 1.5:
            return mejor_color

    return closest


def _nombre_neutro(r: int, g: int, b: int) -> tuple[str, str]:
    """Nombre para colores casi neutros (saturacion < umbral): negro/blanco/gris por luminancia."""
    if max(r, g, b) < 45:
        return "negro", "negro"
    if min(r, g, b) > 200:
        return "blanco", "blanco"
    # gris: el CSS gris mas cercano por redmean
    mejor: str | None = None
    mejor_dist = float("inf")
    for css_hex, css_name in CSS3_HEX_TO_NAMES.items():
        if _CSS_TO_BASIC.get(css_name, "gris") != "gris":
            continue
        cr, cg, cb = hex_to_rgb(css_hex)
        d = _redmean(r, g, b, cr, cg, cb)
        if d < mejor_dist:
            mejor_dist, mejor = d, css_name
    css_es = CSS_COLORS_ES.get(mejor, mejor or "gris")
    return css_es, "gris"


def get_color_names(hex_color: str) -> tuple:
    """
    Devuelve (nombre_css_es, nombre_basico) para un color hex.

    Si la saturación HSV del color está por debajo de UMBRAL_SATURACION_NEUTRO,
    se trata como neutro (negro/blanco/gris según luminancia) y NO se hace el
    matching CSS: evita que píxeles casi grises de fotos B&N caigan en
    categorías "coloreadas" (violeta, verde, azul, rosa) por el sesgo anti-gris.
    """
    r, g, b = hex_to_rgb(hex_color)
    if _saturacion_hsv(r, g, b) < UMBRAL_SATURACION_NEUTRO:
        return _nombre_neutro(r, g, b)
    css_name_en = closest_css_color(hex_color)
    css_name_es = CSS_COLORS_ES.get(css_name_en, css_name_en)
    basic_name = _CSS_TO_BASIC.get(css_name_en, "gris")
    return css_name_es, basic_name


def _saturacion_hsv(r: int, g: int, b: int) -> float:
    """Calcula saturación HSV (0-1) a partir de RGB."""
    r_n, g_n, b_n = r / 255.0, g / 255.0, b / 255.0
    mx = max(r_n, g_n, b_n)
    mn = min(r_n, g_n, b_n)
    if mx == 0:
        return 0.0
    return (mx - mn) / mx


def _es_gris_o_negro(r: int, g: int, b: int, umbral_saturacion: float = 0.08) -> bool:
    """
    True si el color es casi gris (muy baja saturación) o casi negro.
    Útil para filtrar colores poco interesantes.
    """
    if max(r, g, b) < 30:  # negro / casi negro
        return True
    if _saturacion_hsv(r, g, b) < umbral_saturacion:
        return True
    return False


def _calcular_grilla(w: int, h: int, celdas_objetivo: int = 16) -> tuple[int, int]:
    """
    Calcula una grilla de celdas adaptativa según el aspect ratio.
    Ej: 4:3 → 4x4, 16:9 → 5x3, etc.
    """
    ratio = w / h
    cols = max(2, int(round((celdas_objetivo * ratio) ** 0.5)))
    rows = max(2, int(round(celdas_objetivo / cols)))
    return cols, rows


def _peso_central(col: int, row: int, cols: int, rows: int) -> float:
    """
    Peso de centralidad para una celda de la grilla.
    Las celdas del centro pesan 1.0, las de las esquinas bajan a ~0.3.
    Útil para dar más importancia a objetos ubicados en el centro de la imagen.
    """
    cx = (cols - 1) / 2
    cy = (rows - 1) / 2
    dx = (col - cx) / max(1.0, cx)
    dy = (row - cy) / max(1.0, cy)
    dist = (dx * dx + dy * dy) ** 0.5  # distancia euclídea normalizada
    return max(0.3, 1.0 - dist * 0.7)


def extract_dominant_colors(image_path: str, n_colors: int = 3) -> list:
    """
    Extrae los N colores más representativos de una imagen.

    Estrategia (grilla + concentración + centralidad + sat. relativa):
      1. Divide la imagen en una grilla de ~16 celdas.
      2. Cada celda se pondera por cercanía al centro (peso_central).
      3. En cada celda, cuantiza y obtiene colores con frecuencias.
      4. Para cada color único del conjunto global:
         - Calcula frecuencia (con peso central) y cantidad de celdas.
         - Score = freq * (total_celdas / n_celdas)^2 * peso_sat
         - peso_sat usa saturación RELATIVA: si hay al menos un color
           vibrante en la imagen, los apagados pierden peso drásticamente.
         - La concentración al cuadrado premia objetos pequeños pero
           localizados (cartel, bicicleta, bandera).
         - La centralidad da más peso a objetos en el centro de la toma.
      5. Filtra grises/negros extremos.
      6. Devuelve los N mejor puntuados en hex.

    Sin dependencias externas: solo Pillow.
    """
    try:
        from PIL import Image

        # ── 1. Abrir y normalizar ──
        img = Image.open(image_path)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        elif img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg

        # Redimensionar a tamaño de trabajo
        w, h = img.size
        img.thumbnail((300, 300), Image.LANCZOS)
        w, h = img.size

        # ── 2. Dividir en grilla ──
        cols, rows = _calcular_grilla(w, h)
        celda_w = max(1, w // cols)
        celda_h = max(1, h // rows)

        # {paleta_idx: {"freq": total, "celdas": set, "r": r, "g": g, "b": b}}
        acum = {}

        for row in range(rows):
            for col in range(cols):
                left = col * celda_w
                upper = row * celda_h
                right = min(left + celda_w, w)
                lower = min(upper + celda_h, h)
                tile = img.crop((left, upper, right, lower))

                # Peso de centralidad: lo que está al centro pesa más
                peso_central = _peso_central(col, row, cols, rows)

                # Cuantizar la celda a pocos colores
                q = tile.quantize(colors=12, method=Image.MEDIANCUT)
                counts = q.getcolors()
                if not counts:
                    continue
                pal = q.getpalette()

                for freq, idx in counts:
                    r_c = pal[idx * 3]
                    g_c = pal[idx * 3 + 1]
                    b_c = pal[idx * 3 + 2]
                    hex_c = rgb_to_hex(r_c, g_c, b_c)
                    if hex_c not in acum:
                        acum[hex_c] = {"freq": 0, "celdas": set(), "r": r_c, "g": g_c, "b": b_c}
                    # La frecuencia se pondera por cercanía al centro
                    acum[hex_c]["freq"] += freq * peso_central
                    acum[hex_c]["celdas"].add((row, col))

        if not acum:
            return ["#808080"] * n_colors

        # ── 3. Puntuar cada color ──
        # Score = freq * concentracion^2 * peso_sat
        #
        #   freq:          cantidad de píxeles de ese color (con peso central)
        #   concentracion: (total_celdas / celdas_que_ocupa)^2
        #                  colores localizados reciben boost cuadrático
        #                  (un color en 4 celdas recibe boost 16x)
        #   peso_sat:      peso por saturación, usando saturación RELATIVA:
        #                  si en la imagen hay algún color vibrante (sat alta),
        #                  los colores apagados pierden peso drásticamente.
        #                  si todo es apagado, todos se nivelan.
        #
        total_celdas = rows * cols

        # Encontrar la saturación máxima de la imagen
        sat_max = 0.0
        for hex_c, data in acum.items():
            sat = _saturacion_hsv(data["r"], data["g"], data["b"])
            if sat > sat_max:
                sat_max = sat
        # Evitar división por cero en imágenes completamente grises
        if sat_max < 0.1:
            sat_max = 0.1

        scored = []
        for hex_c, data in acum.items():
            r, g, b = data["r"], data["g"], data["b"]
            n_celdas = len(data["celdas"])
            sat = _saturacion_hsv(r, g, b)
            # Saturación RELATIVA: qué tan saturado es respecto al más saturado
            sat_rel = sat / sat_max
            # Peso agresivo: si hay un color muy saturado en la imagen,
            # los colores apagados quedan muy abajo
            peso_sat = 0.05 + 0.95 * sat_rel
            concentracion = (total_celdas / max(1, n_celdas)) ** 2
            score = data["freq"] * concentracion * peso_sat
            scored.append((score, r, g, b))

        scored.sort(key=lambda x: x[0], reverse=True)

        # ── 4. Seleccionar, filtrando grises/negros ──
        colors = []
        for score, r, g, b in scored:
            if len(colors) >= n_colors:
                break
            if _es_gris_o_negro(r, g, b) and len(colors) > 0:
                if any(not _es_gris_o_negro(*hex_to_rgb(c)) for c in colors):
                    continue
            colors.append(rgb_to_hex(r, g, b))

        # Rellenar si faltan
        if len(colors) < n_colors:
            for score, r, g, b in scored:
                if len(colors) >= n_colors:
                    break
                hex_c = rgb_to_hex(r, g, b)
                if hex_c not in colors:
                    colors.append(hex_c)

        while len(colors) < n_colors:
            colors.append("#808080")

        return colors

    except Exception as e:
        return ["#808080"] * n_colors
