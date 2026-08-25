<?php
/**
 * Devuelve hasta N medios aleatorios filtrados por municipio/color/provincia/tag/tipo.
 * GET params:
 *   municipio (string, opcional; acepta valores separados por coma)
 *   color     (string, opcional; acepta valores separados por coma)
 *   provincia (string, opcional; acepta valores separados por coma)
 *   tag       (string, opcional; acepta valores separados por coma)
 *   horas     (string, opcional; valores 0..23 separados por coma; filtra por franja [min,max] en hora local Argentina UTC-3)
 *   subtipo   (string, opcional; acepta valores separados por coma, ej: 360)
 *   tipo      (string, opcional: image,video,audio,text — separado por coma;
 *              'text' devuelve los medios type='text' (textos del viaje))
 *   limite    (int, opcional, default 20)
 */
header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
require_once __DIR__ . '/../includes/db.php';
$pdo = db();

$municipio = isset($_GET['municipio']) ? trim($_GET['municipio']) : '';
$color     = isset($_GET['color'])     ? trim($_GET['color'])     : '';
$provincia = isset($_GET['provincia']) ? trim($_GET['provincia']) : '';
$tag       = isset($_GET['tag'])       ? trim($_GET['tag'])       : '';
$horas     = isset($_GET['horas'])    ? trim($_GET['horas'])     : '';
$subtipo   = isset($_GET['subtipo'])  ? trim($_GET['subtipo'])   : '';
$tipoStr   = isset($_GET['tipo'])      ? trim($_GET['tipo'])      : '';
$limite    = isset($_GET['limite'])    ? max(1, min(20, (int)$_GET['limite'])) : 5;

function valores_param($texto) {
    $valores = array_map('trim', explode(',', $texto));
    $valores = array_filter($valores, function($v) { return $v !== ''; });
    return array_values(array_unique($valores));
}

function minusculas_utf8_ligero($texto) {
    $texto = strtr($texto, [
        'Á' => 'á', 'É' => 'é', 'Í' => 'í', 'Ó' => 'ó', 'Ú' => 'ú',
        'Ü' => 'ü', 'Ñ' => 'ñ',
    ]);
    return strtolower($texto);
}

function agregar_in(&$condiciones, &$params, $columna, $prefijo, $valores) {
    if (!count($valores)) return;
    $marcas = [];
    foreach ($valores as $i => $valor) {
        $k = ':' . $prefijo . $i;
        $marcas[] = $k;
        $params[$k] = $valor;
    }
    $condiciones[] = $columna . ' IN (' . implode(',', $marcas) . ')';
}

// Construir WHERE dinámico
$condiciones = [];
$params = [];

$municipios = valores_param($municipio);
$colores = valores_param($color);
$provincias = valores_param($provincia);
$tags = valores_param($tag);
$subtipos = valores_param($subtipo);

$horasSelec = [];
foreach (valores_param($horas) as $hv) {
    if (is_numeric($hv)) {
        $ih = (int)$hv;
        if ($ih >= 0 && $ih <= 23) $horasSelec[] = $ih;
    }
}
$horasSelec = array_values(array_unique($horasSelec));

agregar_in($condiciones, $params, 'm.municipio', 'municipio', $municipios);
agregar_in($condiciones, $params, 'm.provincia', 'provincia', $provincias);
agregar_in($condiciones, $params, 'm.subtipo', 'subtipo', $subtipos);

if (count($colores)) {
    $partesColor = [];
    foreach ($colores as $i => $valor) {
        $k = ':color' . $i;
        $partesColor[] = "(m.color_1 = $k OR m.color_2 = $k OR m.color_3 = $k)";
        $params[$k] = $valor;
    }
    $condiciones[] = '(' . implode(' OR ', $partesColor) . ')';
}

if (count($tags)) {
    $partesTag = [];
    foreach ($tags as $i => $valor) {
        $k = ':tag' . $i;
        $partesTag[] = "(',' || lower(replace(replace(m.keywords, ', ', ','), ' ,', ',')) || ',') LIKE $k";
        $params[$k] = '%,' . minusculas_utf8_ligero($valor) . ',%';
    }
    $condiciones[] = 'm.keywords IS NOT NULL AND (' . implode(' OR ', $partesTag) . ')';
}

// Franja horaria simple [min,max] sobre hora local Argentina UTC-3
// (la DB guarda m.hora en UTC como 'HH:MM'; medios con hora NULL quedan excluidos con el filtro activo)
if (count($horasSelec)) {
    $params[':hmin'] = min($horasSelec);
    $params[':hmax'] = max($horasSelec);
    $condiciones[] = "((CAST(substr(m.hora,1,2) AS INTEGER) - 3 + 24) % 24) BETWEEN :hmin AND :hmax";
}

$where = '';
if (count($condiciones)) {
    $where = 'WHERE ' . implode(' AND ', $condiciones);
}

// Tipos solicitados (incluye 'text' = medios tipo texto del viaje)
$tipos = ['image', 'video', 'audio', 'text'];
if ($tipoStr !== '') {
    $t = explode(',', $tipoStr);
    $t = array_map('trim', $t);
    $t = array_intersect($t, $tipos);
    if (count($t)) $tipos = array_values($t);
}

$resultados = [];

// El bloque 360 necesita más candidatos: después se filtran los que no tienen
// archivo en media/ (serían links rotos en el visor).
$limiteConsulta = in_array('360', $subtipos, true) ? 60 : $limite;

foreach ($tipos as $tipo) {
    // WHERE dinámico: si hay filtros previos, concatenar con AND
    $whereTipo = ($where ? ' AND' : ' WHERE') . ' m.tipo = :tipo';
    $sql = "SELECT m.id, m.archivo, m.tipo, m.subtipo, m.carpeta,
                   m.ruta_relativa, m.tamano_bytes, m.duracion_seg,
                   m.fecha, m.hora,
                   m.color_1, m.color_1_hex,
                   m.provincia, m.municipio, m.localidad,
                   m.titulo, m.descripcion, m.transcripcion
            FROM medios m
            $where$whereTipo
            ORDER BY RANDOM()
            LIMIT :limite";

    $stmt = $pdo->prepare($sql);
    foreach ($params as $k => $v) {
        // Los enteros (ej: :hmin/:hmax de la franja horaria) se vinculan como INT;
        // si se vinculan como STR, SQLite compara entero < texto y no hay coincidencias.
        $stmt->bindValue($k, $v, is_int($v) ? PDO::PARAM_INT : PDO::PARAM_STR);
    }
    $stmt->bindValue(':tipo', $tipo);
    $stmt->bindValue(':limite', $limiteConsulta, PDO::PARAM_INT);
    $stmt->execute();
    $filas = $stmt->fetchAll(PDO::FETCH_ASSOC);

    $resultados[$tipo] = $filas;
}

// Disponibilidad web: para el bloque 360 solo se devuelven los videos cuyo
// archivo existe en media/<carpeta>/<archivo> (los demás son links rotos).
if (in_array('360', $subtipos, true) && isset($resultados['video'])) {
    $disponibles = array_filter($resultados['video'], function ($m) {
        $carpeta = str_replace('\\', '/', trim((string)$m['carpeta'], "/\\ \t\n\r\0\x0B"));
        $archivo = (string)$m['archivo'];
        if ($archivo === '') return false;
        return file_exists(__DIR__ . '/../media/' . ($carpeta !== '' ? $carpeta . '/' : '') . $archivo);
    });
    $resultados['video'] = array_slice(array_values($disponibles), 0, $limite);
}

echo json_encode([
    'total_general' => array_sum(array_map('count', $resultados)),
    'filtros' => [
        'municipio' => $municipio,
        'color'     => $color,
        'provincia' => $provincia,
        'tag'       => $tag,
        'horas'     => $horasSelec,
        'subtipo'   => $subtipos,
        'tipos'     => $tipos,
        'limite'    => $limite
    ],
    'resultados' => $resultados
], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
