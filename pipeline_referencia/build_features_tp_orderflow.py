#!/usr/bin/env python3
"""
Build de features de EXPLICABILIDAD de order-flow para Tasa Plazos Spot (TP),
leyendo variables desde ClickHouse (prod_strategy) y el PnL desde los Google
Sheets del trader. Solo JUNIO 2026, instrumentos AL30 / AL30C / AL30D.

Variables nuevas (a nivel ticker-dia) que NO estaban en features_tp_junio.db:
  Order-flow ejecutado (connector_execution_report, execution_type=TRADE):
    BOT_FILL_VOL              volumen nominal operado por el bot
    BOT_N_FILLS              cantidad de fills
    BOT_AGGRESSIVE_VOL_RATIO  % del volumen fill que fue IOC (agresivo/toma)
    BOT_PASSIVE_VOL_RATIO     % del volumen fill que fue DAY (pasivo/cotiza)
  Intencion de ordenes (strategy_on_order_event, status=PENDING_NEW):
    BOT_N_ORDERS             ordenes enviadas
    BOT_IOC_ORDER_RATIO       % de ordenes enviadas con TIF=IOC
    BOT_ORD_PASIVO_PCT        % ordenes causa pipeline_pasivo_*
    BOT_ORD_AGRESIVO_PCT      % ordenes causa pipeline_agresivo
    BOT_ORD_REVERSION_PCT     % ordenes causa pipeline_reversion
    BOT_ORD_BALANCE_PCT       % ordenes causa pipeline_balance_*
  PPT vs OTC (strategy_underlying_holding_query, ultimo snapshot del dia):
    BOT_OTC_QTY_ABS          |otc_quantity| del subyacente
    BOT_OTC_RATIO             % |otc| / (|ppt| + |otc|)  (proxy tenencia bilateral)
  Tasas que ve el bot (strategy_statistics, mediana del dia):
    BOT_RATE_BEST_LEND / WORST_LEND / BEST_BORROW / WORST_BORROW (+ *_AGG)
  Tipo de instrumento (classify):
    TIPO_INSTRUMENTO, CURRENCY, CONTRACT_MULTIPLIER
  PnL del trader (Google Sheet, hoja 'PnL', en MONEDA NATIVA del ticker):
    BOT_PNL

Uso:  python build_features_tp_orderflow.py
      python build_features_tp_orderflow.py --skip-pnl   (no toca Drive)
"""
import argparse
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from src.download.clickhouse import _get_client
from src.data.google_sheets import _drive_service, read_sheet_raw, list_worksheets
from src.instruments import classify

ROOT = Path(__file__).parent
DB_OUT = ROOT / "data" / "features_tp_orderflow_junio.db"

TP = "devs-strat-TasaPlazosSpot"
TICKERS = ["AL30", "AL30C", "AL30D"]

# Moneda -> (fragmento del nombre del sheet, cuenta actual). La cuenta importa:
# pesos hoy corre en 10410 (NO 10907, que es la planilla vieja y da PnL=0).
SHEET_CFG = {
    "ars":  ("CI vs 48",         "10410"),
    "ext":  ("Tasa Cable",       "10709"),
    "usd":  ("Tasa en dolares",  "10302"),
}
TICKER_CCY = {"AL30": "ars", "AL30C": "ext", "AL30D": "usd"}

_MESES = {6: "JUNIO"}
JUN_INI, JUN_FIN = "2026-06-01", "2026-06-30"


# ── PnL desde Google Sheets ───────────────────────────────────────────────────

def _parse_fecha(s):
    try:
        d, m, y = str(s).strip().split("-")
        M = {"ene":1,"feb":2,"mar":3,"abr":4,"may":5,"jun":6,"jul":7,
             "ago":8,"sep":9,"oct":10,"nov":11,"dic":12}
        return pd.Timestamp(int(y), M[m.lower()[:3]], int(d))
    except Exception:
        return pd.NaT


def _parse_val(s):
    s = str(s).replace("$", "").replace(",", "").strip()
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        v = float(s)
    except ValueError:
        return np.nan
    return -v if neg else v


def _resolver_sheet(files, frag, acc, mes_txt):
    """Elige el sheet de junio (frag + mes + cuenta, sin '2025') que tenga hoja 'PnL'."""
    cands = [f for f in files
             if frag in f["name"] and mes_txt in f["name"].upper()
             and acc in f["name"] and "2025" not in f["name"]]
    for f in cands:
        try:
            if "PnL" in list_worksheets(f["id"]):
                return f
        except Exception:
            continue
    return cands[0] if cands else None


def cargar_pnl_junio():
    """Devuelve dict {(ticker, 'YYYY-MM-DD'): pnl} leyendo la hoja 'PnL' de cada sheet."""
    svc = _drive_service()
    files, tok = [], None
    while True:
        r = svc.files().list(
            fields="nextPageToken,files(id,name)", pageSize=200, pageToken=tok,
            q="mimeType='application/vnd.google-apps.spreadsheet' and trashed=false",
            supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        files += r.get("files", []); tok = r.get("nextPageToken")
        if not tok:
            break

    pnl = {}
    for ccy, (frag, acc) in SHEET_CFG.items():
        f = _resolver_sheet(files, frag, acc, _MESES[6])
        if not f:
            print(f"  [PnL {ccy}] no encontre sheet ({frag} {acc})")
            continue
        raw = read_sheet_raw(f["id"], "PnL")
        hdr = raw[0]
        fechas = [_parse_fecha(c) for c in hdr[1:]]
        tickers_ccy = [tk for tk, c in TICKER_CCY.items() if c == ccy]
        got = 0
        for row in raw[1:]:
            tk = str(row[0]).strip()
            if tk not in tickers_ccy:
                continue
            for j, fe in enumerate(fechas, start=1):
                if pd.isna(fe) or fe.month != 6 or j >= len(row):
                    continue
                v = _parse_val(row[j])
                if pd.notna(v):
                    pnl[(tk, fe.strftime("%Y-%m-%d"))] = v
                    got += 1
        print(f"  [PnL {ccy}] {f['name'][:48]:48s} -> {got} puntos")
    return pnl


# ── Variables desde ClickHouse ────────────────────────────────────────────────

def mapa_ejecuciones(c, ticker):
    """Todas las ejecuciones de junio del ticker. Devuelve dict fecha-> (account, [live_ids], [secs])
    y los conjuntos globales de live_ids / accounts / secs para queries mensuales."""
    r = c.query(f"""
        SELECT toString(partition_date) d, live_id, accounts, symbols.security_id
        FROM prod_strategy.strategy_execution_started
        WHERE partition_date BETWEEN '{JUN_INI}' AND '{JUN_FIN}'
          AND project_name = '{TP}'
          AND arrayExists(x -> startsWith(x, '{ticker}-'), symbols.security_id)
        ORDER BY partition_date, timestamp""")
    por_dia, lids, accs, secs = {}, set(), set(), set()
    for d, lid, accounts, symsec in r.result_rows:
        acc = accounts[0] if accounts else None
        s = [x for x in symsec if x.startswith(ticker + "-")]
        e = por_dia.setdefault(d, {"account": acc, "live_ids": [], "secs": set()})
        e["live_ids"].append(lid); e["secs"].update(s)
        lids.add(lid); accs.add(acc); secs.update(s)
    return por_dia, lids, accs, secs


def features_ch_ticker(c, ticker, por_dia, lids, accs, secs):
    """Variables CH rapidas del ticker para junio (exec_report, order_event, OTC).
    Las tasas (strategy_statistics) se calculan aparte, en una sola pasada global."""
    if not por_dia:
        return pd.DataFrame()
    lid_in = "','".join(lids); acc_in = "','".join(a for a in accs if a); sec_in = "','".join(secs)

    base = pd.DataFrame([
        {"fecha": d, "ticker": ticker, "account": e["account"], "n_live": len(e["live_ids"])}
        for d, e in sorted(por_dia.items())
    ]).set_index("fecha")

    # 1. Order-flow ejecutado + ciclo de ordenes (por dia, una sola query).
    #    El filtro por security_id aisla el instrumento aunque la cuenta este compartida.
    #    TRADE=fill; REPLACED=recotizacion; EXPIRED (IOC)=intento agresivo que no fillo;
    #    REJECTED=orden rechazada; PARTIALLY_FILLED vs FILLED=calidad del fill.
    ef = c.query_df(f"""
        SELECT toString(partition_date) fecha,
               -- volumen fill y pasivo/agresivo
               sumIf(last_quantity, execution_type='TRADE' AND time_in_force='IMMEDIATE_OR_CANCEL') agg,
               sumIf(last_quantity, execution_type='TRADE' AND time_in_force='DAY') pas,
               sumIf(last_quantity, execution_type='TRADE') tot,
               countIf(execution_type='TRADE') n,
               -- volumen por pata (CI=0001 / 24hs=0002) y por lado
               sumIf(last_quantity, execution_type='TRADE' AND position(security_id, '-0001-')>0) vol_ci,
               sumIf(last_quantity, execution_type='TRADE' AND position(security_id, '-0002-')>0) vol_24,
               sumIf(last_quantity, execution_type='TRADE' AND side='BUY')  vol_buy,
               sumIf(last_quantity, execution_type='TRADE' AND side='SELL') vol_sell,
               -- calidad del fill
               countIf(execution_type='TRADE' AND order_status='FILLED')           n_filled,
               countIf(execution_type='TRADE' AND order_status='PARTIALLY_FILLED')  n_partial,
               -- ciclo de vida / fallidas
               countIf(execution_type='REPLACED')  n_replace,
               countIf(execution_type='REJECTED')  n_rejected,
               countIf(execution_type='EXPIRED')   n_expired,
               countIf(execution_type='CANCELED')  n_canceled,
               -- IOC: intentos (TRADE+EXPIRED) vs fills
               countIf(time_in_force='IMMEDIATE_OR_CANCEL' AND execution_type='TRADE')   ioc_trade,
               countIf(time_in_force='IMMEDIATE_OR_CANCEL' AND execution_type='EXPIRED') ioc_expired
        FROM prod_strategy.connector_execution_report
        WHERE partition_date BETWEEN '{JUN_INI}' AND '{JUN_FIN}'
          AND account IN ('{acc_in}') AND security_id IN ('{sec_in}')
        GROUP BY fecha""")
    ef = ef.set_index("fecha") if not ef.empty else ef
    if not ef.empty:
        tot = ef["tot"].replace(0, np.nan)
        base["BOT_FILL_VOL"] = ef["tot"]
        base["BOT_N_FILLS"] = ef["n"]
        base["BOT_AGGRESSIVE_VOL_RATIO"] = 100*ef["agg"]/tot
        base["BOT_PASSIVE_VOL_RATIO"] = 100*ef["pas"]/tot
        # patas y lados (estructura del "loan": colocar/tomar = near/far)
        base["BOT_VOL_CI"] = ef["vol_ci"]
        base["BOT_VOL_24HS"] = ef["vol_24"]
        base["BOT_VOL_BUY"] = ef["vol_buy"]
        base["BOT_VOL_SELL"] = ef["vol_sell"]
        # calidad del fill
        fills = (ef["n_filled"] + ef["n_partial"]).replace(0, np.nan)
        base["BOT_PARTIAL_FILL_RATIO"] = 100*ef["n_partial"]/fills
        # ciclo de vida
        base["BOT_N_REPLACE"] = ef["n_replace"]
        base["BOT_N_REJECTED"] = ef["n_rejected"]
        base["BOT_N_EXPIRED"] = ef["n_expired"]
        base["BOT_N_CANCELED"] = ef["n_canceled"]
        # IOC: de los intentos agresivos, % que consiguio fill
        ioc_try = (ef["ioc_trade"] + ef["ioc_expired"]).replace(0, np.nan)
        base["BOT_IOC_FILL_RATIO"] = 100*ef["ioc_trade"]/ioc_try

    # 2. Intencion de ordenes (por dia, causa, TIF)
    oc = c.query_df(f"""
        SELECT toString(partition_date) fecha, causality, time_in_force, count() n
        FROM prod_strategy.strategy_on_order_event
        WHERE partition_date BETWEEN '{JUN_INI}' AND '{JUN_FIN}'
          AND live_id IN ('{lid_in}') AND status='PENDING_NEW'
        GROUP BY fecha, causality, time_in_force""")
    if not oc.empty:
        tot = oc.groupby("fecha")["n"].sum()
        base["BOT_N_ORDERS"] = tot
        def caus_pct(pref):
            s = oc[oc.causality.str.startswith(pref)].groupby("fecha")["n"].sum()
            return 100*s/tot
        base["BOT_ORD_PASIVO_PCT"] = caus_pct("pipeline_pasivo")
        base["BOT_ORD_AGRESIVO_PCT"] = caus_pct("pipeline_agresivo")
        base["BOT_ORD_REVERSION_PCT"] = caus_pct("pipeline_reversion")
        base["BOT_ORD_BALANCE_PCT"] = caus_pct("pipeline_balance")
        ioc = oc[oc.time_in_force == "IMMEDIATE_OR_CANCEL"].groupby("fecha")["n"].sum()
        base["BOT_IOC_ORDER_RATIO"] = 100*ioc/tot

    return base.reset_index()


def otc_all(c, live_to_ticker):
    """PPT vs OTC para todos los tickers en UNA pasada sobre strategy_underlying_holding_query
    (keyed por live_id, sin ambiguedad de cuenta). Ultimo snapshot del dia por live/asset.
    Devuelve df fecha/ticker con BOT_OTC_QTY_ABS y BOT_OTC_RATIO (=|otc|/(|ppt|+|otc|))."""
    lid_in = "','".join(live_to_ticker)
    hq = c.query_df(f"""
        SELECT fecha, live_id, sum(abs(q)) q, sum(abs(otc)) otc FROM (
          SELECT toString(partition_date) fecha, live_id, asset,
                 argMax(quantity, timestamp) q, argMax(otc_quantity, timestamp) otc
          FROM prod_strategy.strategy_underlying_holding_query
          PREWHERE asset LIKE 'AL30%'
          WHERE partition_date BETWEEN '{JUN_INI}' AND '{JUN_FIN}' AND live_id IN ('{lid_in}')
          GROUP BY fecha, live_id, asset)
        GROUP BY fecha, live_id""")
    if hq.empty:
        return pd.DataFrame(columns=["fecha", "ticker"])
    hq["ticker"] = hq["live_id"].map(live_to_ticker)
    g = hq.groupby(["fecha", "ticker"]).agg(q=("q", "sum"), otc=("otc", "sum")).reset_index()
    g["BOT_OTC_QTY_ABS"] = g["otc"]
    g["BOT_OTC_RATIO"] = 100*g["otc"]/(g["q"]+g["otc"]).replace(0, np.nan)
    return g[["fecha", "ticker", "BOT_OTC_QTY_ABS", "BOT_OTC_RATIO"]]


# Nombres de las tasas del bot (statistics_type -> columna).
# BOT_RATE_*        = umbrales de decision del bot (best/worst).
# BOT_MKT_*_RATE    = la TASA DE MERCADO que el bot computo (a_f3_market_*), en la misma
#                     escala que MKT_TASA_* del libro -> comparable manzana-con-manzana.
_RATE_RENAME = {
    "best_lend_rate":"BOT_RATE_BEST_LEND", "worst_lend_rate":"BOT_RATE_WORST_LEND",
    "best_borrow_rate":"BOT_RATE_BEST_BORROW", "worst_borrow_rate":"BOT_RATE_WORST_BORROW",
    "worst_lend_rate_aggressive":"BOT_RATE_WORST_LEND_AGG",
    "worst_borrow_rate_aggressive":"BOT_RATE_WORST_BORROW_AGG",
    "a_f3_market_lend_rate":"BOT_MKT_LEND_RATE",
    "a_f3_market_borrow_rate":"BOT_MKT_BORROW_RATE",
}


def rates_all(c, live_to_ticker):
    """Tasas que ve el bot para TODOS los tickers en UNA sola pasada mensual sobre
    strategy_statistics (378M filas/dia; su ORDER BY no incluye live_id, asi que
    filtrarlo no poda -> conviene escanear el mes una vez, no 1 por ticker).
    PREWHERE statistics_type lee primero la columna barata. Devuelve df fecha/ticker/BOT_RATE_*."""
    lid_in = "','".join(live_to_ticker)
    rt = c.query_df(f"""
        SELECT toString(partition_date) fecha, live_id, statistics_type t, median(value) m
        FROM prod_strategy.strategy_statistics
        PREWHERE statistics_type IN ('best_lend_rate','worst_lend_rate','best_borrow_rate',
            'worst_borrow_rate','worst_lend_rate_aggressive','worst_borrow_rate_aggressive',
            'a_f3_market_lend_rate','a_f3_market_borrow_rate')
        WHERE partition_date BETWEEN '{JUN_INI}' AND '{JUN_FIN}' AND live_id IN ('{lid_in}')
        GROUP BY fecha, live_id, t""")
    if rt.empty:
        return pd.DataFrame(columns=["fecha", "ticker"])
    rt["ticker"] = rt["live_id"].map(live_to_ticker)
    # mediana de las medianas por-live cuando hay >1 live/ticker/dia (normalmente 1)
    agg = rt.groupby(["fecha", "ticker", "t"])["m"].median().reset_index()
    piv = agg.pivot_table(index=["fecha", "ticker"], columns="t", values="m").reset_index()
    return piv.rename(columns=_RATE_RENAME)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-pnl", action="store_true", help="No leer Drive (BOT_PNL=NaN)")
    args = ap.parse_args()

    print("== PnL desde Google Sheets ==")
    pnl = {} if args.skip_pnl else cargar_pnl_junio()

    print("\n== Variables ClickHouse ==", flush=True)
    c = _get_client()

    # Fase 1: mapas de ejecucion por ticker + mapa global live_id -> ticker.
    maps, live_to_ticker = {}, {}
    for tk in TICKERS:
        por_dia, lids, accs, secs = mapa_ejecuciones(c, tk)
        maps[tk] = (por_dia, lids, accs, secs)
        for lid in lids:
            live_to_ticker[lid] = tk

    # Fase 2: queries rapidas por ticker (exec_report, order_event, OTC).
    partes = []
    for tk in TICKERS:
        por_dia, lids, accs, secs = maps[tk]
        info = classify(tk, plazo="CI")
        t0 = time.time()
        d = features_ch_ticker(c, tk, por_dia, lids, accs, secs)
        if d.empty:
            print(f"  {tk}: sin datos"); continue
        d["TIPO_INSTRUMENTO"] = info.tipo_name
        d["CURRENCY"] = info.currency
        d["CONTRACT_MULTIPLIER"] = info.factor_pnl
        d["BOT_PNL"] = [pnl.get((tk, f), np.nan) for f in d["fecha"]]
        partes.append(d)
        print(f"  {tk} ({info.tipo_name}/{info.currency}): {len(d)} dias  {time.time()-t0:.1f}s", flush=True)

    df = pd.concat(partes, ignore_index=True)

    # Fase 3: OTC y tasas en pasadas globales (tablas enormes cuyo ORDER BY no incluye
    # live_id; conviene escanear el mes una sola vez para los 3 tickers juntos).
    t0 = time.time()
    otc = otc_all(c, live_to_ticker)
    print(f"  OTC (global): {len(otc)} filas  {time.time()-t0:.1f}s", flush=True)
    if not otc.empty:
        df = df.merge(otc, on=["fecha", "ticker"], how="left")

    t0 = time.time()
    rates = rates_all(c, live_to_ticker)
    print(f"  tasas (global): {len(rates)} filas  {time.time()-t0:.1f}s", flush=True)
    if not rates.empty:
        df = df.merge(rates, on=["fecha", "ticker"], how="left")
    orden = ["fecha","ticker","account","TIPO_INSTRUMENTO","CURRENCY","CONTRACT_MULTIPLIER",
             "n_live","BOT_PNL"]
    cols = orden + [c for c in df.columns if c not in orden]
    df = df[cols]

    DB_OUT.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_OUT)
    df.to_sql("features", con, if_exists="replace", index=False)
    con.close()
    print(f"\nGuardado: {len(df)} filas x {len(df.columns)} cols -> {DB_OUT}")
    print(df[["fecha","ticker","BOT_PNL","BOT_AGGRESSIVE_VOL_RATIO","BOT_IOC_ORDER_RATIO",
              "BOT_OTC_RATIO"]].to_string())


if __name__ == "__main__":
    sys.exit(main() or 0)
