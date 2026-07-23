#!/usr/bin/env python3
"""
Genera dos CSV auxiliares para el notebook tp_orderflow_junio.ipynb:
  data/tp_statistics_catalog.csv  -> las 64 statistics_type que guarda la estrategia
                                     (con categoria, conteo por ticker, y si ya se usa)
  data/tp_order_cycle_junio.csv   -> ciclo de vida de ordenes (execution_type x order_status
                                     x time_in_force) por ticker, agregado junio 2026
"""
import sys
from pathlib import Path
import pandas as pd

from src.download.clickhouse import _get_client
from build_features_tp_orderflow import mapa_ejecuciones, JUN_INI, JUN_FIN, TICKERS

ROOT = Path(__file__).parent
DIA = "2026-06-16"   # dia representativo para el catalogo de statistics

RATES = {"best_lend_rate","worst_lend_rate","best_borrow_rate","worst_borrow_rate",
         "worst_lend_rate_aggressive","worst_borrow_rate_aggressive"}


def categoria(t: str) -> str:
    if t in RATES or t.endswith("_rate"):        return "tasas (lend/borrow)"
    if t.startswith("a_") or t.startswith("agg_"): return "agresivo"
    if t.startswith("p_") or t.startswith("passive_"): return "pasivo"
    if t.startswith("b_") or t.startswith("bal_"): return "balanceo"
    if t.startswith("rev_"):                     return "reversion"
    if "liquidity" in t:                         return "liquidez"
    return "generales"


def main():
    c = _get_client()

    # mapa live -> ticker (solo dia representativo, para el catalogo)
    live_to_ticker = {}
    for tk in TICKERS:
        _, lids, _, _ = mapa_ejecuciones(c, tk)
        for lid in lids:
            live_to_ticker[lid] = tk
    lid_in = "','".join(live_to_ticker)

    # --- 1. Catalogo de statistics_type (conteo por live el dia DIA) ---
    print(f"catalogo statistics ({DIA})...", flush=True)
    st = c.query_df(f"""
        SELECT statistics_type, live_id, count() n
        FROM prod_strategy.strategy_statistics
        WHERE partition_date='{DIA}' AND live_id IN ('{lid_in}')
        GROUP BY statistics_type, live_id""")
    st["ticker"] = st["live_id"].map(live_to_ticker)
    piv = st.pivot_table(index="statistics_type", columns="ticker", values="n",
                         aggfunc="sum", fill_value=0)
    piv.columns = [f"n_{c_}" for c_ in piv.columns]
    piv = piv.reset_index()
    piv["categoria"] = piv["statistics_type"].map(categoria)
    piv["usado_en_features"] = piv["statistics_type"].isin(RATES)
    piv = piv.sort_values(["categoria", "statistics_type"])
    out1 = ROOT / "data" / "tp_statistics_catalog.csv"
    piv.to_csv(out1, index=False)
    print(f"  {len(piv)} tipos -> {out1}")

    # --- 2. Ciclo de ordenes junio por ticker ---
    print("ciclo de ordenes junio...", flush=True)
    filas = []
    for tk in TICKERS:
        _, _, accs, secs = mapa_ejecuciones(c, tk)
        acc_in = "','".join(a for a in accs if a); sec_in = "','".join(secs)
        d = c.query_df(f"""
            SELECT toString(execution_type) execution_type, toString(order_status) order_status,
                   toString(time_in_force) time_in_force, count() n, sum(last_quantity) qty
            FROM prod_strategy.connector_execution_report
            WHERE partition_date BETWEEN '{JUN_INI}' AND '{JUN_FIN}'
              AND account IN ('{acc_in}') AND security_id IN ('{sec_in}')
            GROUP BY execution_type, order_status, time_in_force""")
        d.insert(0, "ticker", tk)
        filas.append(d)
    cyc = pd.concat(filas, ignore_index=True).sort_values(["ticker", "n"], ascending=[True, False])
    out2 = ROOT / "data" / "tp_order_cycle_junio.csv"
    cyc.to_csv(out2, index=False)
    print(f"  {len(cyc)} filas -> {out2}")


if __name__ == "__main__":
    sys.exit(main() or 0)
