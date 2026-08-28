# Corrección de timestamps de videos 360° (post-ingesta)

Temporario. Repara el desfase de relojes Insta360 documentado en
`docs/discrepancia_horarios_camaras.md`.

## Problema

`QuickTime:CreateDate` embebido es **UTC**. La ingesta lo trataba como
`ART` (`UTC-3`) → `+3h`. El `filename` `VID_YYYYMMDD_HHMMSS` lleva relojes
`LA+7h` / `UTC+1` / reconfigurada → hasta `±7h` si se usa como fallback.

## Script

`scripts/corregir_timestamp_360.py` — post-ingesta. Re-lee `CreateDate` del
archivo, recalcula `timestamp_utc = CreateDate (UTC)` y `timestamp_original =
UTC-3h`, limpia `ubicacion_video*` para forzar re-ubicación.

```bash
# Previsualizar (no escribe)
python scripts/corregir_timestamp_360.py --dry-run
python scripts/corregir_timestamp_360.py --dry-run --json | jq .

# Aplicar solo pendientes (ya corregidos con 360_UTC y delta ≤10m se omiten)
python scripts/corregir_timestamp_360.py --mode skip --reubicar

# Re-corregir todos los 360 (p.ej. tras re-remuxar)
python scripts/corregir_timestamp_360.py --mode update --reubicar

# Desde cero (con backup automático)
python scripts/corregir_timestamp_360.py --mode replace --reubicar
```

`--reubicar` encadena `ubicar_videos_gpx --solo-360 --mode update` (interpola
contra GPX; respeta gaps `>1800s`).

## Flujo recomendado en otra PC (mismos videos, casi mismos)

```bash
python flujos.py ingest --root <carpeta>          # 1) ingerir
python flujos.py corregir-360 --dry-run           # 2) validar desfases
python flujos.py corregir-360 --reubicar          # 3) corregir + re-ubicar
python flujos.py gradient && python flujos.py astronomia
```

## TUI

`python flujos.py` → `7) Scripts temporarios` → `1) Corregir timestamps 360`.

## Cuándo borrar

Cuando `ingest.py` corrija 360 nativamente, eliminar el script y la opción 7.
