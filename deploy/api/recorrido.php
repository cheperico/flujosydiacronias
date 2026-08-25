<?php
// Devuelve todos los puntos ordenados cronológicamente con colores dominantes
// para dibujar la línea "recorrido" en el canvas.
header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
require_once __DIR__ . '/../includes/db.php';
$pdo = db();

$sql = "SELECT id, archivo, carpeta, tipo, ruta_relativa,
               fecha, hora,
               color_1, color_1_hex,
               color_2, color_2_hex,
               color_3, color_3_hex,
               latitud, longitud,
               provincia, municipio, descripcion, keywords
        FROM medios
        ORDER BY fecha, hora, id";

$stmt = $pdo->query($sql);
$puntos = $stmt->fetchAll(PDO::FETCH_ASSOC);

// Hex representativo por nombre (paleta canónica): el swatch del chip debe
// coincidir con el nombre de la categoría. El hex dominante bruto de las fotos
// suele ser oscuro/apagado y no coincide visualmente con el nombre (ej: "rosa"
// → beige, "gris" → teal). Se usa la misma paleta Material del fallback en
// js/app.js para que el chip se vea igual con y sin API.
$HEX_CANONICO = [
    'azul'     => '#1976d2',
    'negro'    => '#212121',
    'gris'     => '#757575',
    'verde'    => '#388e3c',
    'marrón'   => '#5d4037',
    'amarillo' => '#fbc02d',
    'rojo'     => '#d32f2f',
    'rosa'     => '#e91e63',
    'violeta'  => '#7b1fa2',
    'blanco'   => '#f5f5f5',
    'naranja'  => '#f57c00',
];

// Contar slots por nombre (color_1/2/3) para ordenar por frecuencia real
$sqlColores = "SELECT nombre, COUNT(*) AS total FROM (
                 SELECT color_1 AS nombre FROM medios WHERE color_1 IS NOT NULL
                 UNION ALL
                 SELECT color_2 FROM medios WHERE color_2 IS NOT NULL
                 UNION ALL
                 SELECT color_3 FROM medios WHERE color_3 IS NOT NULL
               ) GROUP BY nombre";
$stmtColores = $pdo->query($sqlColores);
$coloresDisponibles = [];
foreach ($stmtColores->fetchAll(PDO::FETCH_ASSOC) as $f) {
    $nom = $f['nombre'];
    $coloresDisponibles[] = [
        'nombre' => $nom,
        'hex'    => $HEX_CANONICO[$nom] ?? '#607d8b',
        'total'  => (int)$f['total']
    ];
}
// Ordenar por cantidad descendente
usort($coloresDisponibles, function($a, $b) {
    return $b['total'] - $a['total'];
});

echo json_encode([
    'total'    => count($puntos),
    'puntos'   => $puntos,
    'colores'  => $coloresDisponibles
], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
