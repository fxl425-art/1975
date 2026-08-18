"""Compare fixed and quarter-end-market-cap P/E on rolling net income."""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="")
    temporary.replace(path)


def fetch_market_cap(ticker: str) -> tuple[float, str, str, str]:
    from openbb import obb

    quote = obb.equity.price.quote(symbol=ticker, provider="fmp").to_df()
    if quote.empty or "market_cap" not in quote.columns:
        raise RuntimeError("FMP quote did not return market_cap.")
    market_cap = pd.to_numeric(quote.iloc[0]["market_cap"], errors="coerce")
    if pd.isna(market_cap) or float(market_cap) <= 0:
        raise RuntimeError("FMP returned an invalid current market capitalization.")
    timestamp = quote.iloc[0].get("last_timestamp")
    as_of = str(timestamp) if pd.notna(timestamp) else datetime.now().astimezone().isoformat()
    return float(market_cap), as_of, "USD", "OpenBB/FMP equity.price.quote"


def fetch_quarterly_income(ticker: str, limit: int) -> pd.DataFrame:
    from openbb import obb

    result = obb.equity.fundamental.income(
        symbol=ticker,
        period="quarterly",
        limit=limit,
        provider="sec",
    ).to_df()
    required = {"period_ending", "net_income"}
    missing = required.difference(result.columns)
    if missing:
        raise RuntimeError(f"SEC income data is missing columns: {sorted(missing)}")
    return result


def fetch_historical_market_caps(ticker: str) -> pd.DataFrame:
    from openbb import obb

    result = obb.equity.historical_market_cap(
        symbol=ticker,
        provider="fmp",
    ).to_df()
    if result.empty or "market_cap" not in result.columns:
        return pd.DataFrame(columns=["date", "market_cap"])
    result = result.reset_index()
    date_column = "date" if "date" in result.columns else result.columns[0]
    return result.rename(columns={date_column: "date"})[["date", "market_cap"]]


def fetch_historical_prices(
    ticker: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    from openbb import obb

    result = obb.equity.price.historical(
        symbol=ticker,
        start_date=start_date,
        end_date=end_date,
        provider="fmp",
    ).to_df()
    if result.empty or "close" not in result.columns:
        raise RuntimeError("FMP historical prices did not return close prices.")
    result = result.reset_index()
    date_column = "date" if "date" in result.columns else result.columns[0]
    return result.rename(columns={date_column: "date"})[["date", "close"]]


def normalize_daily_series(
    frame: pd.DataFrame | None,
    value_column: str,
) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["date", value_column])
    normalized = frame.copy()
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    normalized[value_column] = pd.to_numeric(
        normalized[value_column], errors="coerce"
    )
    normalized = normalized.dropna(subset=["date", value_column])
    normalized = normalized[normalized[value_column] > 0]
    return (
        normalized.drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )


def last_observation_on_or_before(
    series: pd.DataFrame,
    quarter_end: pd.Timestamp,
    value_column: str,
    max_gap_days: int = 7,
) -> tuple[float, pd.Timestamp] | None:
    candidates = series[series["date"] <= quarter_end]
    if candidates.empty:
        return None
    row = candidates.iloc[-1]
    matched_date = pd.Timestamp(row["date"])
    if (quarter_end - matched_date).days > max_gap_days:
        return None
    return float(row[value_column]), matched_date


def calculate_curve(
    income: pd.DataFrame,
    market_cap: float,
    historical_market_caps: pd.DataFrame | None = None,
    historical_prices: pd.DataFrame | None = None,
) -> pd.DataFrame:
    frame = income.copy()
    frame["period_ending"] = pd.to_datetime(frame["period_ending"], errors="coerce")
    frame["net_income"] = pd.to_numeric(frame["net_income"], errors="coerce")
    frame = frame.dropna(subset=["period_ending", "net_income"])
    frame = frame.drop_duplicates(subset=["period_ending"], keep="first")
    frame = frame.sort_values("period_ending").reset_index(drop=True)
    if len(frame) < 4:
        raise RuntimeError("At least four valid quarterly net-income records are required.")

    frame["ttm_net_income"] = frame["net_income"].rolling(4, min_periods=4).sum()
    frame["fixed_market_cap"] = float(market_cap)
    positive = frame["ttm_net_income"] > 0
    frame["fixed_market_cap_pe"] = float("nan")
    frame.loc[positive, "fixed_market_cap_pe"] = (
        market_cap / frame.loc[positive, "ttm_net_income"]
    )
    frame["pe_status"] = "insufficient_history"
    complete = frame["ttm_net_income"].notna()
    frame.loc[complete & positive, "pe_status"] = "valid"
    frame.loc[complete & ~positive, "pe_status"] = "non_positive_ttm_net_income"

    caps = normalize_daily_series(historical_market_caps, "market_cap")
    prices = normalize_daily_series(historical_prices, "close")
    frame["quarter_end_market_cap"] = float("nan")
    frame["quarter_end_market_cap_date"] = pd.NaT
    frame["quarter_end_market_cap_method"] = "missing"
    frame["quarter_end_close"] = float("nan")
    frame["quarter_end_share_basis"] = float("nan")
    frame["quarter_end_market_cap_pe"] = float("nan")
    frame["quarter_end_pe_status"] = "insufficient_history"

    share_column = None
    for candidate in ("weighted_ave_basic_shares_os", "weighted_ave_diluted_shares_os"):
        if candidate in frame.columns:
            share_column = candidate
            frame[candidate] = pd.to_numeric(frame[candidate], errors="coerce")
            break

    for index, row in frame[complete].iterrows():
        quarter_end = pd.Timestamp(row["period_ending"])
        cap_match = last_observation_on_or_before(
            caps, quarter_end, "market_cap"
        )
        if cap_match:
            quarter_cap, cap_date = cap_match
            method = "fmp_historical_market_cap"
        else:
            price_match = last_observation_on_or_before(
                prices, quarter_end, "close"
            )
            shares = row.get(share_column) if share_column else None
            if not price_match or pd.isna(shares) or float(shares) <= 0:
                frame.loc[index, "quarter_end_pe_status"] = "missing_market_cap"
                continue
            close, cap_date = price_match
            quarter_cap = close * float(shares)
            method = f"estimated_close_x_{share_column}"
            frame.loc[index, "quarter_end_close"] = close
            frame.loc[index, "quarter_end_share_basis"] = float(shares)

        frame.loc[index, "quarter_end_market_cap"] = quarter_cap
        frame.loc[index, "quarter_end_market_cap_date"] = cap_date
        frame.loc[index, "quarter_end_market_cap_method"] = method
        if row["ttm_net_income"] <= 0:
            frame.loc[index, "quarter_end_pe_status"] = (
                "non_positive_ttm_net_income"
            )
            continue
        frame.loc[index, "quarter_end_market_cap_pe"] = (
            quarter_cap / row["ttm_net_income"]
        )
        frame.loc[index, "quarter_end_pe_status"] = "valid"
    frame["a_line_pe"] = frame["quarter_end_market_cap_pe"]
    frame["b_line_pe"] = frame["fixed_market_cap_pe"]
    return frame


def currency_from_income(frame: pd.DataFrame) -> str | None:
    if "reported_currency" not in frame.columns:
        return None
    values = {
        str(value).upper().strip()
        for value in frame["reported_currency"].dropna()
        if str(value).strip()
    }
    if len(values) > 1:
        raise RuntimeError(f"Multiple SEC reporting currencies found: {sorted(values)}")
    return next(iter(values), None)


def format_money(value: float, currency: str) -> str:
    magnitude = abs(value)
    if magnitude >= 1e12:
        return f"{currency} {value / 1e12:.2f}T"
    if magnitude >= 1e9:
        return f"{currency} {value / 1e9:.2f}B"
    if magnitude >= 1e6:
        return f"{currency} {value / 1e6:.2f}M"
    return f"{currency} {value:,.0f}"


def render_svg(
    ticker: str,
    curve: pd.DataFrame,
    market_cap: float,
    currency: str,
    as_of: str,
) -> str:
    plotted = curve[curve["ttm_net_income"].notna()].reset_index(drop=True)
    fixed_valid = plotted["fixed_market_cap_pe"].notna()
    instant_valid = plotted["quarter_end_market_cap_pe"].notna()
    if not fixed_valid.any():
        raise RuntimeError("No positive rolling four-quarter net income is available to plot.")
    if not instant_valid.any():
        raise RuntimeError("No quarter-end market capitalization is available to plot.")

    width, height = 1200, 740
    left, right, top, bottom = 95, 45, 150, 95
    plot_width = width - left - right
    plot_height = height - top - bottom
    max_pe = float(
        pd.concat(
            [
                plotted.loc[fixed_valid, "fixed_market_cap_pe"],
                plotted.loc[instant_valid, "quarter_end_market_cap_pe"],
            ]
        ).max()
    )
    y_max = max(5.0, math.ceil(max_pe * 1.1 / 5.0) * 5.0)
    line_color = "#2463eb"

    def x_pos(index: int) -> float:
        denominator = max(len(plotted) - 1, 1)
        return left + plot_width * index / denominator

    def y_pos(value: float) -> float:
        return top + plot_height * (1.0 - value / y_max)

    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<style>text{font-family:Arial,"Microsoft YaHei",sans-serif;fill:#172033}.title{font-size:28px;font-weight:700}.sub{font-size:15px;fill:#566176}.axis{font-size:13px;fill:#566176}.note{font-size:13px;fill:#7a4250}.legend{font-size:14px;fill:#344055}</style>',
        f'<text x="{left}" y="42" class="title">{html.escape(ticker)} Rolling P/E Comparison</text>',
        f'<text x="{left}" y="70" class="sub">Fixed market cap: {html.escape(format_money(market_cap, currency))} | As of: {html.escape(as_of)}</text>',
        '<text x="95" y="94" class="sub">Shared denominator: rolling sum of four SEC quarterly net-income observations</text>',
        f'<line x1="{left}" y1="120" x2="{left+42}" y2="120" stroke="{line_color}" stroke-width="4"/>',
        f'<text x="{left+52}" y="125" class="legend">A line — Quarter-end market-cap P/E</text>',
        f'<line x1="{left+310}" y1="120" x2="{left+352}" y2="120" stroke="{line_color}" stroke-opacity="0.5" stroke-width="4"/>',
        f'<text x="{left+362}" y="125" class="legend">B line — Fixed-current-market-cap P/E</text>',
    ]

    for tick in range(6):
        value = y_max * tick / 5
        y = y_pos(value)
        elements.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" stroke="#dfe5ee"/>')
        elements.append(f'<text x="{left-12}" y="{y+4:.1f}" text-anchor="end" class="axis">{value:.1f}x</text>')

    tick_count = min(8, len(plotted))
    tick_indexes = sorted({round(i * (len(plotted) - 1) / max(tick_count - 1, 1)) for i in range(tick_count)})
    for index in tick_indexes:
        x = x_pos(index)
        label = plotted.iloc[index]["period_ending"].strftime("%Y-%m-%d")
        elements.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{height-bottom}" stroke="#eef1f6"/>')
        elements.append(f'<text x="{x:.1f}" y="{height-bottom+25}" text-anchor="middle" class="axis">{label}</text>')

    def add_line(column: str, opacity: float) -> None:
        segment: list[str] = []
        segments: list[list[str]] = []
        for index, row in plotted.iterrows():
            value = row[column]
            if pd.isna(value):
                if segment:
                    segments.append(segment)
                    segment = []
                continue
            segment.append(f"{x_pos(index):.1f},{y_pos(float(value)):.1f}")
        if segment:
            segments.append(segment)
        for points in segments:
            if len(points) == 1:
                x, y = points[0].split(",")
                elements.append(
                    f'<circle cx="{x}" cy="{y}" r="4" fill="{line_color}" fill-opacity="{opacity}"/>'
                )
            else:
                elements.append(
                    f'<polyline points="{" ".join(points)}" fill="none" stroke="{line_color}" stroke-opacity="{opacity}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>'
                )

    add_line("fixed_market_cap_pe", 0.5)
    add_line("quarter_end_market_cap_pe", 1.0)

    fixed_last_index = int(plotted[fixed_valid].index[-1])
    fixed_last_pe = float(plotted.loc[fixed_last_index, "fixed_market_cap_pe"])
    instant_last_index = int(plotted[instant_valid].index[-1])
    instant_last_pe = float(
        plotted.loc[instant_last_index, "quarter_end_market_cap_pe"]
    )
    elements.extend(
        [
            f'<circle cx="{x_pos(fixed_last_index):.1f}" cy="{y_pos(fixed_last_pe):.1f}" r="5" fill="{line_color}" fill-opacity="0.5"/>',
            f'<circle cx="{x_pos(instant_last_index):.1f}" cy="{y_pos(instant_last_pe):.1f}" r="5" fill="{line_color}"/>',
            f'<text x="{width-right}" y="70" text-anchor="end" class="legend">Latest: quarter-end {instant_last_pe:.2f}x | fixed {fixed_last_pe:.2f}x</text>',
            f'<line x1="{left}" y1="{height-bottom}" x2="{width-right}" y2="{height-bottom}" stroke="#79859a"/>',
            f'<line x1="{left}" y1="{top}" x2="{left}" y2="{height-bottom}" stroke="#79859a"/>',
            f'<text x="{width/2:.1f}" y="{height-25}" text-anchor="middle" class="axis">Quarter end</text>',
        ]
    )
    excluded = int((plotted["pe_status"] == "non_positive_ttm_net_income").sum())
    if excluded:
        elements.append(f'<text x="{left}" y="{height-50}" class="note">{excluded} quarter(s) omitted because rolling TTM net income was zero or negative.</text>')
    elements.append("</svg>")
    return "\n".join(elements) + "\n"


def write_csv(path: Path, frame: pd.DataFrame) -> None:
    columns = [
        "period_ending",
        "net_income",
        "ttm_net_income",
        "fixed_market_cap",
        "fixed_market_cap_pe",
        "pe_status",
        "quarter_end_market_cap",
        "quarter_end_market_cap_date",
        "quarter_end_market_cap_method",
        "quarter_end_close",
        "quarter_end_share_basis",
        "quarter_end_market_cap_pe",
        "quarter_end_pe_status",
        "a_line_pe",
        "b_line_pe",
    ]
    rows = frame[frame["ttm_net_income"].notna()][columns].copy()
    rows["period_ending"] = rows["period_ending"].dt.strftime("%Y-%m-%d")
    rows["quarter_end_market_cap_date"] = pd.to_datetime(
        rows["quarter_end_market_cap_date"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    rows.to_csv(temporary, index=False, encoding="utf-8", quoting=csv.QUOTE_MINIMAL)
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare fixed and quarter-end-market-cap P/E on rolling SEC net income."
    )
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--history-quarters", type=int, default=40)
    parser.add_argument("--market-cap", type=float)
    parser.add_argument("--market-cap-as-of")
    parser.add_argument("--market-cap-currency", default="USD")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    ticker = args.ticker.upper().strip()
    if args.history_quarters < 4:
        raise ValueError("--history-quarters must be at least 4.")
    root = args.project_root.resolve()
    output_dir = args.output_dir if args.output_dir.is_absolute() else root / args.output_dir

    if args.market_cap is None:
        market_cap, as_of, currency, cap_source = fetch_market_cap(ticker)
    else:
        if args.market_cap <= 0:
            raise ValueError("--market-cap must be positive.")
        market_cap = float(args.market_cap)
        as_of = args.market_cap_as_of or datetime.now().astimezone().isoformat()
        currency = args.market_cap_currency.upper().strip()
        cap_source = "user-supplied"

    income = fetch_quarterly_income(ticker, args.history_quarters)
    income_currency = currency_from_income(income)
    if income_currency and income_currency != currency:
        raise RuntimeError(
            f"Currency mismatch: market cap is {currency}, SEC net income is "
            f"{income_currency}. Supply a converted --market-cap in {income_currency}."
        )
    period_dates = pd.to_datetime(income["period_ending"], errors="coerce").dropna()
    if period_dates.empty:
        raise RuntimeError("SEC income data contains no valid quarter-end dates.")
    price_start = (period_dates.min() - pd.Timedelta(days=10)).date().isoformat()
    price_end = period_dates.max().date().isoformat()
    historical_cap_error = None
    try:
        historical_caps = fetch_historical_market_caps(ticker)
    except Exception as exc:
        historical_caps = pd.DataFrame(columns=["date", "market_cap"])
        historical_cap_error = str(exc)
    historical_prices = fetch_historical_prices(
        ticker,
        start_date=price_start,
        end_date=price_end,
    )
    curve = calculate_curve(
        income,
        market_cap,
        historical_market_caps=historical_caps,
        historical_prices=historical_prices,
    )

    stem = f"{ticker}_fixed_market_cap_pe"
    svg_path = output_dir / f"{stem}.svg"
    csv_path = output_dir / f"{stem}.csv"
    manifest_path = output_dir / f"{stem}.json"
    atomic_write(svg_path, render_svg(ticker, curve, market_cap, currency, as_of))
    write_csv(csv_path, curve)
    completed = curve[curve["ttm_net_income"].notna()]
    manifest: dict[str, Any] = {
        "ticker": ticker,
        "metrics": [
            "quarter-end-market-cap rolling P/E",
            "fixed-current-market-cap rolling P/E",
        ],
        "line_names": {
            "A": "quarter-end-market-cap rolling P/E",
            "B": "fixed-current-market-cap rolling P/E",
        },
        "formulas": {
            "quarter_end": "quarter-end market cap / rolling sum of four quarterly net-income observations",
            "fixed_current": "current market cap / rolling sum of four quarterly net-income observations",
        },
        "market_cap": market_cap,
        "market_cap_currency": currency,
        "market_cap_as_of": as_of,
        "market_cap_source": cap_source,
        "net_income_source": "OpenBB/SEC equity.fundamental.income (quarterly)",
        "quarter_end_market_cap_source": (
            "OpenBB/FMP historical_market_cap when available; otherwise "
            "FMP quarter-end close x SEC weighted-average basic shares"
        ),
        "quarter_end_match_rule": "nearest trading date on or before fiscal quarter-end, maximum gap 7 days",
        "historical_market_cap_fetch_error": historical_cap_error,
        "quarterly_records": len(curve),
        "curve_points": len(completed),
        "valid_fixed_pe_points": int(
            completed["fixed_market_cap_pe"].notna().sum()
        ),
        "valid_quarter_end_pe_points": int(
            completed["quarter_end_market_cap_pe"].notna().sum()
        ),
        "direct_historical_market_cap_points": int(
            (completed["quarter_end_market_cap_method"] == "fmp_historical_market_cap").sum()
        ),
        "estimated_historical_market_cap_points": int(
            completed["quarter_end_market_cap_method"].str.startswith(
                "estimated_", na=False
            ).sum()
        ),
        "non_positive_ttm_points": int((completed["pe_status"] == "non_positive_ttm_net_income").sum()),
        "svg_path": str(svg_path),
        "csv_path": str(csv_path),
    }
    atomic_write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": "rendered", **manifest, "manifest_path": str(manifest_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1) from exc
