"""Compare quarter-end-market-cap rolling P/E (A lines) across tickers."""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import math
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from urllib.parse import urlencode

import pandas as pd

from fixed_market_cap_pe import (
    atomic_write,
    calculate_curve,
    fetch_historical_market_caps,
    fetch_historical_prices,
    fetch_quarterly_income,
)


DEFAULT_COLORS = {
    "GOOGL": "#DB4437",
    "AAPL": "#111827",
    "LITE": "#8B5CF6",
    "TSM": "#00A67E",
    "GEV": "#F59E0B",
    "NVDA": "#2463EB",
}
FALLBACK_COLORS = [
    "#0EA5E9",
    "#E11D48",
    "#16A34A",
    "#9333EA",
    "#EA580C",
    "#475569",
]
FRED_TWD_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXTAUS"


def fetch_twd_per_usd() -> pd.DataFrame:
    request = Request(FRED_TWD_URL, headers={"User-Agent": "us-stock-research/1.0"})
    with urlopen(request, timeout=30) as response:  # noqa: S310
        content = response.read().decode("utf-8")
    frame = pd.read_csv(io.StringIO(content))
    date_column = "observation_date" if "observation_date" in frame.columns else frame.columns[0]
    if "DEXTAUS" not in frame.columns:
        raise RuntimeError("FRED DEXTAUS response is missing its value column.")
    frame = frame.rename(
        columns={date_column: "date", "DEXTAUS": "twd_per_usd"}
    )
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["twd_per_usd"] = pd.to_numeric(
        frame["twd_per_usd"], errors="coerce"
    )
    return frame.dropna(subset=["date", "twd_per_usd"]).sort_values("date")


def fetch_nasdaq_prices(
    ticker: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    end = pd.Timestamp(end_date)
    start = max(pd.Timestamp(start_date), end - pd.Timedelta(days=370))
    query = urlencode(
        {
            "assetclass": "stocks",
            "fromdate": start.date().isoformat(),
            "todate": end.date().isoformat(),
            "limit": 5000,
        }
    )
    url = f"https://api.nasdaq.com/api/quote/{ticker}/historical?{query}"
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json, text/plain, */*",
            "Referer": f"https://www.nasdaq.com/market-activity/stocks/{ticker.lower()}/historical",
        },
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310
        payload = json.loads(response.read().decode("utf-8"))
    rows = (
        (payload.get("data") or {}).get("tradesTable") or {}
    ).get("rows") or []
    if not rows:
        raise RuntimeError(f"Nasdaq returned no recent historical prices for {ticker}.")
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["close"] = pd.to_numeric(
        frame["close"].astype(str).str.replace("$", "", regex=False).str.replace(
            ",", "", regex=False
        ),
        errors="coerce",
    )
    return frame.dropna(subset=["date", "close"])[["date", "close"]]


def comparison_prices(
    ticker: str,
    start_date: str,
    end_date: str,
) -> tuple[pd.DataFrame, str, str | None]:
    try:
        return (
            fetch_historical_prices(ticker, start_date, end_date),
            "OpenBB/FMP historical prices",
            None,
        )
    except Exception as exc:
        return (
            fetch_nasdaq_prices(ticker, start_date, end_date),
            "Nasdaq official recent historical prices",
            str(exc),
        )


def match_fx(
    fx: pd.DataFrame,
    period_end: pd.Timestamp,
    max_gap_days: int = 14,
) -> tuple[float, str]:
    candidates = fx[fx["date"] <= period_end]
    if candidates.empty:
        raise RuntimeError(f"No TWD/USD rate is available on or before {period_end.date()}.")
    row = candidates.iloc[-1]
    matched_date = pd.Timestamp(row["date"])
    if (period_end - matched_date).days > max_gap_days:
        raise RuntimeError(f"TWD/USD rate is too stale for {period_end.date()}.")
    return float(row["twd_per_usd"]), matched_date.date().isoformat()


def normalize_fmp_income(ticker: str, limit: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    from openbb import obb

    raw = obb.equity.fundamental.income(
        symbol=ticker,
        period="quarter",
        limit=min(limit, 5),
        provider="fmp",
    ).to_df()
    aliases = {
        "bottom_line_net_income": "net_income",
        "weighted_average_basic_shares_outstanding": "weighted_ave_basic_shares_os",
        "weighted_average_diluted_shares_outstanding": "weighted_ave_diluted_shares_os",
    }
    frame = raw.rename(columns=aliases).copy()
    required = {"period_ending", "net_income"}
    missing = required.difference(frame.columns)
    if missing:
        raise RuntimeError(f"FMP income data is missing columns: {sorted(missing)}")
    currencies = {
        str(value).upper().strip()
        for value in frame.get("reported_currency", pd.Series(dtype=object)).dropna()
    }
    if len(currencies) != 1:
        raise RuntimeError(f"Expected one FMP reporting currency, found {sorted(currencies)}")
    original_currency = next(iter(currencies))
    frame["net_income_original"] = pd.to_numeric(frame["net_income"], errors="coerce")
    frame["net_income_original_currency"] = original_currency
    frame["income_fx_to_usd"] = 1.0
    frame["income_fx_date"] = None

    if original_currency == "TWD":
        fx = fetch_twd_per_usd()
        converted = []
        rates = []
        dates = []
        for _, row in frame.iterrows():
            rate, fx_date = match_fx(fx, pd.Timestamp(row["period_ending"]))
            converted.append(float(row["net_income_original"]) / rate)
            rates.append(rate)
            dates.append(fx_date)
        frame["net_income"] = converted
        frame["income_fx_to_usd"] = rates
        frame["income_fx_date"] = dates
        frame["reported_currency"] = "USD"
    elif original_currency != "USD":
        raise RuntimeError(
            f"Unsupported comparison currency {original_currency} for {ticker}."
        )

    return frame, {
        "income_source": "OpenBB/FMP quarterly income",
        "source_quarters": len(frame),
        "original_currency": original_currency,
        "converted_to": "USD",
        "fx_source": "FRED DEXTAUS" if original_currency == "TWD" else None,
    }


def comparison_income(ticker: str, limit: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    if ticker == "TSM":
        return normalize_fmp_income(ticker, limit)
    frame = fetch_quarterly_income(ticker, limit).copy()
    frame["net_income_original"] = frame["net_income"]
    frame["net_income_original_currency"] = frame.get("reported_currency", "USD")
    frame["income_fx_to_usd"] = 1.0
    frame["income_fx_date"] = None
    return frame, {
        "income_source": "OpenBB/SEC quarterly income",
        "source_quarters": len(frame),
        "original_currency": "USD",
        "converted_to": "USD",
        "fx_source": None,
    }


def build_ticker_curve(ticker: str, limit: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    income, metadata = comparison_income(ticker, limit)
    period_dates = pd.to_datetime(income["period_ending"], errors="coerce").dropna()
    if period_dates.empty:
        raise RuntimeError(f"{ticker} has no valid quarter-end dates.")
    start_date = (period_dates.min() - pd.Timedelta(days=10)).date().isoformat()
    end_date = period_dates.max().date().isoformat()
    try:
        historical_caps = fetch_historical_market_caps(ticker)
        historical_cap_error = None
    except Exception as exc:
        historical_caps = pd.DataFrame(columns=["date", "market_cap"])
        historical_cap_error = str(exc)
    prices, price_source, primary_price_error = comparison_prices(
        ticker, start_date, end_date
    )
    curve = calculate_curve(
        income,
        market_cap=1.0,
        historical_market_caps=historical_caps,
        historical_prices=prices,
    )
    curve["ticker"] = ticker
    completed = curve[curve["ttm_net_income"].notna()]
    metadata.update(
        {
            "curve_points": len(completed),
            "valid_a_line_points": int(completed["a_line_pe"].notna().sum()),
            "direct_market_cap_points": int(
                (completed["quarter_end_market_cap_method"] == "fmp_historical_market_cap").sum()
            ),
            "estimated_market_cap_points": int(
                completed["quarter_end_market_cap_method"].str.startswith(
                    "estimated_", na=False
                ).sum()
            ),
            "historical_market_cap_fetch_error": historical_cap_error,
            "price_source": price_source,
            "primary_price_fetch_error": primary_price_error,
        }
    )
    return completed, metadata


def render_svg(
    combined: pd.DataFrame,
    ticker_order: list[str],
    colors: dict[str, str],
    y_max_override: float | None,
) -> str:
    valid = combined.dropna(subset=["a_line_pe"]).copy()
    if valid.empty:
        raise RuntimeError("No positive A-line P/E observations are available.")
    valid["period_ending"] = pd.to_datetime(valid["period_ending"])
    date_min = valid["period_ending"].min()
    date_max = valid["period_ending"].max()
    span_days = max((date_max - date_min).days, 1)

    width, height = 1400, 800
    left, right, top, bottom = 95, 55, 145, 100
    plot_width = width - left - right
    plot_height = height - top - bottom
    observed_max = float(valid["a_line_pe"].max())
    if y_max_override is not None:
        if y_max_override <= 0:
            raise ValueError("--y-max must be positive.")
        y_max = float(y_max_override)
    else:
        y_max = max(5.0, math.ceil(observed_max * 1.1 / 5.0) * 5.0)

    def x_pos(value: pd.Timestamp) -> float:
        return left + plot_width * (value - date_min).days / span_days

    def y_pos(value: float) -> float:
        clipped = min(value, y_max)
        return top + plot_height * (1.0 - clipped / y_max)

    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<style>text{font-family:Arial,"Microsoft YaHei",sans-serif;fill:#172033}.title{font-size:28px;font-weight:700}.sub{font-size:15px;fill:#566176}.axis{font-size:13px;fill:#566176}.legend{font-size:14px;fill:#344055}.note{font-size:12px;fill:#7a4250}</style>',
        f'<text x="{left}" y="42" class="title">A-Line Rolling P/E Comparison</text>',
        f'<text x="{left}" y="70" class="sub">Quarter-end market cap / rolling four-quarter net income | {date_min.date()} to {date_max.date()}</text>',
    ]

    for tick in range(6):
        value = y_max * tick / 5
        y = y_pos(value)
        elements.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" stroke="#dfe5ee"/>'
        )
        elements.append(
            f'<text x="{left-12}" y="{y+4:.1f}" text-anchor="end" class="axis">{value:.1f}x</text>'
        )

    for tick in range(8):
        date = date_min + timedelta(days=round(span_days * tick / 7))
        x = x_pos(date)
        elements.append(
            f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{height-bottom}" stroke="#eef1f6"/>'
        )
        elements.append(
            f'<text x="{x:.1f}" y="{height-bottom+25}" text-anchor="middle" class="axis">{date.date()}</text>'
        )

    legend_x = left
    for position, ticker in enumerate(ticker_order):
        series = combined[combined["ticker"] == ticker].sort_values("period_ending")
        color = colors[ticker]
        legend_row = position // 3
        legend_col = position % 3
        lx = legend_x + legend_col * 390
        ly = 98 + legend_row * 26
        ticker_valid = series.dropna(subset=["a_line_pe"])
        latest = ticker_valid.iloc[-1] if not ticker_valid.empty else None
        latest_text = (
            f"{ticker} {float(latest['a_line_pe']):.2f}x ({pd.Timestamp(latest['period_ending']).date()})"
            if latest is not None
            else f"{ticker} no valid P/E"
        )
        elements.append(
            f'<line x1="{lx}" y1="{ly}" x2="{lx+36}" y2="{ly}" stroke="{color}" stroke-width="4"/>'
        )
        elements.append(
            f'<text x="{lx+46}" y="{ly+5}" class="legend">{html.escape(latest_text)}</text>'
        )

        segment: list[str] = []
        segments: list[list[str]] = []
        for _, row in series.iterrows():
            value = row["a_line_pe"]
            if pd.isna(value):
                if segment:
                    segments.append(segment)
                    segment = []
                continue
            segment.append(
                f"{x_pos(pd.Timestamp(row['period_ending'])):.1f},{y_pos(float(value)):.1f}"
            )
        if segment:
            segments.append(segment)
        for points in segments:
            if len(points) == 1:
                x, y = points[0].split(",")
                elements.append(
                    f'<circle cx="{x}" cy="{y}" r="4" fill="{color}"/>'
                )
            else:
                elements.append(
                    f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>'
                )
        if latest is not None:
            elements.append(
                f'<circle cx="{x_pos(pd.Timestamp(latest["period_ending"])):.1f}" cy="{y_pos(float(latest["a_line_pe"])):.1f}" r="4" fill="{color}"/>'
            )

    elements.extend(
        [
            f'<line x1="{left}" y1="{height-bottom}" x2="{width-right}" y2="{height-bottom}" stroke="#79859a"/>',
            f'<line x1="{left}" y1="{top}" x2="{left}" y2="{height-bottom}" stroke="#79859a"/>',
            f'<text x="{width/2:.1f}" y="{height-25}" text-anchor="middle" class="axis">Fiscal quarter end</text>',
            f'<text x="{left}" y="{height-52}" class="note">Values above {y_max:.1f}x are clipped at the chart ceiling; CSV retains raw values.</text>'
            if observed_max > y_max
            else "",
            "</svg>",
        ]
    )
    return "\n".join(item for item in elements if item) + "\n"


def write_csv(path: Path, frame: pd.DataFrame) -> None:
    columns = [
        "ticker",
        "period_ending",
        "net_income_original",
        "net_income_original_currency",
        "income_fx_to_usd",
        "income_fx_date",
        "net_income",
        "ttm_net_income",
        "quarter_end_market_cap_date",
        "quarter_end_market_cap",
        "quarter_end_market_cap_method",
        "quarter_end_close",
        "quarter_end_share_basis",
        "a_line_pe",
        "quarter_end_pe_status",
    ]
    rows = frame[columns].copy()
    rows["period_ending"] = pd.to_datetime(rows["period_ending"]).dt.strftime(
        "%Y-%m-%d"
    )
    rows["quarter_end_market_cap_date"] = pd.to_datetime(
        rows["quarter_end_market_cap_date"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    rows.to_csv(temporary, index=False, encoding="utf-8", quoting=csv.QUOTE_MINIMAL)
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare A-line rolling P/E curves across U.S.-listed stocks."
    )
    parser.add_argument("--tickers", nargs="+", required=True)
    parser.add_argument("--history-quarters", type=int, default=20)
    parser.add_argument("--y-max", type=float)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.history_quarters < 4:
        raise ValueError("--history-quarters must be at least 4.")
    tickers = list(dict.fromkeys(ticker.upper().strip() for ticker in args.tickers))
    root = args.project_root.resolve()
    output_dir = args.output_dir if args.output_dir.is_absolute() else root / args.output_dir
    colors = {
        ticker: DEFAULT_COLORS.get(
            ticker, FALLBACK_COLORS[index % len(FALLBACK_COLORS)]
        )
        for index, ticker in enumerate(tickers)
    }

    curves = []
    metadata = {}
    for ticker in tickers:
        curve, ticker_metadata = build_ticker_curve(ticker, args.history_quarters)
        curves.append(curve)
        metadata[ticker] = ticker_metadata
        print(
            f"{ticker}: {ticker_metadata['valid_a_line_points']} valid A-line points",
            flush=True,
        )
    combined = pd.concat(curves, ignore_index=True)

    stem = "A_line_comparison_" + "_".join(tickers)
    svg_path = output_dir / f"{stem}.svg"
    csv_path = output_dir / f"{stem}.csv"
    manifest_path = output_dir / f"{stem}.json"
    atomic_write(
        svg_path,
        render_svg(combined, tickers, colors, args.y_max),
    )
    write_csv(csv_path, combined)
    manifest = {
        "metric": "A line: quarter-end market cap / rolling four-quarter net income",
        "tickers": tickers,
        "colors": colors,
        "ticker_metadata": metadata,
        "comparison_currency": "USD",
        "quarter_end_rule": "nearest trading date on or before fiscal quarter-end, maximum gap 7 days",
        "tsm_fx_source": "FRED DEXTAUS, Taiwan dollars per U.S. dollar",
        "svg_path": str(svg_path),
        "csv_path": str(csv_path),
    }
    atomic_write(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )
    print(
        json.dumps(
            {"status": "rendered", **manifest, "manifest_path": str(manifest_path)},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1) from exc
