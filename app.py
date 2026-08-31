from __future__ import annotations

import datetime as dt
import html
import json
import math
import re
import statistics
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
TWSE = "https://www.twse.com.tw/rwd/zh"
OPEN = "https://openapi.twse.com.tw/v1"
HEADERS = {"User-Agent": "Mozilla/5.0 Taiwan-stock-v1/1.0"}


def get_json(url: str):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def number(value, default=0.0):
    text = str(value or "").replace(",", "").replace("--", "").strip()
    try:
        return float(text)
    except ValueError:
        return default


def roc_date(text: str) -> dt.date:
    digits = re.sub(r"\D", "", text)
    if len(digits) != 7:
        raise ValueError(text)
    return dt.date(int(digits[:3]) + 1911, int(digits[3:5]), int(digits[5:]))


def fetch_market_day(day: dt.date):
    params = urllib.parse.urlencode({"date": day.strftime("%Y%m%d"), "type": "ALL", "response": "json"})
    payload = get_json(f"{TWSE}/afterTrading/MI_INDEX?{params}")
    if payload.get("stat") != "OK":
        return []
    tables = payload.get("tables", [])
    table = next((t for t in tables if "證券代號" in t.get("fields", []) and len(t.get("fields", [])) >= 9), None)
    if not table:
        return []
    fields = table["fields"]
    rows = []
    for values in table.get("data", []):
        row = dict(zip(fields, values))
        code = str(row.get("證券代號", "")).strip()
        if not re.fullmatch(r"\d{4}", code):
            continue
        close = number(row.get("收盤價"))
        value = number(row.get("成交金額"))
        volume = number(row.get("成交股數"))
        if close > 0:
            rows.append({"code": code, "name": row.get("證券名稱", ""), "date": day.isoformat(), "close": close, "value": value, "volume": volume})
    return rows


def trading_history(days=32):
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
    found = []
    cursor = today
    attempts = 0
    while len(found) < days and attempts < 65:
        if cursor.weekday() < 5:
            rows = fetch_market_day(cursor)
            if rows:
                found.append(rows)
        cursor -= dt.timedelta(days=1)
        attempts += 1
    if len(found) < 20:
        raise RuntimeError(f"歷史行情不足：只取得 {len(found)} 個交易日")
    return list(reversed(found))


def fetch_revenue():
    data = get_json(f"{OPEN}/opendata/t187ap05_L")
    return {str(r.get("公司代號", "")).strip(): number(r.get("營業收入-去年同月增減(%)")) for r in data}


def fetch_eps():
    result = {}
    for suffix in ("ci", "basi", "fh", "ins", "bd", "mim"):
        try:
            data = get_json(f"{OPEN}/opendata/t187ap06_L_{suffix}")
        except Exception:
            continue
        for row in data:
            code = str(row.get("公司代號", "")).strip()
            eps = number(row.get("基本每股盈餘（元）"))
            if code and (code not in result or eps > result[code]):
                result[code] = eps
    return result


def fetch_foreign(day: dt.date):
    params = urllib.parse.urlencode({"date": day.strftime("%Y%m%d"), "response": "json"})
    try:
        payload = get_json(f"{TWSE}/fund/TWT38U?{params}")
    except Exception:
        return {}
    table = next((t for t in payload.get("tables", []) if "證券代號" in t.get("fields", [])), None)
    if not table:
        return {}
    fields = table["fields"]
    result = {}
    for values in table.get("data", []):
        row = dict(zip(fields, values))
        code = str(row.get("證券代號", "")).strip()
        candidates = [v for k, v in row.items() if "買賣超股數" in k]
        if re.fullmatch(r"\d{4}", code) and candidates:
            result[code] = number(candidates[-1])
    return result


def fetch_exclusions():
    excluded = set()
    for endpoint in ("/announcement/punish", "/exchangeReport/TWT85U"):
        try:
            for row in get_json(OPEN + endpoint):
                code = str(row.get("Code", "")).strip()
                if re.fullmatch(r"\d{4}", code):
                    excluded.add(code)
        except Exception:
            pass
    return excluded


def fetch_news():
    result = defaultdict(int)
    try:
        for row in get_json(f"{OPEN}/opendata/t187ap04_L"):
            code = str(row.get("公司代號", "")).strip()
            if code:
                result[code] += 1
    except Exception:
        pass
    return result


def percentile(values, value):
    if not values:
        return 50.0
    return 100.0 * sum(v <= value for v in values) / len(values)


def build_scores(history):
    series = defaultdict(list)
    names = {}
    for daily in history:
        for row in daily:
            series[row["code"]].append(row)
            names[row["code"]] = row["name"]
    latest_day = dt.date.fromisoformat(history[-1][0]["date"])
    revenue = fetch_revenue()
    eps = fetch_eps()
    foreign = fetch_foreign(latest_day)
    exclusions = fetch_exclusions()
    news = fetch_news()
    raw = []
    for code, rows in series.items():
        if len(rows) < 20 or code in exclusions:
            continue
        recent = rows[-20:]
        avg_value = statistics.mean(r["value"] for r in recent)
        if avg_value < 50_000_000:
            continue
        closes = [r["close"] for r in rows]
        values = [r["value"] for r in rows]
        ma5 = statistics.mean(closes[-5:])
        ma10 = statistics.mean(closes[-10:])
        ma20 = statistics.mean(closes[-20:])
        momentum5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else 0
        volume_ratio = values[-1] / statistics.mean(values[-20:]) if statistics.mean(values[-20:]) else 0
        trend = (int(closes[-1] > ma5) + int(ma5 > ma10) + int(ma10 > ma20)) / 3 * 100
        raw.append({"code": code, "name": names[code], "price": closes[-1], "avg_value": avg_value,
                    "revenue": revenue.get(code, 0), "eps": eps.get(code, 0), "foreign": foreign.get(code, 0),
                    "momentum": momentum5, "volume_ratio": volume_ratio, "trend": trend, "news": news.get(code, 0)})
    keys = ("revenue", "eps", "foreign", "momentum", "volume_ratio")
    distributions = {k: [r[k] for r in raw] for k in keys}
    for row in raw:
        p = {k: percentile(distributions[k], row[k]) for k in keys}
        news_score = min(row["news"] * 20, 100)
        row["stable"] = .25*p["revenue"] + .25*p["eps"] + .15*p["foreign"] + .20*row["trend"] + .10*p["volume_ratio"] + .05*news_score
        row["growth"] = .30*p["revenue"] + .25*p["eps"] + .15*p["foreign"] + .15*row["trend"] + .10*p["momentum"] + .05*news_score
        row["strong"] = .15*p["revenue"] + .10*p["eps"] + .20*p["foreign"] + .20*p["volume_ratio"] + .20*p["momentum"] + .10*row["trend"] + .05*news_score
        row["overall"] = (row["stable"] + row["growth"] + row["strong"]) / 3
    return raw, latest_day


def card(row, index, score_key):
    reasons = [f"營收年增 {row['revenue']:.1f}%", f"EPS {row['eps']:.2f}", f"5日動能 {row['momentum']:.1f}%", f"量能 {row['volume_ratio']:.2f}倍"]
    return f'''<article class="stock"><div class="rank">{index}</div><div><h3>{html.escape(row['name'])} <small>{row['code']}</small></h3><p>{' · '.join(reasons)}</p></div><div class="score">{row[score_key]:.1f}<small>分</small></div></article>'''


def render(rows, latest_day):
    labels = [("stable", "穩健 Top 5"), ("growth", "成長 Top 5"), ("strong", "強勢 Top 5"), ("overall", "綜合 Top 5")]
    sections = []
    for key, label in labels:
        top = sorted(rows, key=lambda r: r[key], reverse=True)[:5]
        sections.append(f'<section id="{key}"><h2>{label}</h2>{"".join(card(r, i + 1, key) for i, r in enumerate(top))}</section>')
    stamp = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    page = f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>台股 AI 選股 V1</title><style>
:root{{--bg:#f4f6f9;--card:#fff;--ink:#172033;--muted:#667085;--brand:#1d4ed8}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:system-ui,"Noto Sans TC",sans-serif}}main{{max-width:860px;margin:auto;padding:24px 16px 60px}}header{{background:linear-gradient(135deg,#172554,#2563eb);color:white;border-radius:22px;padding:28px;margin-bottom:18px}}h1{{margin:0 0 8px;font-size:clamp(26px,6vw,40px)}}header p{{margin:5px 0;opacity:.85}}nav{{display:flex;gap:8px;overflow:auto;margin:16px 0}}nav a{{white-space:nowrap;background:white;color:var(--brand);padding:10px 14px;border-radius:999px;text-decoration:none;font-weight:700}}section{{margin-top:28px}}h2{{font-size:21px}}.stock{{display:grid;grid-template-columns:34px 1fr auto;align-items:center;gap:12px;background:var(--card);padding:15px;margin:10px 0;border-radius:16px;box-shadow:0 3px 16px #1720330d}}.rank{{display:grid;place-items:center;width:30px;height:30px;border-radius:50%;background:#dbeafe;color:#1d4ed8;font-weight:800}}h3{{margin:0 0 5px}}h3 small{{color:var(--muted);font-weight:500}}.stock p{{margin:0;color:var(--muted);font-size:13px;line-height:1.5}}.score{{font-size:24px;font-weight:800;color:var(--brand)}}.score small{{font-size:12px;margin-left:2px}}footer{{color:var(--muted);font-size:12px;margin-top:30px;line-height:1.6}}@media(max-width:520px){{.stock{{grid-template-columns:30px 1fr}}.score{{grid-column:2;font-size:18px}}}}
</style></head><body><main><header><h1>台股 AI 選股 V1</h1><p>3～5 個交易日觀察 · 僅台灣上市股票</p><p>行情日：{latest_day.isoformat()}　更新：{stamp}</p></header><nav>{''.join(f'<a href="#{k}">{v.replace(" Top 5", "")}</a>' for k,v in labels)}</nav>{''.join(sections)}<footer>篩選：近20日平均成交金額至少 5,000 萬元，並排除處置及變更交易股票。分數是規則式量化評分，不代表獲利保證，也不是買賣建議。資料來源：臺灣證券交易所 OpenAPI／公開資訊觀測站。</footer></main></body></html>'''
    DOCS.mkdir(exist_ok=True)
    (DOCS / "index.html").write_text(page, encoding="utf-8")
    (DOCS / "results.json").write_text(json.dumps({"market_date": latest_day.isoformat(), "stocks": rows}, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    history = trading_history()
    rows, latest_day = build_scores(history)
    if len(rows) < 20:
        raise RuntimeError(f"通過篩選的股票過少：{len(rows)}")
    render(rows, latest_day)
    print(f"完成：{latest_day}，候選 {len(rows)} 檔，頁面位於 docs/index.html")


if __name__ == "__main__":
    main()
