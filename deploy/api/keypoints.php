<?php
/**
 * Devuelve N keypoints al azar (pure random, ORDER BY RANDOM()).
 * GET params:
 *   tipo   = transcripcion|transcription|contexto  (default transcripcion)
 *            transcripcion → kp_key='transcription'
 *            contexto      → kp_key LIKE 'contexto_%'
 *            (también acepta valores exactos de kp_key como contexto_astronomia, etc.)
 *   limite = 1..100 (default 50)
 */
header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
require_once __DIR__ . '/../includes/db.php';
$pdo = db();

$tipoRaw = isset($_GET['tipo']) ? trim((string)$_GET['tipo']) : 'transcripcion';
$tipoRaw = strtolower($tipoRaw);
$limite = isset($_GET['limite']) ? (int)$_GET['limite'] : 50;
if ($limite < 1) $limite = 1;
if ($limite > 100) $limite = 100;

// Mapear tipo → condición WHERE sobre kp_key
$where = '';
$params = [];
$tipoNorm = $tipoRaw;
if ($tipoRaw === '' || $tipoRaw === 'transcripcion' || $tipoRaw === 'transcription') {
    $where = "kp_key = 'transcription'";
    $tipoNorm = 'transcripcion';
} elseif ($tipoRaw === 'contexto') {
    $where = "kp_key LIKE 'contexto_%'";
    $tipoNorm = 'contexto';
} elseif (strpos($tipoRaw, 'contexto_') === 0) {
    $where = "kp_key = :kpkey";
    $params[':kpkey'] = $tipoRaw;
    $tipoNorm = $tipoRaw;
} else {
    // Fallback: intentar como valor exacto de kp_key
    $where = "kp_key = :kpkey";
    $params[':kpkey'] = $tipoRaw;
    $tipoNorm = $tipoRaw;
}

$sql = "SELECT id, media_id, kp_key, value, offset_secs, timestamp_absolute,
               media_tipo, media_subtipo, archivo, carpeta,
               latitud, longitud, posicion_fuente, fecha, hora
        FROM keypoints
        WHERE $where
        ORDER BY RANDOM()
        LIMIT :limite";

try {
    $stmt = $pdo->prepare($sql);
    foreach ($params as $k => $v) {
        $stmt->bindValue($k, $v, PDO::PARAM_STR);
    }
    $stmt->bindValue(':limite', $limite, PDO::PARAM_INT);
    $stmt->execute();
    $filas = $stmt->fetchAll(PDO::FETCH_ASSOC);
} catch (Exception $e) {
    http_response_code(500);
    echo json_encode(['error' => 'db', 'detalle' => $e->getMessage()], JSON_UNESCAPED_UNICODE);
    exit;
}

// Total disponible del tipo (para info)
$totalDisponible = 0;
try {
    $cntSql = "SELECT COUNT(*) FROM keypoints WHERE $where";
    $cntStmt = $pdo->prepare($cntSql);
    foreach ($params as $k => $v) {
        $cntStmt->bindValue($k, $v, PDO::PARAM_STR);
    }
    $cntStmt->execute();
    $totalDisponible = (int)$cntStmt->fetchColumn();
} catch (Exception $e) {
    $totalDisponible = count($filas);
}

echo json_encode([
    'tipo' => $tipoNorm,
    'limite' => $limite,
    'total_disponible' => $totalDisponible,
    'total' => count($filas),
    'keypoints' => $filas,
], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
