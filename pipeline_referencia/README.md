# pipeline_referencia/ — cómo se construyeron los datos

Estos scripts generan los archivos de `data/` desde **ClickHouse** (`prod_strategy`)
y los **Google Sheets** del trader. Están acá para **documentar el origen y las queries**
de cada variable — no son standalone: dependen de la infraestructura interna
(`src.download.clickhouse`, `src.data.google_sheets`, `src.instruments`) y de credenciales.

Para leer el análisis no hace falta correrlos: los datos ya están en `data/`.

## Orden de construcción

1. `build_features_tp_orderflow.py` → `features_tp_orderflow_junio.db`
   Variables nuevas `BOT_*` (order-flow, IOC, ciclo, PPT/OTC, tasas del bot) + `BOT_PNL` del Sheet.
2. `build_tp_catalogos.py` → `tp_statistics_catalog.csv` + `tp_order_cycle_junio.csv`
3. El pipe original `tp_stats.py` → `features_tp.db` (variables `MKT_*`, `SPREAD_*`, `LAT_*`)
4. `build_features_tp_combined.py` → `features_tp_combined_junio.db` (join por `ticker`, `fecha`)

Las queries exactas de cada variable están transcriptas en `../diccionario_variables.html`.
