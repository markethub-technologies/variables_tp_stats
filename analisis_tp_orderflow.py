#!/usr/bin/env python3
"""
Análisis de order-flow de Tasa Plazos Spot (TP) — junio 2026, AL30 / AL30C / AL30D.

STANDALONE: sólo necesita numpy, pandas y matplotlib. Lee los datos ya construidos
de la carpeta data/ (no toca ClickHouse ni Google Sheets). Las queries y tablas de
origen de cada variable están documentadas en diccionario_variables.html y en los
scripts de pipeline_referencia/.

Junta las variables ORIGINALES del pipe (microestructura + tasas del libro + latencias)
con las CANDIDATAS NUEVAS de order-flow (comportamiento del bot), por (ticker, fecha).

Uso:
    python analisis_tp_orderflow.py                # imprime tablas y guarda figuras/
    python analisis_tp_orderflow.py --no-figuras   # sólo las tablas por consola

Salida:
    figuras/4_ciclo_ordenes.png
    figuras/5_pasivo_agresivo.png
    figuras/6_ppt_otc.png
    figuras/7_tasas.png
    figuras/8_bot_vs_libro.png
    figuras/9_correlacion_pnl.png
"""
import argparse
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
DATA = ROOT / "data"
FIG = ROOT / "figuras"

COLOR = {"AL30": "#264653", "AL30C": "#2a9d8f", "AL30D": "#e9c46a"}
TKS = ["AL30", "AL30C", "AL30D"]


def sep(titulo):
    print("\n" + "=" * 78)
    print(titulo)
    print("=" * 78)


def cargar():
    df = pd.read_sql("SELECT * FROM features",
                     sqlite3.connect(DATA / "features_tp_combined_junio.db"))
    df["fecha"] = pd.to_datetime(df["fecha"])
    return df


# ── 1. Racconto: variables por familia ────────────────────────────────────────
def familia(c):
    if c.startswith("MKT_TASA"): return "orig: tasas"
    if c.startswith("MKT_INTRADAY"): return "orig: vol tasa"
    if c.startswith("MKT"): return "orig: microestructura"
    if c.startswith("SPREAD"): return "orig: spread caución"
    if c.startswith("LAT"): return "orig: latencias"
    if c.startswith("BOT_RATE"): return "nueva: tasas bot"
    if c.startswith("BOT_ORD") or "AGGRESSIVE" in c or "PASSIVE" in c: return "nueva: pasivo/agresivo"
    if "IOC" in c: return "nueva: IOC"
    if c.startswith("BOT_N") or "PARTIAL" in c: return "nueva: ciclo órdenes"
    if "OTC" in c or "VOL" in c or "FILL_VOL" in c: return "nueva: PPT/OTC/vol"
    return "meta/otros"


def seccion_1_racconto(df, orig_vars, bot_vars):
    sep("1. Racconto — originales vs candidatas nuevas")
    print(f"dataset combinado: {df.shape[0]} filas x {df.shape[1]} cols")
    print(f"  originales (MKT/SPREAD/LAT): {len(orig_vars)}  |  candidatas nuevas (BOT_*): {len(bot_vars)}")
    print(f"  filas por ticker: {df['ticker'].value_counts().to_dict()}")
    fam = pd.Series([familia(c) for c in df.columns]).value_counts()
    print("\nvariables por familia:")
    print(fam.to_string())


# ── 2. Catálogo de statistics_type ────────────────────────────────────────────
def seccion_2_catalogo():
    sep("2. Catálogo de statistics_type de la estrategia")
    cat = pd.read_csv(DATA / "tp_statistics_catalog.csv")
    print("64 series emitidas por la estrategia (strategy_statistics). Hoy sólo usamos las 6 de tasas.")
    print((cat.groupby("categoria")
              .agg(n_tipos=("statistics_type", "count"), usados=("usado_en_features", "sum"))
              .sort_values("n_tipos", ascending=False)).to_string())
    print("\nTasas por ticker (AL30C no loguea best/worst_*_rate):")
    print(cat[cat.categoria.str.startswith("tasas")]
          [["statistics_type", "n_AL30", "n_AL30C", "n_AL30D", "usado_en_features"]]
          .to_string(index=False))


# ── 3. Panorama del dataset ────────────────────────────────────────────────────
def seccion_3_panorama(df, g, orig_vars):
    sep("3. Panorama del dataset combinado")
    col_tasa = next((c for c in orig_vars if c.startswith("MKT_TASA")), None)
    cov = g.apply(lambda d: pd.Series({
        "dias": len(d),
        "con_originales": int(d[col_tasa].notna().sum()) if col_tasa else 0,
        "con_PnL": int(d["BOT_PNL"].notna().sum()),
    }), include_groups=False)
    print("cobertura:")
    print(cov.to_string())
    vistas = ["BOT_PNL", "BOT_FILL_VOL", "BOT_AGGRESSIVE_VOL_RATIO", "BOT_IOC_FILL_RATIO",
              "BOT_PARTIAL_FILL_RATIO", "BOT_OTC_RATIO", "MKT_SPREAD_TICKS", "MKT_VOLUME_NOM"]
    print("\nmedias por ticker:")
    print(g[[c for c in vistas if c in df.columns]].mean().round(2).T.to_string())


# ── 4. Ciclo de vida de órdenes ────────────────────────────────────────────────
def seccion_4_ciclo(df, g, plt):
    sep("4. Ciclo de vida de las órdenes")
    cyc = pd.read_csv(DATA / "tp_order_cycle_junio.csv")
    tr = cyc[cyc.execution_type == "TRADE"]
    print("Volumen fill por TIF (nominales):")
    print(tr.pivot_table(index="ticker", columns="time_in_force", values="qty",
                         aggfunc="sum", fill_value=0).round(0).to_string())
    if plt is None:
        return
    x = np.arange(len(TKS))
    fig, axes = plt.subplots(1, 3, figsize=(15, 4))
    part = g["BOT_PARTIAL_FILL_RATIO"].mean().reindex(TKS)
    axes[0].bar(x, 100 - part, color="#2a9d8f", label="FILLED")
    axes[0].bar(x, part, bottom=100 - part, color="#e9c46a", label="PARTIALLY_FILLED")
    axes[0].set_title("Calidad del fill (%)", fontsize=10); axes[0].legend(fontsize=8)
    axes[1].bar(x, g["BOT_IOC_FILL_RATIO"].mean().reindex(TKS), color="#264653")
    axes[1].set_title("IOC fill ratio (% intentos con fill)", fontsize=10); axes[1].set_ylim(0, 100)
    w = 0.27
    for i, (col, lbl, cl) in enumerate([("BOT_N_REJECTED", "rejected", "#e76f51"),
                                        ("BOT_N_EXPIRED", "expired (IOC)", "#f4a261"),
                                        ("BOT_N_CANCELED", "canceled", "#8ab17d")]):
        axes[2].bar(x + (i - 1) * w, g[col].mean().reindex(TKS), w, label=lbl, color=cl)
    axes[2].set_title("Órdenes no-completadas (media/día)", fontsize=10); axes[2].legend(fontsize=8)
    for ax in axes:
        ax.set_xticks(x); ax.set_xticklabels(TKS); ax.grid(axis="y", alpha=.3)
    plt.tight_layout(); fig.savefig(FIG / "4_ciclo_ordenes.png", dpi=110); plt.close(fig)


# ── 5. Pasivo vs agresivo ──────────────────────────────────────────────────────
def seccion_5_pasivo_agresivo(g, plt):
    sep("5. Pasivo vs agresivo")
    print("Volumen fill agresivo (IOC) vs pasivo (DAY), % — media junio:")
    print(g[["BOT_AGGRESSIVE_VOL_RATIO", "BOT_PASSIVE_VOL_RATIO"]].mean().round(1).reindex(TKS).to_string())
    print("\nÓrdenes enviadas por causa (%):")
    causas = ["BOT_ORD_PASIVO_PCT", "BOT_ORD_AGRESIVO_PCT", "BOT_ORD_REVERSION_PCT", "BOT_ORD_BALANCE_PCT"]
    print(g[causas].mean().round(1).reindex(TKS).to_string())
    if plt is None:
        return
    x = np.arange(len(TKS))
    fig, axes = plt.subplots(1, 2, figsize=(13, 4))
    agg = g["BOT_AGGRESSIVE_VOL_RATIO"].mean().reindex(TKS)
    axes[0].bar(x, 100 - agg, color="#2a9d8f", label="pasivo (DAY)")
    axes[0].bar(x, agg, bottom=100 - agg, color="#e76f51", label="agresivo (IOC)")
    axes[0].set_title("Volumen fill: pasivo vs agresivo (%)", fontsize=10); axes[0].legend(fontsize=8)
    causas = ["BOT_ORD_PASIVO_PCT", "BOT_ORD_AGRESIVO_PCT", "BOT_ORD_REVERSION_PCT", "BOT_ORD_BALANCE_PCT"]
    cl = ["#2a9d8f", "#e76f51", "#e9c46a", "#8ab17d"]; bottom = np.zeros(len(TKS))
    for col, c_ in zip(causas, cl):
        v = g[col].mean().reindex(TKS).values
        axes[1].bar(x, v, bottom=bottom, label=col.replace("BOT_ORD_", "").replace("_PCT", ""), color=c_)
        bottom += v
    axes[1].set_title("Órdenes enviadas por causa (%)", fontsize=10); axes[1].legend(fontsize=8)
    for ax in axes:
        ax.set_xticks(x); ax.set_xticklabels(TKS); ax.grid(axis="y", alpha=.3)
    plt.tight_layout(); fig.savefig(FIG / "5_pasivo_agresivo.png", dpi=110); plt.close(fig)


# ── 6. PPT vs OTC ──────────────────────────────────────────────────────────────
def seccion_6_ppt_otc(df, g, plt):
    sep("6. PPT vs OTC")
    print("PPT operado = BOT_FILL_VOL (todo el exec_report es lit). OTC sólo como tenencia (otc_quantity).")
    print(g[["BOT_FILL_VOL", "BOT_OTC_QTY_ABS", "BOT_OTC_RATIO"]].mean().round(2).reindex(TKS).to_string())
    if plt is None:
        return
    x = np.arange(len(TKS))
    fig, axes = plt.subplots(1, 2, figsize=(13, 4))
    axes[0].bar(x, g["BOT_FILL_VOL"].mean().reindex(TKS), color="#264653")
    axes[0].set_xticks(x); axes[0].set_xticklabels(TKS)
    axes[0].set_title("PPT operado — BOT_FILL_VOL medio/día", fontsize=10)
    for tk in TKS:
        d = df[df.ticker == tk].sort_values("fecha")
        axes[1].plot(d["fecha"], d["BOT_OTC_RATIO"], "o-", ms=4, label=tk, color=COLOR[tk])
    axes[1].set_title("OTC ratio (% tenencia bilateral)", fontsize=10); axes[1].legend(fontsize=8)
    for ax in axes:
        ax.grid(alpha=.3)
    plt.tight_layout(); fig.savefig(FIG / "6_ppt_otc.png", dpi=110); plt.close(fig)


# ── 7. Loans (colocar / tomar) y tasas ──────────────────────────────────────────
def seccion_7_tasas(df, g, plt):
    sep("7. Loans (colocar / tomar) y tasas")
    print("Volumen por pata y lado (media/día, nominales):")
    print(g[["BOT_VOL_CI", "BOT_VOL_24HS", "BOT_VOL_BUY", "BOT_VOL_SELL"]].mean().round(0).reindex(TKS).to_string())
    rate_cols = [c for c in df.columns if c.startswith("BOT_RATE_")]
    print("\nTasas del bot (media junio) — AL30C sin datos:")
    print(g[rate_cols].mean().round(3).reindex(TKS).T.to_string())
    if plt is None:
        return
    fig, ax = plt.subplots(figsize=(11, 4))
    for tk in ["AL30", "AL30D"]:
        d = df[df.ticker == tk].sort_values("fecha")
        ax.plot(d["fecha"], d["BOT_RATE_WORST_LEND"], "o-", ms=4, color=COLOR[tk], label=f"{tk} worst_lend")
        ax.plot(d["fecha"], d["BOT_RATE_WORST_BORROW"], "s--", ms=4, color=COLOR[tk], alpha=.6,
                label=f"{tk} worst_borrow")
    ax.axhline(0, color="k", lw=.5); ax.legend(fontsize=8, ncol=2); ax.grid(alpha=.3)
    ax.set_title("Tasas colocar/tomar que ejecuta el bot", fontsize=10)
    plt.tight_layout(); fig.savefig(FIG / "7_tasas.png", dpi=110); plt.close(fig)


# ── 8. ¿El bot ve la misma tasa de mercado que el libro? ────────────────────────
def seccion_8_bot_vs_libro(df, plt):
    sep("8. ¿El bot ve la misma tasa de MERCADO que el libro?")
    book = {"lend":   ["MKT_TASA_TASACOLOCARBIDPASIVO_MEDIAN", "MKT_TASA_TASACOLOCAROFFERPASIVO_MEDIAN"],
            "borrow": ["MKT_TASA_TASATOMARBIDPASIVO_MEDIAN",  "MKT_TASA_TASATOMAROFFERPASIVO_MEDIAN"]}
    botc = {"lend": "BOT_MKT_LEND_RATE", "borrow": "BOT_MKT_BORROW_RATE"}
    dd = df.copy()
    for c in list(botc.values()) + [x for v in book.values() for x in v]:
        if c in dd:
            dd.loc[dd[c].abs() > 1, c] = np.nan  # descarta outliers de mediana (días degenerados)

    rows = []
    for lado, bcols in book.items():
        for bc in bcols:
            for tk in TKS:
                s = dd[dd.ticker == tk][[botc[lado], bc]].dropna()
                if len(s) > 5:
                    gap = s[botc[lado]] - s[bc]
                    rows.append({"lado": lado, "ticker": tk, "n": len(s),
                                 "libro": bc.replace("MKT_TASA_", "").replace("PASIVO_MEDIAN", ""),
                                 "corr": round(s[botc[lado]].corr(s[bc], method="spearman"), 2),
                                 "gap_med": round(gap.median(), 4),
                                 "|gap|_med": round(gap.abs().median(), 4)})
    print("bot (a_f3_market) vs libro (MKT_TASA) — por lado/par/ticker:")
    print(pd.DataFrame(rows).to_string(index=False))

    pares = [("BOT_MKT_LEND_RATE",   "MKT_TASA_TASACOLOCAROFFERPASIVO_MEDIAN", "colocar (lend)"),
             ("BOT_MKT_BORROW_RATE", "MKT_TASA_TASATOMARBIDPASIVO_MEDIAN",    "tomar (borrow)")]
    print()
    for b, k, t in pares:
        gap = (dd[b] - dd[k]).abs()
        print(f"{t}: |bot - libro| mediana={gap.median():.4f}  p90={gap.quantile(.9):.4f}")
    if plt is None:
        return
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    for ax, (b, k, t) in zip(axes, pares):
        for tk in TKS:
            d = dd[dd.ticker == tk]
            ax.scatter(d[k], d[b], s=30, alpha=.75, label=tk, color=COLOR[tk])
        vals = pd.concat([dd[b], dd[k]]).dropna()
        if len(vals):
            ax.plot([vals.min(), vals.max()], [vals.min(), vals.max()], "k--", lw=.8, label="y=x")
        ax.set_xlabel(f"libro: {k}", fontsize=7); ax.set_ylabel(f"bot: {b}", fontsize=8)
        ax.set_title(t, fontsize=10); ax.legend(fontsize=8); ax.grid(alpha=.3)
    plt.tight_layout(); fig.savefig(FIG / "8_bot_vs_libro.png", dpi=110); plt.close(fig)


# ── 9. Correlación con el PnL ────────────────────────────────────────────────────
def seccion_9_correlacion(df, orig_vars, bot_vars, plt):
    sep("9. Correlación con el PnL (originales + candidatas)")
    cand = [c for c in orig_vars + bot_vars if df[c].notna().sum() > 10 and df[c].nunique() > 3]

    def corr_pnl(sub):
        out = {}
        for c in cand:
            s = sub[[c, "BOT_PNL"]].dropna()
            out[c] = s[c].corr(s["BOT_PNL"], method="spearman") if len(s) > 5 else np.nan
        return pd.Series(out)

    tab = pd.DataFrame({tk: corr_pnl(df[df.ticker == tk]) for tk in TKS})
    tab["|prom|"] = tab.abs().mean(axis=1)
    top = tab.sort_values("|prom|", ascending=False).head(20).drop(columns="|prom|").round(2)
    print("Top-20 drivers del PnL (Spearman vs BOT_PNL por ticker):")
    print(top.to_string())
    if plt is None:
        return
    fig, ax = plt.subplots(figsize=(5.5, 8))
    im = ax.imshow(top.values, cmap="RdBu_r", vmin=-1, vmax=1, aspect="auto")
    ax.set_xticks(range(len(top.columns))); ax.set_xticklabels(top.columns)
    ax.set_yticks(range(len(top.index))); ax.set_yticklabels(top.index, fontsize=7.5)
    for i in range(top.shape[0]):
        for j in range(top.shape[1]):
            v = top.values[i, j]
            if pd.notna(v):
                ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=7,
                        color="white" if abs(v) > .55 else "black")
    fig.colorbar(im, ax=ax, shrink=.4, label="Spearman vs BOT_PNL")
    ax.set_title("Top-20 drivers del PnL (orig + nuevas)", fontsize=10)
    plt.tight_layout(); fig.savefig(FIG / "9_correlacion_pnl.png", dpi=110); plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-figuras", action="store_true", help="No generar PNGs (sólo tablas)")
    args = ap.parse_args()

    plt = None
    if not args.no_figuras:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        FIG.mkdir(exist_ok=True)

    df = cargar()
    g = df.groupby("ticker")
    orig_vars = [c for c in df.columns if c.startswith(("MKT", "SPREAD", "LAT"))]
    bot_vars = [c for c in df.columns if c.startswith("BOT_") and c != "BOT_PNL"]

    seccion_1_racconto(df, orig_vars, bot_vars)
    seccion_2_catalogo()
    seccion_3_panorama(df, g, orig_vars)
    seccion_4_ciclo(df, g, plt)
    seccion_5_pasivo_agresivo(g, plt)
    seccion_6_ppt_otc(df, g, plt)
    seccion_7_tasas(df, g, plt)
    seccion_8_bot_vs_libro(df, plt)
    seccion_9_correlacion(df, orig_vars, bot_vars, plt)

    if plt is not None:
        print(f"\nFiguras guardadas en {FIG}/")


if __name__ == "__main__":
    main()
