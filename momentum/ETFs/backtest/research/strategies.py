"""Ranking variants. rerank() reproduces emr.build_ranking exactly at its defaults, and adds
optional extra factors / filters. All features use data up to the day BEFORE the rebalance."""
import numpy as np
import pandas as pd
from lib import zs

EXCLUDED = {"LIQUID"}


def _feature(name, i, ctx):
    A = ctx.A
    with np.errstate(all="ignore"):
        if name == "r1m":   return A[i - 1] / A[i - 1 - 21] - 1
        if name == "r3m":   return A[i - 1] / A[i - 1 - 63] - 1
        if name == "r6m":   return A[i - 1] / A[i - 1 - 126] - 1
        if name == "r12m":  return A[i - 1] / A[i - 1 - 252] - 1
        if name == "m12_1": return A[i - 1 - 21] / A[i - 1 - 252] - 1
        if name == "lowvol": return -ctx.vol(i, 126)
        if name == "above200": return A[i - 1] / np.nanmean(A[i - 200:i], axis=0) - 1
    raise KeyError(name)


def make_rerank(thr=0.25, w6=0.5, w3=0.5, wS=0.5, wC=0.5, extras=None, filters=None):
    """extras: {feature: weight} added to the blended score as weight*z(feature) (z over the pool).
    filters: list of feature names that must be > 0 for entry eligibility (absolute momentum)."""
    extras = extras or {}
    filters = filters or []

    def fn(df, i, ctx):
        df = df.copy()
        sec_ok = ~df["SECTOR"].isin(EXCLUDED)
        pfh = df["PCT_FROM_HIGH"]
        pool = ((pfh <= thr * 100) | pfh.isna()) & sec_ok
        col = ctx.prices.columns.get_indexer(df["TICKER"])

        def zp(series):
            z = pd.Series(np.nan, index=df.index)
            if pool.sum() > 0:
                z[pool] = zs(series[pool])
            return z

        z6, z3, zc = zp(df["SHARPE_6M"]), zp(df["SHARPE_3M"]), zp(df["CLENOW_90D"])
        b, o6, o3 = z6.notna() & z3.notna(), z6.notna() & z3.isna(), z6.isna() & z3.notna()
        sc = pd.Series(np.nan, index=df.index)
        sc[b] = w6 * z6[b] + w3 * z3[b]; sc[o6] = w6 * z6[o6]; sc[o3] = w3 * z3[o3]
        b2, os_, oc = sc.notna() & zc.notna(), sc.notna() & zc.isna(), sc.isna() & zc.notna()
        score = pd.Series(np.nan, index=df.index)
        score[b2] = wS * sc[b2] + wC * zc[b2]; score[os_] = wS * sc[os_]; score[oc] = wC * zc[oc]
        for name, w in extras.items():
            f = pd.Series(_feature(name, i, ctx)[col], index=df.index)
            zf = zp(f)
            score = score.add(w * zf.fillna(0.0), fill_value=0.0).where(score.notna() | zf.notna())
        elig = pool.copy()
        for name in filters:
            f = pd.Series(_feature(name, i, ctx)[col], index=df.index)
            elig &= (f > 0) | f.isna()
        out_rank = pd.Series(np.nan, index=df.index)
        if elig.sum() > 0:
            out_rank[elig] = score[elig].rank(ascending=False, na_option="bottom").astype(int)
        df["RANK_INVESTABLE"] = out_rank
        df["SCREEN_PASS"] = elig
        return df
    return fn
