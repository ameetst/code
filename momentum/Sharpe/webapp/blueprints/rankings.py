from flask import Blueprint, abort, render_template, request, send_file

import momentum_lib as ml

from webapp import settings
from webapp.core import actions, cap_tier, config_store, dhan_client, holdings_view, rankings, regime, tradelog

bp = Blueprint("rankings", __name__)


@bp.route("/rankings/download")
def download():
    """Serves the Sharpe.py output workbook (<universe>_rankings.xlsx) as-is. Read-only,
    not gated by READ_ONLY -- matches the Streamlit dashboards' Download Rankings Excel."""
    cfg = config_store.load_config()
    universe = config_store.universe_name(config_store.resolve_file(cfg))
    path = config_store.rankings_xlsx_path(universe)
    if path is None:
        abort(404, description="No rankings workbook found. Run Sharpe.py to generate it first.")
    return send_file(path, as_attachment=True, download_name=path.name,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _view_model():
    cfg = config_store.load_config()
    try:
        bundle = rankings.get_bundle(cfg)
    except FileNotFoundError as e:
        abort(404, description=str(e))

    file = config_store.resolve_file(cfg)
    universe = config_store.universe_name(file)
    max_n = int(cfg["max_n"])
    detail = bundle.regime_detail
    score = bundle.regime_score

    vix_value, vix_source = dhan_client.get_live_vix()
    adv, dec, ad_ratio = regime.compute_ad_ratio(bundle.prices_df)
    if ad_ratio is None:
        ad_ratio_str = "N/A"
    elif ad_ratio == float("inf"):
        ad_ratio_str = "∞"
    else:
        ad_ratio_str = f"{ad_ratio:.2f}"

    return {
        "universe": universe,
        "file": file,
        "max_n": max_n,
        "regime": {
            "score": score,
            "badge": rankings.regime_badge(score),
            "emoji": {"high": "\U0001f7e2", "mid": "\U0001f7e1", "low": "\U0001f534"}[rankings.regime_badge(score)],
            "dynamic_n": int(detail["dynamic_n"]),
            "allow_new": bool(detail["allow_new"]),
            "entry_threshold": ml.DEFAULT_NEW_ENTRY_THRESHOLD,
            "entry_label": "NEW BUYS ALLOWED" if detail["allow_new"]
                           else f"NO NEW BUYS (< {ml.DEFAULT_NEW_ENTRY_THRESHOLD})",
            "eligible_52h": rankings.eligible_52h_count(bundle),
            "signals": [
                {"label": regime.SIGNAL_LABELS.get(k, k), "weight_pct": regime.SIGNAL_WEIGHTS_PCT.get(k),
                 "value": detail[k]}
                for k in regime.SIGNAL_LABELS if k in detail
            ],
            "vix_value": vix_value, "vix_source": vix_source,
            "adv": adv, "dec": dec, "ad_ratio_str": ad_ratio_str,
            "data_start": rankings.fmt_date(bundle.dates[0]),
            "data_end": rankings.fmt_date(bundle.dates[-1]),
        },
    }


def _regime_trend_context(universe: str, entry_threshold: float) -> dict:
    history = regime.load_regime_history(universe)
    return {
        "records_json": regime.trend_chart_records(history, entry_threshold),
        "n_days": len(history),
        "latest_date": rankings.fmt_date(history[-1]["date"]) if history else None,
        "entry_threshold": entry_threshold,
    }


def _cap_tier_dual_context(bundle: rankings.Bundle) -> dict:
    stockdb = cap_tier.load_stockdb()
    if not stockdb:
        return {"available": False}
    rows = cap_tier.compute_cap_tier_dual(bundle.result, bundle.prices_df,
                                           list(bundle.prices_df.index), stockdb)
    return {"available": True, "rows": rows}


@bp.route("/")
def index():
    view = _view_model()
    cfg = config_store.load_config()
    bundle = rankings.get_bundle(cfg)  # already cached by _view_model's own call above
    if not settings.READ_ONLY:
        # Same as the Streamlit dashboards on every load: record today's regime point
        # (a no-op if it's already stored). Never happens in the default read-only mode.
        regime.record_today(view["universe"], bundle.regime_score, bundle.regime_detail)
    view["trend"] = _regime_trend_context(view["universe"], ml.DEFAULT_NEW_ENTRY_THRESHOLD)
    view["cap_tier"] = _cap_tier_dual_context(bundle)
    view.update(holdings_view.build(view["universe"], bundle))   # MTM boxes + Active Holdings
    return render_template("rankings.html", **view)


@bp.route("/dashboard/refresh-prices", methods=["POST"])
def refresh_prices():
    """Fetches live LTP for currently-held tickers (Dhan, Yahoo Finance fallback) and caches it,
    overriding the workbook's last price everywhere (Dashboard holdings, Tradelog form defaults,
    Actions) until the next refresh. Not gated by READ_ONLY: it's a live external read with no
    file write, same category as the VIX/cap-tier fetches. Re-renders just #holdings-content."""
    cfg = config_store.load_config()
    try:
        bundle = rankings.get_bundle(cfg)
    except FileNotFoundError as e:
        abort(404, description=str(e))
    universe = config_store.universe_name(config_store.resolve_file(cfg))
    tl = tradelog.load_tradelog(universe)
    pnl = tradelog.calculate_holdings_and_pnl(tl, rankings.effective_prices(bundle))
    held = [h["Ticker"] for h in pnl["holdings_metrics"]]

    success, error = None, None
    if not held:
        error = "No active holdings to refresh."
    else:
        result = dhan_client.refresh_live_prices(held)
        if result["prices"]:
            success = f"Live prices refreshed from {result['source']}."
            if result["unmatched"]:
                success += (f" {len(result['unmatched'])} ticker(s) could not be resolved: "
                            f"{', '.join(result['unmatched'])}.")
        else:
            error = "Could not fetch live prices from Dhan or Yahoo Finance. Try again shortly."

    return render_template("_holdings_mtm.html", **holdings_view.build(universe, bundle, error=error, success=success))


@bp.route("/rankings/cap-tier/yahoo")
def cap_tier_yahoo():
    """HTMX target for the opt-in 'Load/Refresh Yahoo Market Cap Data' button. Slow
    (~15s across the universe) -- only runs when explicitly requested, matching the
    dashboard's own opt-in button rather than fetching automatically."""
    bundle, _ = _full_bundle_and_cfg()
    force = request.args.get("refresh") == "1"
    if force:
        cap_tier.clear_yahoo_cache()
    try:
        rows = cap_tier.compute_cap_tier_momentum(bundle.prices_df, list(bundle.prices_df.index), force=force)
        error = None
    except Exception as e:
        rows, error = None, str(e)
    return render_template("_cap_tier_yahoo.html", rows=rows, error=error)


_FULL_DEFAULT_TOP_N = 100


def _full_bundle_and_cfg():
    cfg = config_store.load_config()
    try:
        bundle = rankings.get_bundle(cfg)
    except FileNotFoundError as e:
        abort(404, description=str(e))
    return bundle, cfg


@bp.route("/rankings/full")
def full_page():
    bundle, cfg = _full_bundle_and_cfg()
    total = len(bundle.result)
    clenow, _ = rankings.load_clenow(cfg)
    ath, _ = rankings.load_ath(cfg)
    return render_template(
        "full_rankings.html",
        universe=config_store.universe_name(config_store.resolve_file(cfg)),
        total=total,
        default_top_n=min(_FULL_DEFAULT_TOP_N, total),
        eligibility_options=rankings.ELIGIBILITY_OPTIONS,
        sort_options=rankings.sort_options(clenow, ath),
    )


@bp.route("/rankings/full/table")
def full_table_partial():
    bundle, cfg = _full_bundle_and_cfg()
    total = len(bundle.result)

    eligibility = request.args.get("eligibility", "All")
    if eligibility not in rankings.ELIGIBILITY_OPTIONS:
        eligibility = "All"
    sort_col = request.args.get("sort_col", "RANK")
    top_n = request.args.get("top_n", type=int) or min(_FULL_DEFAULT_TOP_N, total)
    top_n = max(10, min(top_n, total)) if total else 0
    rel_dd_breach_threshold = float(cfg["rel_dd_breach_threshold"])
    search = request.args.get("q", "").strip()[:100]

    clenow, clenow_generated = rankings.load_clenow(cfg)
    ath, ath_generated = rankings.load_ath(cfg)
    held_only = request.args.get("held") == "1"
    held = set(actions.current_holdings(config_store.universe_name(config_store.resolve_file(cfg)))) if held_only else None
    rows = rankings.full_rankings_rows(bundle, eligibility, sort_col, top_n, rel_dd_breach_threshold,
                                        search=search, clenow=clenow, ath=ath, held=held)
    return render_template(
        "_full_rankings_table.html",
        headers=[rankings.FULL_COLUMN_LABELS.get(c, c) for c in rankings.full_display_columns(bundle, clenow, ath)],
        clenow_available=clenow is not None,
        clenow_generated=clenow_generated.strftime("%d-%b-%y %H:%M") if clenow_generated else None,
        ath_available=ath is not None,
        ath_coverage=int(ath["PCT_FROM_ATH"].notna().sum()) if ath is not None else 0,
        rows=rows,
        blocked=rankings.blocked_breakdown(rows),
        summary=rankings.full_rankings_summary(bundle, rel_dd_breach_threshold),
        rel_dd_breach_threshold=rel_dd_breach_threshold,
        search=search, eligibility=eligibility,
        held_only=held_only, held_count=len(held) if held is not None else 0,
        held_missing=len(held - set(bundle.result.index)) if held is not None else 0,
    )
