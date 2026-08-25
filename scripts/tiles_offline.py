#!/usr/bin/env python3
"""
tiles_offline.py — Predescarga de tiles de la vista inicial para mapas Folium.

Los mapas por municipio que renderiza TouchDesigner (Web Render TOP sobre
`file://`) descargan sus tiles de CartoDB por internet en runtime, y al abrir
varios mapas a la vez se genera un cuello de botella de red. Este módulo
incrusta en el propio HTML los tiles de la **vista inicial** como data URIs
(base64): al abrir el mapa, Leaflet los muestra al instante sin red, y el
zoom/desplazamiento posterior sigue cargando de internet (capa única).

Nada de esto requiere servidor: los data URIs son parte del documento HTML y
funcionan en `file://` (Chromium de TD), sin CORS ni procesos extra.

Estrategia (UNA sola capa de tiles, sin doble descarga):
  El mapa se crea SIN capa base (folium.Map(tiles=None)) y `incrustar_tiles_vista_inicial`
  inyecta un L.TileLayer custom que, para los tiles de la vista inicial,
  devuelve el data URI incrustado y, para los demás, delega en la URL online de
  CartoDB. Al ser la única capa, la zona de la vista inicial NO se descarga dos
  veces (el mapa base de Folium no existe). Si la descarga falla o `zooms` es
  vacío, se inyecta la capa igualmente (dict vacío) y el mapa funciona 100%
  online.

Zooms por defecto: 11-13 (aprobado por el usuario). Si el zoom inicial que
elegiría `fitBounds` cae fuera de ese rango (municipios muy chicos/grandes), se
expande automáticamente a `[z_est-1, z_est, z_est+1]` para cubrir la vista.
"""

import base64
import json
import logging
import math
import os
import urllib.request

log = logging.getLogger("tiles_offline")

# URL de tiles CartoDB positron (light_all), misma que usa Folium para
# 'CartoDB positron'. Se descarga sin retina ({r}).
TILE_URL_CARTO = "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png"
SUBDOMINIOS = "abcd"
ZOOMS_DEFAULT = [11, 12, 13]
CACHE_DIR_DEFAULT = "tiles_cache"
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
                url = TILE_URL_CARTO.format(s="a", z=z, x=x, y=y)
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
    online_json = json.dumps(TILE_URL_CARTO)
    subs_json = json.dumps(SUBDOMINIOS)
    atribucion_json = json.dumps(atribucion)
    return f"""
(function() {{
  var _tiles = {tiles_json};
  var _online = {online_json};
  var _subs = {subs_json};
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
      subdomains: _subs,
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


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Descarga los tiles de la vista inicial de una zona a la cache de disco."
    )
    parser.add_argument("lat0", type=float, help="Latitud esquina 1")
    parser.add_argument("lon0", type=float, help="Longitud esquina 1")
    parser.add_argument("lat1", type=float, help="Latitud esquina 2")
    parser.add_argument("lon1", type=float, help="Longitud esquina 2")
    parser.add_argument("--zooms", default="11,12,13", help="Zooms separados por coma")
    parser.add_argument("--cache-dir", default=CACHE_DIR_DEFAULT)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    zooms = [int(z) for z in args.zooms.split(",")]
    lat0, lon0, lat1, lon1 = _bounds_con_padding(args.lat0, args.lon0, args.lat1, args.lon1)
    tiles = tiles_en_bounds(lat0, lon0, lat1, lon1, zooms)
    pngs = descargar_tiles_png(tiles, args.cache_dir)
    print(f"Tiles en cache: {len(pngs)}/{len(tiles)}")