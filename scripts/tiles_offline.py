#!/usr/bin/env python3
"""
tiles_offline.py — Mapas Folium autocontenidos para TouchDesigner (cero red).

Los mapas por municipio que renderiza TouchDesigner (Web Render TOP sobre
`file://`) descargaban de internet, en runtime, sus tiles de Esri (antes CartoDB)
Y los assets JS/CSS de Folium (Leaflet, jQuery, Bootstrap, FontAwesome...) de
varios CDNs. El Web Render TOP usa CEF con un proceso de navegador por TOP y una
cache temporal que se borra al salir → cada mapa re-descargaba todo desde cero,
y la página quedaba en blanco hasta resolver los assets (cuello de botella con
varios mapas abiertos a la vez).

Este módulo hace cada HTML 100% autocontenido en dos planos:
  1. TILES de la vista inicial → data URIs base64 (ver abajo).
  2. ASSETS JS/CSS de Folium + fuentes de íconos → incrustados inline en el HTML
     (vía `folium` `embedded=True`), eliminando cualquier request a CDN.

Nada de esto requiere servidor: los data URIs e inline son parte del documento
HTML y funcionan en `file://` (Chromium de TD), sin CORS ni procesos extra.

Tiles (UNA sola capa, sin doble descarga):
  El mapa se crea SIN capa base (folium.Map(tiles=None)) y `incrustar_tiles_vista_inicial`
  inyecta un L.TileLayer custom que, para los tiles de la vista inicial,
  devuelve el data URI incrustado y, para los demás, delega en la URL online de
  Esri Light Gray. Al ser la única capa, la zona de la vista inicial NO se
  descarga dos veces (el mapa base de Folium no existe). Si la descarga falla o
  `zooms` es vacío, se inyecta la capa igualmente (dict vacío) y el mapa
  funciona 100% online.

  NOTA 2026-08-26: CartoDB dejó de servir sus tiles públicos sin API key (overlay
  "API KEY REQUIRED", ver home-assistant/frontend#53800). Se reemplazó por Esri
  World Light Gray (Canvas), estilo claro equivalente, SIN key. URL:
  https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}

Zooms por defecto: 11-13 (aprobado por el usuario). Si el zoom inicial que
elegiría `fitBounds` cae fuera de ese rango (municipios muy chicos/grandes), se
expande automáticamente a `[z_est-1, z_est, z_est+1]` para cubrir la vista.

Assets autocontenidos:
  `guardar_autocontenido(mapa, ruta)` descarga (una vez, cache en `assets_cache/`,
  regenerable) los JS/CSS que Folium referenciaría por CDN, los apunta a rutas
  locales file:// y guarda el HTML con `render(embedded=True)` (folium los
  incrusta inline). `inline_fuentes` además incrusta las fuentes de los íconos
  de marcadores (FontAwesome solid + glyphicons) como data URIs. Si la descarga
  de assets falla (sin internet al generar), se guarda con el método normal
  (CDN) como fallback.
"""

import base64
import json
import logging
import math
import os
import re
import urllib.request

log = logging.getLogger("tiles_offline")

# URL de tiles Esri World Light Gray (Canvas). Misma que usa mapa_ruta.py para
# el tile default. Sin subdominios ({s}); orden {z}/{y}/{x} (diferente de Carto).
TILE_URL_ESRI = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/"
    "World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}"
)
# Deprecated: CartoDB positron (requiere API key desde 2026-08-26).
# TILE_URL_CARTO = "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png"
ZOOMS_DEFAULT = [11, 12, 13]
# Cache de tiles versionado por proveedor (Esri ≠ Carto en estilo).
CACHE_DIR_DEFAULT = "tiles_cache/esri"
CACHE_DIR_ASSETS_DEFAULT = "assets_cache"
# Zoom mínimo/máximo absolutos para el recorte del rango embebido.
ZOOM_MIN_ABS = 4
ZOOM_MAX_ABS = 18
# Tamaño nominal del contenedor del mapa (px) para estimar el zoom de fitBounds.
CONTENEDOR_PX = (1200, 700)
TIMEOUT_S = 10


# ---------------------------------------------------------------------------
# Matemática de tiles (Web Mercator / slippy map)
# ---------------------------------------------------------------------------

def _proyectar_y(lat: float) -> float:
    """Proyecta latitud a la coordenada Y de Web Mercator (0..1)."""
    lat_rad = math.radians(lat)
    return (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0


def tiles_en_bounds(
    lat0: float, lon0: float, lat1: float, lon1: float, zooms: list[int]
) -> list[tuple[int, int, int]]:
    """Lista de tiles (z, x, y) que cubren el bounds [lat0,lon0]-[lat1,lon1].

    lat0/lat1 y lon0/lon1 son el bounds en WGS84 (sin normalizar el orden:
    la función acepta esquinas en cualquier orden).
    """
    lat_min, lat_max = min(lat0, lat1), max(lat0, lat1)
    lon_min, lon_max = min(lon0, lon1), max(lon0, lon1)
    tiles: list[tuple[int, int, int]] = []
    for z in zooms:
        n = 2 ** z
        x_min = math.floor((lon_min + 180.0) / 360.0 * n)
        x_max = math.ceil((lon_max + 180.0) / 360.0 * n)
        y_min = math.floor(_proyectar_y(lat_max) * n)
        y_max = math.ceil(_proyectar_y(lat_min) * n)
        for x in range(x_min, x_max + 1):
            for y in range(y_min, y_max + 1):
                if 0 <= x < n and 0 <= y < n:
                    tiles.append((z, x, y))
    return tiles


def zoom_fit_bounds(
    lat0: float,
    lon0: float,
    lat1: float,
    lon1: float,
    contenedor_px: tuple[int, int] = CONTENEDOR_PX,
) -> int:
    """Estima el zoom que elegiría `fitBounds` de Leaflet para el bounds.

    Replica la lógica estándar: el zoom más alto en el que el bounds (en px,
    según Web Mercator) entra en el contenedor, considerando ancho y alto.
    El resultado es una estimación (Leaflet además aplica su propio redondeo y
    el tamaño real del contenedor en el navegador), por eso el embebido usa un
    rango ±1 alrededor de este valor.
    """
    ancho, alto = contenedor_px
    lat_min, lat_max = min(lat0, lat1), max(lat0, lat1)
    lon_min, lon_max = min(lon0, lon1), max(lon0, lon1)

    # Ancho en px de un grado de longitud a zoom z = 256 * 2^z / 360
    d_lon = max(lon_max - lon_min, 1e-9)
    z_por_ancho = math.log2(max(ancho * 360.0 / (256.0 * d_lon), 1.0))

    # Alto en px del rango de latitud (Mercator) a zoom z
    y_0 = _proyectar_y(lat_max)   # norte → y chico
    y_1 = _proyectar_y(lat_min)   # sur → y grande
    d_y = max(abs(y_1 - y_0), 1e-9)
    z_por_alto = math.log2(max(alto / (256.0 * d_y), 1.0))

    z = math.floor(min(z_por_ancho, z_por_alto))
    return max(ZOOM_MIN_ABS, min(z, ZOOM_MAX_ABS))


def _bounds_con_padding(
    lat0: float, lon0: float, lat1: float, lon1: float
) -> tuple[float, float, float, float]:
    """Estira el bounds con un padding proporcional (15% o 0.005° mínimo)."""
    lat_min, lat_max = min(lat0, lat1), max(lat0, lat1)
    lon_min, lon_max = min(lon0, lon1), max(lon0, lon1)
    pad = max((lat_max - lat_min) * 0.15, (lon_max - lon_min) * 0.15, 0.005)
    return (
        lat_min - pad,
        lon_min - pad,
        lat_max + pad,
        lon_max + pad,
    )


# ---------------------------------------------------------------------------
# Descarga con cache en disco
# ---------------------------------------------------------------------------

def descargar_tiles_png(
    tiles: list[tuple[int, int, int]],
    cache_dir: str = CACHE_DIR_DEFAULT,
) -> dict[tuple[int, int, int], bytes]:
    """Descarga los tiles (z,x,y) y devuelve {clave: bytes}.

    Los tiles ya presentes en `cache_dir/{z}/{x}/{y}.png` se leen del disco
    (no se re-descargan; los municipios cercanos comparten tiles).
    """
    os.makedirs(cache_dir, exist_ok=True)
    resultado: dict[tuple[int, int, int], bytes] = {}
    pendientes: list[tuple[int, int, int]] = []
    for (z, x, y) in tiles:
        ruta = os.path.join(cache_dir, str(z), str(x), f"{y}.png")
        if os.path.isfile(ruta):
            with open(ruta, "rb") as f:
                resultado[(z, x, y)] = f.read()
        else:
            pendientes.append((z, x, y))
    if pendientes:
        log.info("Descargando %d tiles nuevos...", len(pendientes))
        for (z, x, y) in pendientes:
            ruta = os.path.join(cache_dir, str(z), str(x), f"{y}.png")
            try:
                url = TILE_URL_ESRI.format(z=z, y=y, x=x)
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "flujos-tiles-offline/1.0"},
                )
                with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                    datos = resp.read()
                os.makedirs(os.path.dirname(ruta), exist_ok=True)
                with open(ruta, "wb") as f:
                    f.write(datos)
                resultado[(z, x, y)] = datos
            except Exception as e:  # noqa: BLE001
                log.warning("No se pudo descargar tile %d/%d/%d: %s", z, x, y, e)
    return resultado


def data_uris(tiles: dict[tuple[int, int, int], bytes]) -> dict[str, str]:
    """Convierte {tile: bytes} a {"z/x/y": "data:image/png;base64,..."}."""
    return {
        f"{z}/{x}/{y}": "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        for (z, x, y), png in tiles.items()
    }


# ---------------------------------------------------------------------------
# Overlay JS para incrustar en el HTML generado por Folium
# ---------------------------------------------------------------------------

def js_capa_base_embebida(
    variable_mapa: str,
    tiles: dict[str, str],
    atribucion: str = "",
) -> str:
    """Devuelve el JS de la capa de tiles única con la vista inicial embebida.

    Una sola L.TileLayer custom: para los tiles en `tiles` (claves "z/x/y")
    devuelve el data URI (se muestra al instante, sin red); para los demás
    delega en `L.TileLayer.prototype.getTileUrl` (carga de internet). Al ser la
    única capa, no se descarga dos veces la misma zona. `variable_mapa` es
    `mapa.get_name()` de Folium; `atribucion` es el texto de atribución que
    Leaflet agrega al control de atribución.

    Folium renderiza `var <mapa> = L.map(...)` SIEMPRE al final del bloque de
    script, después de cualquier elemento inyectado. Por eso este JS **pollea**
    la variable global del mapa (window[<mapa>]) hasta que exista (cada 50 ms,
    máx. 10 s) y recién entonces agrega la capa. No depende del orden de
    scripts ni del evento load.
    """
    tiles_json = json.dumps(tiles, separators=(",", ":"))
    nombre_json = json.dumps(variable_mapa)
    online_json = json.dumps(TILE_URL_ESRI)
    atribucion_json = json.dumps(atribucion)
    return f"""
(function() {{
  var _tiles = {tiles_json};
  var _online = {online_json};
  var _atribucion = {atribucion_json};
  var _nombre = {nombre_json};
  function _inicializar(_mapa) {{
    var _CapaBase = L.TileLayer.extend({{
      getTileUrl: function(coords) {{
        var key = coords.z + '/' + coords.x + '/' + coords.y;
        if (_tiles[key]) {{ return _tiles[key]; }}
        return L.TileLayer.prototype.getTileUrl.call(this, coords);
      }}
    }});
    var _capa = new _CapaBase(_online, {{
      attribution: _atribucion
    }});
    _capa.addTo(_mapa);
  }}
  var _intentos = 0;
  var _timer = setInterval(function() {{
    _intentos++;
    var _mapa = window[_nombre];
    if (_mapa) {{
      clearInterval(_timer);
      try {{ _inicializar(_mapa); }} catch (e) {{}}
    }} else if (_intentos > 200) {{
      clearInterval(_timer);
    }}
  }}, 50);
}})();
"""


# ---------------------------------------------------------------------------
# Función de alto nivel usada por mapas_municipio.py
# ---------------------------------------------------------------------------

def incrustar_tiles_vista_inicial(
    mapa,
    lats: list[float],
    lons: list[float],
    zooms: list[int] | None = None,
    cache_dir: str = CACHE_DIR_DEFAULT,
    atribucion: str = "",
) -> int:
    """Inyecta en un mapa Folium la capa de tiles con la vista inicial embebida.

    El mapa debe haberse creado SIN capa base (folium.Map(tiles=None)); esta
    función agrega UNA capa custom que resuelve los tiles de la vista inicial
    como data URIs (sin red) y delega el resto a CartoDB online. La capa se
    inyecta SIEMPRE (aunque `zooms=[]` o falle la descarga), así el mapa nunca
    queda sin tiles: con un dict vacío funciona en modo 100% online.

    Args:
        mapa: objeto folium.Map ya construido (tiles=None).
        lats/lons: coordenadas de los puntos del municipio (definen la vista).
        zooms: rango de zooms a incrustar (default ZOOMS_DEFAULT; [] = online).
        cache_dir: carpeta de cache de tiles.
        atribucion: texto de atribución de la capa (p. ej. ATTR_CARTO).

    Returns:
        Número de tiles incrustados (0 si zooms vacío o falló la descarga).
    """
    if not lats or not lons:
        return 0
    # zooms=None → default; zooms=[] → modo online (sin embebido).
    zooms = sorted(set(zooms if zooms is not None else ZOOMS_DEFAULT))
    lat0, lon0, lat1, lon1 = _bounds_con_padding(min(lats), min(lons), max(lats), max(lons))
    if zooms:
        z_est = zoom_fit_bounds(lat0, lon0, lat1, lon1)
        if z_est < zooms[0] - 1 or z_est > zooms[-1] + 1:
            zooms = sorted(set(zooms + [z_est - 1, z_est, z_est + 1]))
            zooms = [z for z in zooms if ZOOM_MIN_ABS <= z <= ZOOM_MAX_ABS]

    try:
        uris: dict[str, str] = {}
        if zooms:
            tiles = tiles_en_bounds(lat0, lon0, lat1, lon1, zooms)
            pngs = descargar_tiles_png(tiles, cache_dir)
            if pngs:
                uris = data_uris(pngs)
        js = js_capa_base_embebida(mapa.get_name(), uris, atribucion=atribucion)
        from folium import Element
        mapa.get_root().script.add_child(Element(js))
        return len(uris)
    except Exception as e:  # noqa: BLE001
        log.warning("No se pudo inyectar la capa de tiles embebidos: %s", e)
        return 0


# ---------------------------------------------------------------------------
# Assets JS/CSS de Folium autocontenidos (cero red)
# ---------------------------------------------------------------------------

def _assets_folium() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Listas (name, url) de los JS/CSS que Folium referencia por CDN.

    Se leen de `folium.folium._default_js/_default_css` del Folium instalado
    (0.20) para mantenerse en sync con la versión.
    """
    import folium.folium as _ff
    return list(_ff._default_js), list(_ff._default_css)


def _assets_fuentes(css_urls: list[str]) -> dict[str, str]:
    """Deriva las URLs de las fuentes de íconos desde los CSS de Folium.

    FontAwesome (all.min.css) → ../webfonts/fa-solid-900.woff2; glyphicons
    (bootstrap-glyphicons.css) → ../fonts/glyphicons-halflings-regular.woff.
    """
    fuentes: dict[str, str] = {}
    for url in css_urls:
        if "fontawesome-free" in url:
            base = url.rsplit("/", 2)[0]  # .../fontawesome-free@6.2.0
            fuentes["fa-solid-900.woff2"] = base + "/webfonts/fa-solid-900.woff2"
        elif "bootstrap-glyphicons" in url:
            base = url.rsplit("/", 2)[0]  # .../bootstrap/3.0.0
            fuentes["glyphicons-halflings-regular.woff"] = (
                base + "/fonts/glyphicons-halflings-regular.woff"
            )
    return fuentes


def _descargar_a(url: str, ruta: str) -> None:
    """Descarga `url` a `ruta` (bytes) si no existe."""
    if os.path.isfile(ruta):
        return
    req = urllib.request.Request(url, headers={"User-Agent": "flujos-assets-offline/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        datos = resp.read()
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "wb") as f:
        f.write(datos)


def descargar_assets(cache_dir: str = CACHE_DIR_ASSETS_DEFAULT) -> None:
    """Descarga/cachea los assets JS/CSS de Folium y las fuentes de íconos.

    Los assets ya presentes en `cache_dir/` no se re-descargan. Los fallos
    individuales se loguean y no abortan: `_assets_core_completos` decide si el
    set quedó utilizable.
    """
    js, css = _assets_folium()
    fuentes = _assets_fuentes([u for _, u in css])
    os.makedirs(cache_dir, exist_ok=True)
    pendientes: list[tuple[str, str]] = []
    for name, url in js:
        pendientes.append((os.path.join(cache_dir, "js", name), url))
    for name, url in css:
        pendientes.append((os.path.join(cache_dir, "css", name), url))
    for nombre_font, url in fuentes.items():
        pendientes.append((os.path.join(cache_dir, "fuentes", nombre_font), url))
    nuevos = [p for p in pendientes if not os.path.isfile(p[0])]
    if not nuevos:
        return
    log.info("Descargando %d assets offline...", len(nuevos))
    for ruta, url in nuevos:
        try:
            _descargar_a(url, ruta)
        except Exception as e:  # noqa: BLE001
            log.warning("No se pudo descargar asset %s: %s", url, e)


def _assets_core_completos(cache_dir: str = CACHE_DIR_ASSETS_DEFAULT) -> bool:
    """True si están todos los JS/CSS de Folium (las fuentes son opcionales)."""
    js, css = _assets_folium()
    for name, _ in js:
        if not os.path.isfile(os.path.join(cache_dir, "js", name)):
            return False
    for name, _ in css:
        if not os.path.isfile(os.path.join(cache_dir, "css", name)):
            return False
    return True


def _leer_texto(ruta: str) -> str:
    with open(ruta, "rb") as f:
        return f.read().decode("utf-8", errors="replace")


def _reemplazar_script(html: str, url: str, contenido: str) -> str:
    """Reemplaza <script src="url"></script> por <script>contenido</script>."""
    return re.sub(
        r'<script[^>]*src="' + re.escape(url) + r'"[^>]*></script>',
        lambda m: "<script>" + contenido + "</script>",
        html,
    )


def _reemplazar_link_css(html: str, url: str, contenido: str) -> str:
    """Reemplaza <link ... href="url" .../> por <style>contenido</style>."""
    return re.sub(
        r'<link[^>]*href="' + re.escape(url) + r'"[^>]*>',
        lambda m: "<style>" + contenido + "</style>",
        html,
    )


def inline_fuentes(html: str, cache_dir: str = CACHE_DIR_ASSETS_DEFAULT) -> str:
    """Reemplaza los url(...) de las fuentes de íconos por data URIs.

    Tras incrustar los CSS inline, sus referencias relativas a fuentes
    (`../webfonts/fa-solid-900.woff2`, `../fonts/glyphicons...woff`) quedarían
    rotas en file://. Se reescriben a data:font/...;base64.
    Las fuentes no descargadas simplemente no se reemplazan (íconos en blanco).
    """
    fuentes = _assets_fuentes([u for _, u in _assets_folium()[1]])
    for nombre_font, _url in fuentes.items():
        ruta = os.path.join(cache_dir, "fuentes", nombre_font)
        if not os.path.isfile(ruta):
            continue
        with open(ruta, "rb") as f:
            datos = f.read()
        mime = "font/woff2" if nombre_font.endswith(".woff2") else "font/woff"
        uri = f"data:{mime};base64," + base64.b64encode(datos).decode("ascii")
        patron = re.compile(
            r"url\(\s*(['\"]?)[^'\")]*" + re.escape(nombre_font) + r"\1\s*\)"
        )
        html = patron.sub(lambda m: f'url("{uri}")', html)
    return html


def guardar_autocontenido(
    mapa,
    ruta: str,
    cache_dir: str = CACHE_DIR_ASSETS_DEFAULT,
) -> bool:
    """Guarda un mapa Folium como HTML 100% autocontenido (cero red).

    Descarga (cache) los assets JS/CSS de Folium y las fuentes de íconos,
    renderiza el mapa con el método normal (no `embedded=True`: folium propaga
    el kwarg `embedded` a todos los hijos y `Marker.render()` no lo acepta) y
    reemplaza en el HTML los tags `<script src>`/`<link href>` de CDN por su
    contenido inline. Si el set de assets no está completo (p. ej. sin internet
    al generar), guarda con `mapa.save(ruta)` normal (CDN).

    Args:
        mapa: objeto folium.Map ya construido.
        ruta: ruta de salida del HTML.
        cache_dir: carpeta de cache de assets.

    Returns:
        True si se guardó autocontenido; False si se usó el fallback CDN.
    """
    try:
        descargar_assets(cache_dir)
    except Exception as e:  # noqa: BLE001
        log.warning("No se pudieron obtener assets offline; mapa con CDN: %s", e)
        mapa.save(ruta)
        return False
    if not _assets_core_completos(cache_dir):
        log.warning("Assets offline incompletos; mapa con CDN.")
        mapa.save(ruta)
        return False
    js, css = _assets_folium()
    html = mapa.get_root().render()
    for name, url in js:
        html = _reemplazar_script(
            html, url, _leer_texto(os.path.join(cache_dir, "js", name))
        )
    for name, url in css:
        html = _reemplazar_link_css(
            html, url, _leer_texto(os.path.join(cache_dir, "css", name))
        )
    html = inline_fuentes(html, cache_dir)
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(html)
    return True


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Precarga tiles de la vista inicial y assets offline de Folium a disco."
    )
    parser.add_argument("lat0", type=float, nargs="?", default=None, help="Latitud esquina 1")
    parser.add_argument("lon0", type=float, nargs="?", default=None, help="Longitud esquina 1")
    parser.add_argument("lat1", type=float, nargs="?", default=None, help="Latitud esquina 2")
    parser.add_argument("lon1", type=float, nargs="?", default=None, help="Longitud esquina 2")
    parser.add_argument("--zooms", default="11,12,13", help="Zooms separados por coma")
    parser.add_argument("--cache-dir", default=CACHE_DIR_DEFAULT)
    parser.add_argument("--assets", action="store_true",
                        help="Solo descargar los assets JS/CSS/fuentes de Folium a assets_cache/")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.assets:
        descargar_assets()
        print(f"Assets offline: core completo={_assets_core_completos()}")
    elif args.lat0 is not None:
        zooms = [int(z) for z in args.zooms.split(",")]
        lat0, lon0, lat1, lon1 = _bounds_con_padding(args.lat0, args.lon0, args.lat1, args.lon1)
        tiles = tiles_en_bounds(lat0, lon0, lat1, lon1, zooms)
        pngs = descargar_tiles_png(tiles, args.cache_dir)
        print(f"Tiles en cache: {len(pngs)}/{len(tiles)}")
    else:
        parser.error("Indicá coordenadas o --assets")