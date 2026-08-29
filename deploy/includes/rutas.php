<?php
/**
 * rutas.php — Resolución compartida de rutas de archivos multimedia.
 *
 * Los snapshots guardan la ruta de cada medio de dos formas posibles:
 *   - Deploy (--transcode / default): ruta web-relativa 'media/<carpeta>/<archivo>'.
 *   - Snapshot local (--snapshot-local): ruta ABSOLUTA local (ej: F:\...).
 *
 * flujos_resolver_archivo() devuelve la ruta real que existe en disco, probando
 * ambas candidatas contra las raíces conocidas (FLUJOS_ROOT, la raíz del sitio y
 * la raíz del proyecto). La usan servir_medio.php (para servir el binario) y
 * medios_filtrados.php (para saber si un 360° es "disponible"), garantizando que
 * ambos coincidan.
 */

if (!function_exists('flujos_segmento')) {
    function flujos_segmento($s) {
        // Normaliza separadores a '/' y quita slashes/espacios al inicio y fin.
        return str_replace('\\', '/', trim((string)$s, "/\\ \t\n\r\0\x0B"));
    }
}

if (!function_exists('flujos_resolver_archivo')) {
    /**
     * Resuelve la ruta real (existente en disco) de un medio, o null si no existe.
     *
     * @param string|null $ruta_absoluta Ruta guardada en la DB (absoluta o media/...).
     * @param string|null $carpeta       Carpeta del medio (ej: photos, voice_messages).
     * @param string|null $archivo       Nombre del archivo.
     * @return string|null
     */
    function flujos_resolver_archivo($ruta_absoluta, $carpeta, $archivo) {
        $raices = [
            getenv('FLUJOS_ROOT') ? rtrim(getenv('FLUJOS_ROOT'), '/\\') : null,
            __DIR__ . '/..',      // raíz del sitio web (deploy/media/ junto al resto)
            __DIR__ . '/../..',   // raíz del proyecto Flujos (DBs viejas con n\telegram\...)
        ];

        // Candidatas:
        //  1) la ruta guardada (absoluta local en snapshot-local, media/... en deploy)
        //  2) web-relativa media/<carpeta>/<archivo> (funciona en hosting)
        $candidatas = [];
        if ($ruta_absoluta !== null && $ruta_absoluta !== '') {
            $candidatas[] = flujos_segmento($ruta_absoluta);
        }
        $carpetaLimpia = flujos_segmento($carpeta);
        $archivoLimpio = str_replace('\\', '/', (string)$archivo);
        if ($archivoLimpio !== '') {
            $candidatas[] = 'media/' . ($carpetaLimpia !== '' ? $carpetaLimpia . '/' : '') . $archivoLimpio;
        }

        foreach ($candidatas as $cand) {
            if ($cand === '') continue;
            if (file_exists($cand)) return $cand;
            foreach ($raices as $raiz) {
                if (!$raiz) continue;
                $chk = $raiz . '/' . ltrim($cand, '/');
                if (file_exists($chk)) return $chk;
            }
        }
        return null;
    }
}