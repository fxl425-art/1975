---
name: us-stock-research
description: Prepare, analyze, validate, and render evidence-backed U.S. stock research without an OpenAI API key. Use when the user asks to analyze a U.S. company's latest 10-K risk factors from SEC filings, classify industry-wide versus company-specific risks, assess 3-5 year thesis impact, produce a Markdown report with verified SEC quotations, or compare fixed-current-market-cap and quarter-end-market-cap P/E curves against rolling four-quarter net income.
---

# U.S. Stock Research

Use the active Codex session for judgment. Use the bundled script for SEC extraction, deterministic validation, and Markdown rendering. Never call the OpenAI API or read an API key.

## Workflow

1. Run from the target project root:

   ```powershell
   .\.venv\Scripts\python.exe .\.agents\skills\us-stock-research\scripts\us_stock_risk.py prepare --ticker NVDA
   ```

2. Read the emitted `source_path`, `manifest_path`, and `analysis_json_path`.
3. Read `references/risk-analysis.md` completely.
4. Analyze only the prepared Item 1A source in the current Codex subscription session.
5. Write UTF-8 JSON to the emitted `analysis_json_path`, without Markdown fences.
6. Validate quotations and render the report:

   ```powershell
   .\.venv\Scripts\python.exe .\.agents\skills\us-stock-research\scripts\us_stock_risk.py render --manifest <manifest_path> --analysis-json <analysis_json_path>
   ```

7. Confirm exactly 10 risks and 10 verified quotations. Inspect the Markdown summary table and metadata before delivery.

## Rolling P/E comparison

Read `references/fixed-market-cap-pe.md`, then run:

```powershell
.\.venv\Scripts\python.exe .\.agents\skills\us-stock-research\scripts\fixed_market_cap_pe.py --ticker NVDA
```

The script retrieves quarterly net income from SEC through OpenBB and moves a four-quarter window backward one quarter at a time. It plots quarter-end-market-cap P/E as the primary line and fixed-current-market-cap P/E as a half-opacity line of the same color. Inspect the emitted SVG, CSV, and JSON manifest. Distinguish measured historical market cap from market cap estimated as quarter-end close times SEC weighted-average basic shares.

Call quarter-end-market-cap rolling P/E **A line** and fixed-current-market-cap rolling P/E **B line**. To compare A lines across companies, run:

```powershell
.\.venv\Scripts\python.exe .\.agents\skills\us-stock-research\scripts\compare_a_lines.py --tickers GOOGL AAPL LITE TSM GEV NVDA
```

## Guardrails

- Treat `manifest.json` and `item_1a.txt` as authoritative.
- Do not use legacy HTML or regex extractions when EdgarTools succeeds.
- Do not add news, market data, or memory unless the user requests an expanded report.
- Copy every `evidence_quote` verbatim without ellipses.
- Keep severity, probability, and thesis impact separate.
- Fail closed: never weaken quotation validation merely to produce a report.
- Preserve existing files unless the user requests cleanup.
- Do not interpolate P/E when rolling four-quarter net income is zero or negative.
- Keep market capitalization and net income in the same currency.
- Match a non-trading fiscal quarter-end to the nearest prior trading date, never a later date.

## Updating a Report

Run `prepare` again. Reuse analysis only when the accession and source hash are unchanged. For a new filing, produce a new analysis JSON and report; retain the prior source bundle.
