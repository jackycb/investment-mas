"""Field aliases and normalization helpers for financial statements."""

from __future__ import annotations

import math
import re
from datetime import date

STANDARD_FIELDS = [
    "revenue",
    "gross_profit",
    "operating_profit",
    "net_income",
    "operating_cash_flow",
    "free_cash_flow",
    "total_assets",
    "total_liabilities",
    "total_equity",
    "shares_outstanding",
    "gross_margin",
    "net_margin",
    "roe",
    "roic",
    "roa",
    "debt_ratio",
]

FIELD_ALIASES: dict[str, set[str]] = {
    "revenue": {"revenue", "total_revenue", "營業總收入", "营业总收入", "營業收入", "营业收入", "總收入", "总收入"},
    "gross_profit": {"gross_profit", "毛利", "毛利润"},
    "operating_profit": {"operating_profit", "operating_income", "營業利潤", "营业利润"},
    "net_income": {
        "net_income",
        "淨利潤",
        "净利润",
        "除稅後利潤",
        "除税后利润",
        "持續經營淨收入",
        "持续经营净收入",
        "本年度利润",
        "年度利润",
        "profitfortheyear",
        "profitfortheperiod",
        "profitattributabletoequityholders",
        "profitattributabletoownersofthecompany",
        "profitattributabletoordinaryshareholders",
        "归属于母公司股东的净利润",
        "归属于上市公司股东的净利润",
        "归母净利润",
    },
    "operating_cash_flow": {"operating_cash_flow", "經營活動現金淨額", "经营活动现金净额", "持續經營活動現金淨額", "持续经营活动现金净额"},
    "free_cash_flow": {"free_cash_flow", "自由現金流", "自由现金流"},
    "total_assets": {"total_assets", "資產總計", "资产总计", "總資產", "总资产"},
    "total_liabilities": {"total_liabilities", "負債總計", "负债总计", "總負債", "总负债"},
    "total_equity": {"total_equity", "股東權益總計", "股东权益总计", "股東權益合計", "股东权益合计", "總權益", "总权益"},
    "shares_outstanding": {"shares_outstanding", "總股本", "总股本", "已發行股份", "已发行股份"},
    "gross_margin": {"gross_margin", "毛利率"},
    "net_margin": {"net_margin", "淨利率", "净利率"},
    "roe": {"roe", "淨資產收益率", "净资产收益率", "ROE"},
    "roic": {"roic", "投入資本回報率", "投入资本回报率", "ROIC"},
    "roa": {"roa", "總資產收益率", "总资产收益率", "ROA"},
    "debt_ratio": {"debt_ratio", "資產負債率", "资产负债率"},
}


def normalize_label(label: str) -> str:
    """Normalize row labels for alias matching."""

    return re.sub(r"\s+", "", label.strip().lower().replace("（", "(").replace("）", ")"))


def parse_report_date(raw: str) -> tuple[int | None, date | None]:
    """Parse statement header date text into fiscal year and report date."""

    match = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", raw)
    if match:
        year, month, day = map(int, match.groups())
        return year, date(year, month, day)
    match = re.search(r"(\d{4})", raw)
    if match:
        year = int(match.group(1))
        return year, date(year, 12, 31)
    return None, None


def parse_numeric_value(raw: object) -> float | None:
    """Convert numeric strings with Chinese units into floats."""

    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        if isinstance(raw, float) and math.isnan(raw):
            return None
        return float(raw)

    text = str(raw).strip().replace("\ufeff", "")
    if not text or text in {"--", "—", "N/A", "nan", "None"}:
        return None

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]

    text = text.replace(",", "").replace("%", "").replace("股", "").replace("元", "")
    multiplier = 1.0
    for unit, factor in {"萬億": 1e12, "万亿": 1e12, "億": 1e8, "亿": 1e8, "萬": 1e4, "万": 1e4}.items():
        if text.endswith(unit):
            multiplier = factor
            text = text[: -len(unit)]
            break

    try:
        value = float(text) * multiplier
    except ValueError:
        return None
    return -value if negative else value


def map_raw_metric(label: str) -> str | None:
    """Map raw metric labels to standard fields."""

    normalized = normalize_label(label)
    for field, aliases in FIELD_ALIASES.items():
        if normalized in {normalize_label(alias) for alias in aliases}:
            return field
    return None


def finalize_financial_row(row: dict) -> dict:
    """Fill derived metrics where source statements do not provide them directly."""

    revenue = row.get("revenue")
    gross_profit = row.get("gross_profit")
    net_income = row.get("net_income")
    total_assets = row.get("total_assets")
    total_liabilities = row.get("total_liabilities")
    total_equity = row.get("total_equity")

    if row.get("gross_margin") is None and revenue and gross_profit is not None:
        row["gross_margin"] = gross_profit / revenue
    if row.get("net_margin") is None and revenue and net_income is not None:
        row["net_margin"] = net_income / revenue
    if row.get("roe") is None and total_equity and net_income is not None:
        row["roe"] = net_income / total_equity
    if row.get("roa") is None and total_assets and net_income is not None:
        row["roa"] = net_income / total_assets
    if row.get("debt_ratio") is None and total_assets and total_liabilities is not None:
        row["debt_ratio"] = total_liabilities / total_assets
    if row.get("free_cash_flow") is None and row.get("operating_cash_flow") is not None:
        row["free_cash_flow"] = row["operating_cash_flow"] * 0.85

    row["period_type"] = row.get("period_type") or "annual"
    row["fiscal_period"] = row.get("fiscal_period") or "FY"
    return row
