<?php
/**
 * Devuelve hasta 10 fotos más cercanas a un keypoint.
 * GET params:
 *   kp_id = id en tabla keypoints (obligatorio)
 *   limite = 1..10 (default 10)
 *
 * Lógica (lazy, pure server-side):
 *   1. Lee keypoint (latitud, longitud, timestamp_absolute/fecha/hora)
 *   2. Si tiene lat/lon → 10 más cercanas por Haversine (aprox SQL + Haversine exacto para dist_m)
 *      Si faltan para 10 → relleno temporal por |Δ fecha|.
 *   3. Si no tiene posición → solo temporal.
 */
header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
require_once __DIR__ . '/../includes/db.php';
$pdo = db();

$kpId = isset($_GET['kp_id']) ? (int)$_GET['kp_id'] : 0;
$limite = isset($_GET['limite']) ? max(1, min(10, (int)$_GET['limite'])) : 10;

if ($kpId <= 0) {
    http_response_code(400);
    echo json_encode(['error' => 'kp_id requerido'], JSON_UNESCAPED_UNICODE);
    exit;
}

function haversine_m($lat1, $lon1, $lat2, $lon2) {
    $R = 6371000;
    $dLat = deg2rad($lat2 - $lat1);
    $dLon = deg2rad($lon2 - $lon1);
    $a = sin($dLat/2)*sin($dLat/2) + cos(deg2rad($lat1))*cos(deg2rad($lat2))*sin($dLon/2)*sin($dLon/2);
    $c = 2 * atan2(sqrt($a), sqrt(1-$a));
    return $R * $c;
}

try {
    $stmt = $pdo->prepare("SELECT id, latitud, longitud, timestamp_absolute, fecha, hora FROM keypoints WHERE id = :id");
    $stmt->bindValue(':id', $kpId, PDO::PARAM_INT);
    $stmt->execute();
    $kp = $stmt->fetch(PDO::FETCH_ASSOC);
} catch (Exception $e) {
    http_response_code(500);
    echo json_encode(['error' => 'db', 'detalle' => $e->getMessage()], JSON_UNESCAPED_UNICODE);
    exit;
}

if (!$kp) {
    http_response_code(404);
    echo json_encode(['error' => 'keypoint no encontrado', 'kp_id' => $kpId], JSON_UNESCAPED_UNICODE);
    exit;
}

$kpLat = $kp['latitud'] !== null ? (float)$kp['latitud'] : null;
$kpLon = $kp['longitud'] !== null ? (float)$kp['longitud'] : null;
$kpFecha = $kp['fecha'];
$kpHora = $kp['hora'];
$kpTs = $kp['timestamp_absolute'];

$resultado = [];
$vistos = [];

// Helper para construir fecha/hora para julianday: "YYYY-MM-DD HH:MM"
function ts_para_julianday($fecha, $hora) {
    if (!$fecha) return null;
    $h = $hora ?: '00:00';
    // Normalizar a "YYYY-MM-DD HH:MM:00" para julianday
    if (strlen($h) === 5) $h .= ':00';
    return $fecha . ' ' . $h;
}

if ($kpLat !== null && $kpLon !== null) {
    // 1) Geo: 10 más cercanas por distancia equirectangular aprox (rápido en SQLite)
    try {
        $sqlGeo = "SELECT id, archivo, carpeta, latitud, longitud, fecha, hora
                   FROM medios
                   WHERE tipo = 'image' AND latitud IS NOT NULL AND longitud IS NOT NULL
                   ORDER BY ((latitud - :lat)*(latitud - :lat) + (longitud - :lon)*(longitud - :lon))
                   LIMIT :lim";
        $stmtGeo = $pdo->prepare($sqlGeo);
        $stmtGeo->bindValue(':lat', $kpLat);
        $stmtGeo->bindValue(':lon', $kpLon);
        $stmtGeo->bindValue(':lim', $limite, PDO::PARAM_INT);
        $stmtGeo->execute();
        $candidatas = $stmtGeo->fetchAll(PDO::FETCH_ASSOC);
    } catch (Exception $e) {
        $candidatas = [];
    }
    foreach ($candidatas as $c) {
        $dist = haversine_m($kpLat, $kpLon, (float)$c['latitud'], (float)$c['longitud']);
        $resultado[] = [
            'id' => (int)$c['id'],
            'archivo' => $c['archivo'],
            'carpeta' => $c['carpeta'],
            'latitud' => $c['latitud'] !== null ? (float)$c['latitud'] : null,
            'longitud' => $c['longitud'] !== null ? (float)$c['longitud'] : null,
            'fecha' => $c['fecha'],
            'hora' => $c['hora'],
            'dist_m' => round($dist, 1),
            'delta_secs' => null,
        ];
        $vistos[(int)$c['id']] = true;
    }

    // Relleno temporal si faltan
    if (count($resultado) < $limite && $kpFecha) {
        $faltan = $limite - count($resultado);
        $kpJd = ts_para_julianday($kpFecha, $kpHora);
        if ($kpJd) {
            $excluir = count($vistos) ? 'AND id NOT IN (' . implode(',', array_keys($vistos)) . ')' : '';
            try {
                $sqlTemp = "SELECT id, archivo, carpeta, latitud, longitud, fecha, hora
                            FROM medios
                            WHERE tipo = 'image' AND fecha IS NOT NULL $excluir
                            ORDER BY ABS(julianday(fecha || ' ' || COALESCE(hora,'00:00') || ':00') - julianday(:kpjd))
                            LIMIT :lim";
                $stmtTemp = $pdo->prepare($sqlTemp);
                $stmtTemp->bindValue(':kpjd', $kpJd);
                $stmtTemp->bindValue(':lim', $faltan, PDO::PARAM_INT);
                $stmtTemp->execute();
                $extras = $stmtTemp->fetchAll(PDO::FETCH_ASSOC);
                // Necesitamos la hora del kp para delta_secs
                $kpTsSec = $kpTs ? strtotime($kpTs) : ($kpFecha ? strtotime($kpJd) : null);
                foreach ($extras as $c) {
                    $cTs = $c['fecha'] ? strtotime(ts_para_julianday($c['fecha'], $c['hora'])) : null;
                    $delta = null;
                    if ($kpTsSec !== null && $cTs !== null) $delta = round(abs($cTs - $kpTsSec), 1);
                    $resultado[] = [
                        'id' => (int)$c['id'],
                        'archivo' => $c['archivo'],
                        'carpeta' => $c['carpeta'],
                        'latitud' => $c['latitud'] !== null ? (float)$c['latitud'] : null,
                        'longitud' => $c['longitud'] !== null ? (float)$c['longitud'] : null,
                        'fecha' => $c['fecha'],
                        'hora' => $c['hora'],
                        'dist_m' => null,
                        'delta_secs' => $delta,
                    ];
                }
            } catch (Exception $e) {
                // silencioso
            }
        }
    }
} elseif ($kpFecha) {
    // Sin posición → solo temporal
    $kpJd = ts_para_julianday($kpFecha, $kpHora);
    if ($kpJd) {
        try {
            $sqlTemp = "SELECT id, archivo, carpeta, latitud, longitud, fecha, hora
                        FROM medios
                        WHERE tipo = 'image' AND fecha IS NOT NULL
                        ORDER BY ABS(julianday(fecha || ' ' || COALESCE(hora,'00:00') || ':00') - julianday(:kpjd))
                        LIMIT :lim";
            $stmtTemp = $pdo->prepare($sqlTemp);
            $stmtTemp->bindValue(':kpjd', $kpJd);
            $stmtTemp->bindValue(':lim', $limite, PDO::PARAM_INT);
            $stmtTemp->execute();
            $extras = $stmtTemp->fetchAll(PDO::FETCH_ASSOC);
            $kpTsSec = $kpTs ? strtotime($kpTs) : strtotime($kpJd);
            foreach ($extras as $c) {
                $cTs = strtotime(ts_para_julianday($c['fecha'], $c['hora']));
                $delta = ($kpTsSec !== null && $cTs !== false) ? round(abs($cTs - $kpTsSec), 1) : null;
                $resultado[] = [
                    'id' => (int)$c['id'],
                    'archivo' => $c['archivo'],
                    'carpeta' => $c['carpeta'],
                    'latitud' => $c['latitud'] !== null ? (float)$c['latitud'] : null,
                    'longitud' => $c['longitud'] !== null ? (float)$c['longitud'] : null,
                    'fecha' => $c['fecha'],
                    'hora' => $c['hora'],
                    'dist_m' => null,
                    'delta_secs' => $delta,
                ];
            }
        } catch (Exception $e) {
            // silencioso
        }
    }
}

echo json_encode([
    'kp_id' => $kpId,
    'kp_latitud' => $kpLat,
    'kp_longitud' => $kpLon,
    'total' => count($resultado),
    'fotos' => array_slice($resultado, 0, $limite),
], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
