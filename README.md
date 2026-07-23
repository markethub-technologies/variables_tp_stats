# Tasa Plazos Spot — análisis de order-flow (junio 2026)

Análisis **autónomo** del order-flow de la estrategia Tasa Plazos Spot (TP) para
**AL30 / AL30C / AL30D**, junio 2026. Junta las variables originales del pipe
(microestructura del libro + tasas + latencias) con variables nuevas de
comportamiento del bot (pasivo/agresivo, IOC, ciclo de órdenes, PPT/OTC, tasas
ejecutadas), a nivel **(ticker, fecha)**.

## Contenido

| Archivo | Qué es |
|---|---|
| [`analisis_tp_orderflow.py`](analisis_tp_orderflow.py) | **Análisis standalone.** Corre sobre `data/`, imprime tablas y guarda figuras. Sólo requiere numpy/pandas/matplotlib. |
| [`diccionario_variables.html`](diccionario_variables.html) | **Diccionario de variables**: definición, tabla de origen y **query exacta** de cada variable. |
| [`diccionario_variables_originales.html`](diccionario_variables_originales.html) | Detalle de las variables originales del pipe (MKT_/SPREAD_/LAT_). |
| `data/` | Datos ya construidos (SQLite + CSV). |
| `notebooks/tp_orderflow_junio.ipynb` | El mismo análisis en notebook (interactivo). |
| `pipeline_referencia/` | Scripts que construyen los datos desde ClickHouse + Google Sheets. **No** son standalone (ver nota adentro); están para documentar de dónde sale cada variable. |

## Datos (`data/`)

- **`features_tp_combined_junio.db`** — dataset principal (SQLite, tabla `features`): 62 filas × 99 columnas.
- **`tp_statistics_catalog.csv`** — las 64 `statistics_type` que emite la estrategia.
- **`tp_order_cycle_junio.csv`** — ciclo de vida de órdenes agregado de junio.

## Cómo correr el análisis

```bash
pip install -r requirements.txt
python analisis_tp_orderflow.py            # tablas por consola + figuras/*.png
python analisis_tp_orderflow.py --no-figuras   # sólo tablas
```

Las figuras se guardan en `figuras/`. Para verlo interactivo, abrí el notebook.

## Notas de interpretación

- **PnL en moneda nativa**: `BOT_PNL` es AL30 en pesos, AL30D en dólar MEP, AL30C en cable.
  No sumar entre tickers sin tipo de cambio.
- **AL30C no loguea** `best/worst_*_rate` en `strategy_statistics` → sus `BOT_RATE_*` quedan en NaN.
- Universo y cuentas: AL30→10410 (pesos), AL30C→10709 (cable), AL30D→10302 (dólar MEP).
