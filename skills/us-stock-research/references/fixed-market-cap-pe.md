# Rolling P/E comparison

Use this metric to compare today's equity valuation with the company's earnings power at successive historical quarter-ends.

## Definition

For each quarter `q`:

`TTM net income(q) = NI(q) + NI(q-1) + NI(q-2) + NI(q-3)`

`fixed-current-market-cap rolling P/E(q) = current market cap / TTM net income(q)`

Hold the numerator constant for the entire curve. Move the four-quarter earnings window backward one quarter for every earlier point.

`quarter-end-market-cap rolling P/E(q) = market cap at q / TTM net income(q)`

Use the last trading-day market capitalization on or before fiscal quarter-end `q`. Plot this as the primary, full-strength line. Plot fixed-current-market-cap rolling P/E in the same hue at 50% opacity.

Use these stable names:

- **A line:** quarter-end-market-cap rolling P/E.
- **B line:** fixed-current-market-cap rolling P/E.

For a multi-company comparison, plot A lines only, use a shared calendar-date axis, and assign one color per ticker.

## Multi-company data fallbacks

- Use SEC quarterly income for domestic filers.
- For TSM, use the available FMP quarterly statements because SEC does not provide quarterly 10-Q data. Convert each TWD quarterly net-income observation to USD with the latest FRED `DEXTAUS` observation on or before that quarter-end, then sum the four converted quarters.
- Use FMP historical closing prices first. If the current FMP subscription rejects a ticker, use recent official Nasdaq historical closing prices and leave uncovered older quarters blank.
- Preserve unequal history lengths. Do not manufacture earlier points merely to align companies.

## Quarter-end market-cap hierarchy

1. Prefer FMP historical market capitalization matched to the nearest date on or before fiscal quarter-end.
2. When FMP history does not cover the quarter, estimate market cap as the matched FMP closing price multiplied by SEC `weighted_ave_basic_shares_os` for that quarter.
3. Record the actual market-cap date, method, price, and share basis for every point. Never label an estimate as vendor-reported historical market cap.
4. Reject matches more than seven calendar days before fiscal quarter-end.

## Interpretation guardrails

- The fixed-current-market-cap line is counterfactual. The quarter-end line is historical when FMP market cap is available and an explicit estimate otherwise.
- Record the current market-cap timestamp and source.
- Use SEC quarterly `net_income` as the denominator and preserve its reported currency.
- Require market cap and net income to use the same currency.
- Leave P/E blank when TTM net income is zero or negative; do not plot a negative multiple.
- Treat acquisitions, accounting changes, restatements, fiscal-calendar changes, and large one-time items as possible breaks in comparability.
- Do not mix annual net income with quarterly observations or sum overlapping TTM figures.

## Outputs

The script writes three files under `reports/` by default:

- SVG curve for visual inspection.
- CSV with quarterly net income, rolling TTM net income, both market caps, both P/E values, market-cap dates, methods, and statuses.
- JSON manifest with formula, sources, timestamps, currency, record counts, and file paths.

The multi-company script writes a separate SVG, long-form CSV, and JSON manifest whose filenames begin with `A_line_comparison_`.
