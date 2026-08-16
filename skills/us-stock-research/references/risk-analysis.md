# Risk analysis contract

Analyze only the prepared SEC Item 1A text. Produce exactly ten distinct risks ordered by long-term investment importance.

## Required judgments

- `importance`: `高`, `中`, or `低`.
- `category`: `行业共有风险`, company-specific risk using the actual company name, or `两者兼有`.
- `changes_investment_thesis`: whether the risk could change the 3-5 year thesis, not merely create quarterly volatility.
- `evidence_quote`: one continuous, verbatim English excerpt from Item 1A. Use complete words, no translation, no ellipses, preferably one to three sentences.

## JSON shape

```json
{
  "executive_summary": "Chinese summary",
  "risks": [
    {
      "rank": 1,
      "title": "Chinese title",
      "importance": "高",
      "category": "两者兼有",
      "summary": "Chinese summary",
      "risk_mechanism": "Chinese causal mechanism",
      "investment_impact_3_5y": "Chinese 3-5 year impact",
      "changes_investment_thesis": true,
      "thesis_change_reason": "Chinese explanation",
      "evidence_quote": "Verbatim English SEC excerpt"
    }
  ]
}
```

Use ranks 1 through 10 exactly once. Do not add keys outside this shape. Do not classify every risk as high. A severe but recoverable event can remain medium when it does not alter durable earnings or competitive power.
