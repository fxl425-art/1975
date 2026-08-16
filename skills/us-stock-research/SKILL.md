---
name: us-stock-research
description: Prepare, analyze, validate, and render evidence-backed U.S. stock research from SEC filings using EdgarTools and the active ChatGPT/Codex subscription session, without an OpenAI API key. Use when the user asks to analyze a U.S. company's latest 10-K risk factors, classify industry-wide versus company-specific risks, assess 3-5 year thesis impact, update a prior SEC risk report, or produce a Markdown report with verified SEC quotations.
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

## Guardrails

- Treat `manifest.json` and `item_1a.txt` as authoritative.
- Do not use legacy HTML or regex extractions when EdgarTools succeeds.
- Do not add news, market data, or memory unless the user requests an expanded report.
- Copy every `evidence_quote` verbatim without ellipses.
- Keep severity, probability, and thesis impact separate.
- Fail closed: never weaken quotation validation merely to produce a report.
- Preserve existing files unless the user requests cleanup.

## Updating a Report

Run `prepare` again. Reuse analysis only when the accession and source hash are unchanged. For a new filing, produce a new analysis JSON and report; retain the prior source bundle.
