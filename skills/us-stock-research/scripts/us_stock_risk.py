"""Prepare and render SEC 10-K risk research without calling an AI API."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Literal

from edgar import Company, set_identity
from pydantic import BaseModel, Field


class RiskItem(BaseModel):
    rank: int = Field(ge=1, le=10)
    title: str
    importance: Literal["高", "中", "低"]
    category: str
    summary: str
    risk_mechanism: str
    investment_impact_3_5y: str
    changes_investment_thesis: bool
    thesis_change_reason: str
    evidence_quote: str


class RiskAnalysis(BaseModel):
    executive_summary: str
    risks: list[RiskItem] = Field(min_length=10, max_length=10)


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def normalize_for_match(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(
        str.maketrans(
            {
                "“": '"',
                "”": '"',
                "‘": "'",
                "’": "'",
                "–": "-",
                "—": "-",
                "\u00a0": " ",
            }
        )
    )
    return re.sub(r"\s+", " ", text).strip().casefold()


def project_relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def resolve_path(value: str | Path, root: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def prepare(args: argparse.Namespace) -> int:
    root = args.project_root.resolve()
    ticker = args.ticker.upper().strip()
    set_identity(
        os.getenv("SEC_IDENTITY", "us_stock_ai research fxl425@gmail.com")
    )

    company = Company(ticker)
    filing = company.get_filings(form="10-K").latest()
    risk_text = str(filing.obj()["Item 1A"]).strip()
    if len(risk_text) < 10_000:
        raise RuntimeError(
            f"Item 1A extraction is unexpectedly short ({len(risk_text):,} chars)."
        )

    filing_date = str(filing.filing_date)
    accession = str(filing.accession_no)
    year = filing_date[:4]
    cik = str(company.cik).lstrip("0")
    bundle = root / "data" / "sec" / ticker / accession
    source_path = bundle / "item_1a.txt"
    manifest_path = bundle / "manifest.json"
    analysis_path = root / "data" / "analysis" / f"{ticker}_{year}_risk_analysis.json"
    report_path = root / "reports" / f"{ticker}_{year}_risk_analysis.md"

    atomic_write(source_path, risk_text + "\n")
    manifest = {
        "company_name": company.name,
        "ticker": ticker,
        "form": "10-K",
        "filing_date": filing_date,
        "accession_no": accession,
        "item": "Item 1A",
        "source_url": (
            "https://www.sec.gov/Archives/edgar/data/"
            f"{cik}/{accession.replace('-', '')}/"
        ),
        "source_path": project_relative(source_path, root),
        "source_chars": len(risk_text),
        "source_sha256": hashlib.sha256(risk_text.encode()).hexdigest(),
        "analysis_json_path": project_relative(analysis_path, root),
        "report_path": project_relative(report_path, root),
    }
    atomic_write(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )
    print(
        json.dumps(
            {
                "status": "prepared",
                "manifest_path": str(manifest_path),
                "source_path": str(source_path),
                "analysis_json_path": str(analysis_path),
                "report_path": str(report_path),
                "source_chars": len(risk_text),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def load_bundle(
    manifest_path: Path, root: Path
) -> tuple[dict[str, object], str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_path = resolve_path(str(manifest["source_path"]), root)
    risk_text = source_path.read_text(encoding="utf-8").strip()
    digest = hashlib.sha256(risk_text.encode()).hexdigest()
    if digest != manifest["source_sha256"]:
        raise ValueError("Prepared Item 1A hash does not match manifest.")
    return manifest, risk_text


def validate_analysis(analysis: RiskAnalysis, risk_text: str) -> None:
    ranks = [risk.rank for risk in analysis.risks]
    if sorted(ranks) != list(range(1, 11)):
        raise ValueError(f"Risk ranks must be exactly 1-10; received {ranks}.")

    source = normalize_for_match(risk_text)
    failures = []
    for risk in analysis.risks:
        quote = risk.evidence_quote.strip()
        if len(quote) < 80:
            failures.append(f"#{risk.rank} evidence is too short")
        elif normalize_for_match(quote) not in source:
            failures.append(f"#{risk.rank} evidence is not an exact SEC excerpt")
    if failures:
        raise ValueError(
            "Evidence validation failed; report was not written. "
            + "; ".join(failures)
        )


def md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def render_markdown(analysis: RiskAnalysis, manifest: dict[str, object]) -> str:
    risks = sorted(analysis.risks, key=lambda item: item.rank)
    thesis_risks = [risk for risk in risks if risk.changes_investment_thesis]
    ticker = str(manifest["ticker"])
    year = str(manifest["filing_date"])[:4]
    company = str(manifest["company_name"])
    lines = [
        f"# {ticker} {year} 10-K 投资风险分析",
        "",
        f"> 本报告仅供投资研究参考，不构成投资建议。分析以 {company} 最新 10-K 的 Item 1A 为唯一事实来源。",
        "",
        "## 文件与方法",
        "",
        f"- 公司：{company}（{ticker}）",
        f"- 申报日期：{manifest['filing_date']}",
        f"- Accession Number：`{manifest['accession_no']}`",
        f"- SEC 原始申报：[查看 EDGAR 文件目录]({manifest['source_url']})",
        "- AI 分析：ChatGPT/Codex 订阅会话（未调用 OpenAI API）",
        f"- 报告生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        "- 方法：EdgarTools 提取；Codex 归纳；程序逐条核验引文。",
        "",
        "## 核心结论",
        "",
        analysis.executive_summary.strip(),
        "",
        "## 十大风险总表",
        "",
        "| 排名 | 风险 | 重要性 | 类型 | 可能改变 3–5 年投资逻辑 |",
        "|---:|---|---|---|:---:|",
    ]
    for risk in risks:
        lines.append(
            f"| {risk.rank} | {md_escape(risk.title)} | {risk.importance} | "
            f"{md_escape(risk.category)} | "
            f"{'是' if risk.changes_investment_thesis else '否'} |"
        )

    lines.extend(["", "## 十项风险详细分析", ""])
    for risk in risks:
        lines.extend(
            [
                f"### {risk.rank}. {risk.title}",
                "",
                f"- **重要性：** {risk.importance}",
                f"- **类型：** {risk.category}",
                f"- **风险概述：** {risk.summary}",
                f"- **传导机制：** {risk.risk_mechanism}",
                f"- **未来 3–5 年影响：** {risk.investment_impact_3_5y}",
                f"- **是否改变投资逻辑：** {'是' if risk.changes_investment_thesis else '否'}",
                f"- **判断理由：** {risk.thesis_change_reason}",
                "",
                "**SEC 原文证据：**",
                "",
                "> " + risk.evidence_quote.strip().replace("\n", "\n> "),
                "",
            ]
        )

    lines.extend(["## 可能改变未来 3–5 年投资逻辑的风险", ""])
    if thesis_risks:
        lines.extend(
            f"- **#{risk.rank} {risk.title}：** {risk.thesis_change_reason}"
            for risk in thesis_risks
        )
    else:
        lines.append("- 本次分析未识别出足以改变长期逻辑的风险。")
    lines.extend(
        [
            "",
            "## 说明",
            "",
            "- 重要性不等同于发生概率。",
            "- SEC 风险披露不代表风险一定发生。",
            "- 报告未结合估值、实时行情或 Item 1A 以外的信息。",
            "",
        ]
    )
    return "\n".join(lines)


def render(args: argparse.Namespace) -> int:
    root = args.project_root.resolve()
    manifest_path = resolve_path(args.manifest, root)
    analysis_path = resolve_path(args.analysis_json, root)
    manifest, risk_text = load_bundle(manifest_path, root)
    analysis = RiskAnalysis.model_validate_json(
        analysis_path.read_text(encoding="utf-8")
    )
    validate_analysis(analysis, risk_text)
    output = args.output or resolve_path(str(manifest["report_path"]), root)
    output = resolve_path(output, root)
    atomic_write(output, render_markdown(analysis, manifest))
    print(
        json.dumps(
            {
                "status": "rendered",
                "report_path": str(output),
                "risk_count": len(analysis.risks),
                "verified_quote_count": len(analysis.risks),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare and render SEC 10-K risk research without an AI API."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--ticker", required=True)
    prepare_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    prepare_parser.set_defaults(handler=prepare)

    render_parser = commands.add_parser("render")
    render_parser.add_argument("--manifest", type=Path, required=True)
    render_parser.add_argument("--analysis-json", type=Path, required=True)
    render_parser.add_argument("--output", type=Path)
    render_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    render_parser.set_defaults(handler=render)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.handler(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
