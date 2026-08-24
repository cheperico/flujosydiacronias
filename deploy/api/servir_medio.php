<?php
/**
 * Sirve un archivo multimedia por ID.
 * GET: ?id=123
 */
require_once __DIR__ . '/../includes/db.php';
$pdo = db();

$id = isset($_GET['id']) ? (int)$_GET['id'] : 0;
if ($id <= 0) { http_response_code(400); echo "ID invalido"; exit; }

$stmt = $pdo->prepare("SELECT ruta_absoluta, tipo, carpeta, archivo FROM medios WHERE id = ?");
$stmt->execute([$id]);
$row = $stmt->fetch(PDO::FETCH_ASSOC);

if (!$row || !$row['ruta_absoluta']) {
    http_response_code(404);
    echo "Archivo no encontrado";
    exit;
}

// Raíces contra las que resolver rutas (los archivos viven en media/ del deploy).
$raices = [
    getenv('FLUJOS_ROOT') ? rtrim(getenv('FLUJOS_ROOT'), '/\\') : null,
    __DIR__ . '/..',      // raíz del sitio web (deploy/media/ junto al resto)
    __DIR__ . '/../..',   // raíz del proyecto Flujos (DBs viejas con n\telegram\...)
];

function _flujos_segmento($s) {
    // Normaliza separadores a '/' y quita slashes/spacios al inicio y fin.
    return str_replace('\\', '/', trim((string)$s, "/\\ \t\n\r\0\x0B"));
}

// Candidatas a la ruta real:
//  1) la ruta guardada en la DB (absoluta Windows en snapshots --snapshot-local,
//     web-relativa 'media/...' en snapshots de deploy),
//  2) web-relativa media/<carpeta>/<archivo>: siempre que el deploy haya copiado
//     los medios a media/, funciona en hosting aunque la DB guarde rutas locales.
$candidatas = [];
if ($row['ruta_absoluta'] !== null && $row['ruta_absoluta'] !== '') {
    $candidatas[] = _flujos_segmento($row['ruta_absoluta']);
}
$carpeta = _flujos_segmento($row['carpeta']);
$archivo = str_replace('\\', '/', (string)$row['archivo']);
if ($archivo !== '') {
    $candidatas[] = 'media/' . ($carpeta !== '' ? $carpeta . '/' : '') . $archivo;
}

$path = null;
foreach ($candidatas as $cand) {
    if ($cand === '') continue;
    if (file_exists($cand)) { $path = $cand; break; }
    foreach ($raices as $raiz) {
        if (!$raiz) continue;
        $chk = $raiz . '/' . ltrim($cand, '/');
        if (file_exists($chk)) { $path = $chk; break 2; }
    }
}

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

header('Content-Type: ' . $mime);
header('Content-Length: ' . filesize($path));
header('Cache-Control: public, max-age=86400');
readfile($path);
