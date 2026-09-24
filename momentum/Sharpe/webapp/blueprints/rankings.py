from flask import Blueprint, abort, render_template, request

import momentum_lib as ml

from webapp.core import cap_tier, config_store, dhan_client, rankings, regime

bp = Blueprint("rankings", __name__)


def _view_model(limit: int | None):
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
        "rows": rankings.top_rows(bundle, limit or max_n, rankings.held_tickers(universe),
                                   prices=dhan_client.apply_cached_prices(bundle.latest_prices)),
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
    view = _view_model(None)
    cfg = config_store.load_config()
    bundle = rankings.get_bundle(cfg)  # already cached by _view_model's own call above
    view["trend"] = _regime_trend_context(view["universe"], ml.DEFAULT_NEW_ENTRY_THRESHOLD)
    view["cap_tier"] = _cap_tier_dual_context(bundle)
    return render_template("rankings.html", **view)


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


@bp.route("/rankings/table")
def table_partial():
    """HTMX target: re-renders just the table, e.g. after a limit change or a manual refresh click."""
    limit = request.args.get("limit", type=int)
    return render_template("_rankings_table.html", **_view_model(limit))


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
    return render_template(
        "full_rankings.html",
        universe=config_store.universe_name(config_store.resolve_file(cfg)),
        total=total,
        default_top_n=min(_FULL_DEFAULT_TOP_N, total),
        eligibility_options=rankings.ELIGIBILITY_OPTIONS,
        sort_options=rankings.SORT_OPTIONS,
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

    return render_template(
        "_full_rankings_table.html",
        headers=[rankings.FULL_COLUMN_LABELS.get(c, c) for c in rankings.full_display_columns(bundle)],
        rows=rankings.full_rankings_rows(bundle, eligibility, sort_col, top_n, rel_dd_breach_threshold),
        summary=rankings.full_rankings_summary(bundle, rel_dd_breach_threshold),
        rel_dd_breach_threshold=rel_dd_breach_threshold,
    )
