"""把 sec_analysis / dcf 产出的 Markdown 表格解析为结构化 JSON。
只解析真实存在的表格行，解析不到的键直接缺席——严禁编造数据。"""
import re

# 币种前缀：$/¥/£/€/HK$（20-F 人民币、港币披露与美元报告通用），数值保持单位无关
_CURRENCY = r"[¥$£€]?(?:HK)?\$?"
_MONEY = re.compile(rf"^{_CURRENCY}([\d,]+(?:\.\d+)?)M$")
_PCT = re.compile(r"^(-?[\d.]+)%$")
_NUM = re.compile(rf"^(-)?{_CURRENCY}([\d,]+(?:\.\d+)?)$")
_NEG_MONEY = re.compile(rf"^\({_CURRENCY}([\d,]+(?:\.\d+)?)M\)$")


def _to_number(raw: str):
    raw = raw.strip().replace("**", "")
    if m := _MONEY.match(raw):
        return float(m.group(1).replace(",", ""))
    if m := _NEG_MONEY.match(raw):
        return -float(m.group(1).replace(",", ""))
    if m := _PCT.match(raw):
        return float(m.group(1))
    if m := _NUM.match(raw):
        sign = -1.0 if m.group(1) else 1.0
        return sign * float(m.group(2).replace(",", ""))
    return None


def _section_lines(text: str, section: str):
    """产出指定 `## 小节` 下的所有行。"""
    in_sec = False
    for line in text.splitlines():
        if line.startswith("#"):
            # 任意级别的标题（含 ### 子小节）都是小节边界，
            # 否则 DCF 报告里 `## 估值结果` 之后的 `### 每股估值` 表
            # 会被误并入上一小节（每股内在价值 ≠ 股权内在价值）。
            in_sec = line.lstrip("#").strip().startswith(section)
            continue
        if in_sec:
            yield line


def _table_rows(text: str, section: str) -> list[tuple[str, str]]:
    """返回指定 `## 小节` 下表格的 (第一列, 第二列) 行。"""
    out = []
    for line in _section_lines(text, section):
        if line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 2 and set(cells[0]) - {":", "-", " "} and cells[0] not in ("指标", "项目"):
                out.append((cells[0], cells[1]))
    return out


def parse_analysis_metrics(text: str) -> dict:
    metrics = {}
    for key, raw in _table_rows(text, "财务指标") + _table_rows(text, "现金流"):
        if (v := _to_number(raw)) is not None:
            metrics[key] = v
    return metrics


def parse_dcf_valuation(text: str) -> dict:
    v: dict = {}
    kv = {k: raw for k, raw in _table_rows(text, "基本信息") + _table_rows(text, "估值结果")}
    if (p := _to_number(kv.get("当前股价", ""))) is not None:
        v["price"] = p
    for k, raw in kv.items():
        if "内在价值" in k:
            iv = _to_number(raw)
            if iv is not None:
                v["intrinsic_value_musd"] = iv
    # "### 每股估值" 小节：每股口径（与股价同量纲）；旧报告缺该小节时这些键缺席。
    # 表内键可能带 ** 加粗（| **每股内在价值** | **$127.42** |），先剥掉再比对
    ps = {k.replace("*", ""): raw for k, raw in _table_rows(text, "每股估值")}
    for key, out in [("每股内在价值", "intrinsic_value_per_share"),
                     ("25%安全边际价格", "safety_25_price"),
                     ("50%安全边际价格", "safety_50_price")]:
        if key in ps and (val := _to_number(ps[key])) is not None:
            v[out] = val
    years = []
    # 只认 Owner Earnings 小节里的 `| FY...` 年度行；
    # 敏感性分析表有 `| 20% | $47 | ...` 这类增长率行，全篇扫描会把它编造成年度数据
    for line in _section_lines(text, "Owner Earnings"):
        if not line.strip().startswith("| FY"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 5:
            oe = _to_number(cells[4])
            if oe is not None:
                years.append({"year": cells[0], "oe": oe, "revenue": _to_number(cells[5]) if len(cells) > 5 else None})
    if years:
        v["owner_earnings_by_year"] = years
    return v
