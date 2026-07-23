#!/usr/bin/env python3
"""
Une en un solo dataset las variables ORIGINALES del pipe TP (features_tp_junio.db:
MKT_*, MKT_TASA_*, SPREAD_*_CAUCION, MKT_INTRADAY_VOLATILITY_*, LAT_*) con las
CANDIDATAS NUEVAS de order-flow (features_tp_orderflow_junio.db: BOT_* pasivo/agresivo,
IOC, ciclo de ordenes, PPT/OTC, tasas del bot, tipo).

Clave de join: (ticker, fecha). El PnL sale del db nuevo (BOT_PNL corregido, sheet 10410);
se descarta el BOT_PNL viejo del db original (AL30 pesos venia en 0).

Salida: data/features_tp_combined_junio.db  (tabla 'features').
Uso:  python build_features_tp_combined.py
"""
import sqlite3
import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).parent
DATA = ROOT / "data"
# features_tp.db tiene el pipe original a mes completo (jun 1-30); features_tp_junio.db solo 1-17.
ORIG = DATA / "features_tp.db"
NEW = DATA / "features_tp_orderflow_junio.db"
OUT = DATA / "features_tp_combined_junio.db"


def main():
    # Original: instrumento "AL30/CI" -> ticker "AL30"; fecha int 20260601 -> "2026-06-01"
    orig = pd.read_sql("SELECT * FROM features WHERE fecha BETWEEN 20260601 AND 20260630",
                       sqlite3.connect(ORIG))
    orig["ticker"] = orig["instrumento"].str.split("/").str[0]
    orig["fecha"] = pd.to_datetime(orig["fecha"].astype(str), format="%Y%m%d").dt.strftime("%Y-%m-%d")
    orig = orig.drop(columns=[c for c in ["instrumento", "BOT_PNL"] if c in orig.columns])

    new = pd.read_sql("SELECT * FROM features", sqlite3.connect(NEW))  # fecha ya "YYYY-MM-DD"

    # merge outer: nos quedamos con todas las fechas del db nuevo (mes completo) + las originales
    comb = new.merge(orig, on=["ticker", "fecha"], how="left", suffixes=("", "_orig"))

    # ordenar columnas: meta, PnL, luego BOT_* (nuevas), luego MKT_/SPREAD_/LAT_ (originales)
    meta = ["fecha", "ticker", "account", "TIPO_INSTRUMENTO", "CURRENCY", "CONTRACT_MULTIPLIER",
            "n_live", "BOT_PNL"]
    bot = sorted([c for c in comb.columns if c.startswith("BOT_") and c not in meta])
    orig_cols = sorted([c for c in comb.columns if c not in meta + bot])
    comb = comb[[c for c in meta if c in comb.columns] + bot + orig_cols]

    con = sqlite3.connect(OUT)
    comb.to_sql("features", con, if_exists="replace", index=False)
    con.close()

    n_orig = sum(1 for c in orig_cols if c.startswith(("MKT", "SPREAD", "LAT")))
    n_bot = len(bot)
    print(f"combinado: {len(comb)} filas x {len(comb.columns)} cols -> {OUT}")
    print(f"  originales (MKT/SPREAD/LAT): {n_orig} | candidatas nuevas (BOT_*): {n_bot}")
    cov = comb.groupby("ticker").apply(
        lambda d: pd.Series({
            "dias": len(d),
            "con_originales": int(d[[c for c in orig_cols if c.startswith("MKT_TASA")][0]].notna().sum())
            if any(c.startswith("MKT_TASA") for c in orig_cols) else 0,
            "con_pnl": int(d["BOT_PNL"].notna().sum()),
        }), include_groups=False)
    print(cov.to_string())


if __name__ == "__main__":
    sys.exit(main() or 0)
