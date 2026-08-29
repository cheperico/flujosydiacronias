<?php
/**
 * Sirve un archivo multimedia por ID.
 * GET: ?id=123
 */
require_once __DIR__ . '/../includes/db.php';
require_once __DIR__ . '/../includes/rutas.php';
$pdo = db();

$id = isset($_GET['id']) ? (int)$_GET['id'] : 0;
if ($id <= 0) { http_response_code(400); echo "ID invalido"; exit; }

$stmt = $pdo->prepare("SELECT ruta_absoluta, tipo, carpeta, archivo FROM medios WHERE id = ?");
$stmt->execute([$id]);
$row = $stmt->fetch(PDO::FETCH_ASSOC);

if (!$row) {
    http_response_code(404);
    echo "Archivo no encontrado";
    exit;
}

// Resuelve la ruta real del archivo (absoluta local en snapshot-local, media/... en deploy).
$path = flujos_resolver_archivo($row['ruta_absoluta'], $row['carpeta'], $row['archivo']);

if ($path === null) {
    http_response_code(404);
    echo "Archivo no existe en disco";
    exit;
}

$ext = strtolower(pathinfo($path, PATHINFO_EXTENSION));

// Mapear extensiones a MIME
$mimes = [
    'jpg' => 'image/jpeg', 'jpeg' => 'image/jpeg',
    'png' => 'image/png', 'gif' => 'image/gif',
    'webp' => 'image/webp', 'bmp' => 'image/bmp',
    'mp3' => 'audio/mpeg', 'wav' => 'audio/wav',
    'ogg' => 'audio/ogg', 'm4a' => 'audio/mp4',
    'aac' => 'audio/aac', 'wma' => 'audio/x-ms-wma',
    'mp4' => 'video/mp4', 'mov' => 'video/quicktime',
    'avi' => 'video/x-msvideo', 'webm' => 'video/webm',
    'mkv' => 'video/x-matroska'
];

$mime = isset($mimes[$ext]) ? $mimes[$ext] : 'application/octet-stream';

// Si es thumb y es imagen, redimensionar a ~200px
// Requiere la extensión GD de PHP
$esThumb = isset($_GET['thumb']) && $row['tipo'] === 'image'
           && in_array($ext, ['jpg','jpeg','png','gif','webp'])
           && function_exists('imagecreatefromjpeg');

if ($esThumb) {
    $maxW = 200;
    $info = @getimagesize($path);
    if ($info) {
        list($w, $h) = $info;
        if ($w > $maxW) {
            $ratio = $maxW / $w;
            $nw = $maxW;
            $nh = round($h * $ratio);
            $src = null;
            switch ($info[2]) {
                case IMAGETYPE_JPEG: $src = @imagecreatefromjpeg($path); break;
                case IMAGETYPE_PNG:  $src = @imagecreatefrompng($path); break;
                case IMAGETYPE_GIF:  $src = @imagecreatefromgif($path); break;
                case IMAGETYPE_WEBP: $src = @imagecreatefromwebp($path); break;
            }
            if ($src) {
                $thumb = imagecreatetruecolor($nw, $nh);
                imagecopyresampled($thumb, $src, 0, 0, 0, 0, $nw, $nh, $w, $h);
                header('Content-Type: ' . $mime);
                switch ($info[2]) {
                    case IMAGETYPE_JPEG: imagejpeg($thumb, null, 70); break;
                    case IMAGETYPE_PNG:  imagepng($thumb, null, 6); break;
                    case IMAGETYPE_GIF:  imagegif($thumb); break;
                    case IMAGETYPE_WEBP: imagewebp($thumb, null, 70); break;
                }
                imagedestroy($thumb);
                imagedestroy($src);
                exit;
            }
        }
    }
    // Si no se pudo redimensionar, servir original pero con Content-Type
}

$tamano = filesize($path);
$inicio = 0;
$fin    = $tamano - 1;

header('Accept-Ranges: bytes');
if (isset($_SERVER['HTTP_RANGE']) && preg_match('/bytes=(\d*)-(\d*)/', $_SERVER['HTTP_RANGE'], $m)) {
    $r0 = $m[1] !== '' ? (int)$m[1] : null;
    $r1 = $m[2] !== '' ? (int)$m[2] : null;
    if ($r0 === null && $r1 === null) {
        $r0 = 0; $r1 = $tamano - 1;
    } elseif ($r0 === null) {          // rango sufijo: bytes=-N
        $n = (int)$m[2];
        $r1 = $tamano - 1;
        $r0 = max(0, $tamano - $n);
    } else {                            // rango desde start
        if ($r1 === null) $r1 = $tamano - 1;
    }
    if ($r0 > $r1 || $r0 >= $tamano) {
        http_response_code(416);
        header('Content-Range: bytes */' . $tamano);
        exit;
    }
    if ($r1 >= $tamano) $r1 = $tamano - 1;
    $inicio = $r0;
    $fin    = $r1;
    http_response_code(206);
    header('Content-Range: bytes ' . $inicio . '-' . $fin . '/' . $tamano);
}

$longitud = $fin - $inicio + 1;
header('Content-Type: ' . $mime);
header('Content-Length: ' . $longitud);
header('Cache-Control: public, max-age=86400');

$fp = fopen($path, 'rb');
if ($fp === false) {
    http_response_code(500);
    exit;
}
if ($inicio > 0) fseek($fp, $inicio);
$restante = $longitud;
while ($restante > 0 && !feof($fp)) {
    $trozo = fread($fp, min(65536, $restante));
    if ($trozo === false) break;
    echo $trozo;
    $restante -= strlen($trozo);
}
fclose($fp);
exit;
