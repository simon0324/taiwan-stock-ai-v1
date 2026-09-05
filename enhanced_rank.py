"""台股強化實驗排名：相對強弱、法人、價量、財務品質及籌碼風險。"""
from __future__ import annotations

import datetime as dt
import html
import json
import statistics
import urllib.parse
from collections import defaultdict
from pathlib import Path

import app
from event_news import TZ
from international_rank import returns_for

VERSION = "enhanced-1"
WEIGHTS = {"technical": .25, "institutional": .20, "fundamental": .20, "risk": .10,
           "domestic": .10, "international": .05, "us": .10}


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def table(payload, title_contains=None):
    if payload.get("fields") is not None and payload.get("data") is not None:
        return payload.get("fields", []), payload.get("data", [])
    tables = payload.get("tables", [])
    selected = next((t for t in tables if not title_contains or title_contains in str(t.get("title", ""))), None)
    return (selected.get("fields", []), selected.get("data", [])) if selected else ([], [])


def rows_by_code(payload, code_field, title_contains=None):
    fields, values = table(payload, title_contains)
    result = {}
    for item in values:
        row = dict(zip(fields, item))
        code = str(row.get(code_field, "")).strip()
        if len(code) == 4 and code.isdigit():
            result[code] = row
    return result


def margin_rows(payload):
    """融資融券欄名重複，依官方固定欄位順序分開命名。"""
    _, values = table(payload, "融資融券彙總")
    result = {}
    for item in values:
        code = str(item[0]).strip() if item else ""
        if len(code) == 4 and code.isdigit() and len(item) >= 13:
            result[code] = {"融資前日餘額": item[5], "融資今日餘額": item[6],
                            "融券前日餘額": item[11], "融券今日餘額": item[12]}
    return result


def fetch_day(path, day, select_type):
    query = urllib.parse.urlencode({"date": day.replace("-", ""), "response": "json", "selectType": select_type})
    payload = app.get_json(f"{app.TWSE}/{path}?{query}")
    if payload.get("stat") != "OK":
        raise RuntimeError(f"{path} {day}: {payload.get('stat')}")
    return payload


def collect_institutional(days):
    totals = defaultdict(lambda: {"foreign": 0.0, "trust": 0.0, "days": 0})
    errors = []
    for day in days[-5:]:
        try:
            payload = fetch_day("fund/T86", day, "ALLBUT0999")
            rows = rows_by_code(payload, "證券代號")
            for code, row in rows.items():
                totals[code]["foreign"] += app.number(row.get("外陸資買賣超股數(不含外資自營商)"))
                totals[code]["trust"] += app.number(row.get("投信買賣超股數"))
                totals[code]["days"] += 1
        except Exception as error:
            errors.append(f"{day}: {type(error).__name__}")
    return dict(totals), errors, len(errors) == 0 and len(days[-5:]) == 5


def collect_risk(day):
    specs = {
        "margin": ("marginTrading/MI_MARGN", "ALL", "代號", "融資融券彙總"),
        "lending": ("lending/TWT72U", "SLBNLB", "證券代號", None),
        "daytrade": ("dayTrading/TWTB4U", "All", "證券代號", "當日沖銷交易標的"),
    }
    result, errors = {}, []
    for name, (path, select_type, field, title) in specs.items():
        try:
            payload = fetch_day(path, day, select_type)
            result[name] = margin_rows(payload) if name == "margin" else rows_by_code(payload, field, title)
            if not result[name]:
                raise RuntimeError("無個股資料")
        except Exception as error:
            errors.append(f"{name}: {type(error).__name__}")
            result[name] = {}
    return result, errors, not errors


def collect_fundamentals():
    info_rows = app.get_json(f"{app.OPEN}/opendata/t187ap03_L")
    industries = {str(r.get("公司代號", "")).strip(): str(r.get("產業別", "")).strip() for r in info_rows}
    financial = {}
    errors = []
    for suffix in ("ci", "basi", "fh", "ins", "bd", "mim"):
        try:
            rows = app.get_json(f"{app.OPEN}/opendata/t187ap06_L_{suffix}")
            for r in rows:
                code = str(r.get("公司代號", "")).strip()
                revenue = app.number(r.get("營業收入"))
                operating = app.number(r.get("營業利益（損失）"))
                financial[code] = {"period": f"{r.get('年度')}Q{r.get('季別')}",
                                   "eps": app.number(r.get("基本每股盈餘（元）")),
                                   "operating_margin": operating / revenue * 100 if revenue else None}
        except Exception as error:
            errors.append(f"{suffix}: {type(error).__name__}")
    return industries, financial, errors, bool(industries) and bool(financial)


def pct(values, value):
    return app.percentile([v for v in values if v is not None], value) if value is not None else None


def compute(stocks, history, industries, financial, institutional, risks, overlays, complete):
    series = defaultdict(list)
    for daily in history:
        for row in daily:
            series[row["code"]].append(row)
    latest_returns = {code: (rows[-1]["close"] / rows[-6]["close"] - 1) * 100 for code, rows in series.items() if len(rows) >= 6}
    market_return = statistics.median(latest_returns.values()) if latest_returns else 0
    industry_returns = defaultdict(list)
    for code, value in latest_returns.items():
        if industries.get(code):
            industry_returns[industries[code]].append(value)
    industry_medians = {key: statistics.median(values) for key, values in industry_returns.items()}
    prepared = []
    for stock in stocks:
        code, rows = stock["code"], series.get(stock["code"], [])
        fin, inst = financial.get(code), institutional.get(code)
        missing = []
        if len(rows) < 20: missing.append("20日行情")
        if not industries.get(code): missing.append("產業分類")
        if not fin or fin.get("operating_margin") is None: missing.append("財務品質")
        if not inst or inst.get("days") != 5: missing.append("法人5日")
        avg_volume = statistics.mean(r["volume"] for r in rows[-20:]) if len(rows) >= 20 else 0
        inst_raw = ((inst["foreign"] + 1.5 * inst["trust"]) / avg_volume) if inst and avg_volume else None
        ret = latest_returns.get(code)
        rel_market = ret - market_return if ret is not None else None
        rel_industry = ret - industry_medians.get(industries.get(code), ret) if ret is not None and industries.get(code) else None
        breakout = bool(rows and rows[-1]["close"] >= max(r["high"] for r in rows[-20:-1]) and stock["volume_ratio"] >= 1.5) if len(rows) >= 20 else False
        prepared.append({"stock": stock, "fin": fin, "missing": missing, "inst_raw": inst_raw,
                         "rel_market": rel_market, "rel_industry": rel_industry, "breakout": breakout})
    distributions = {key: [p[key] for p in prepared if p[key] is not None] for key in ("inst_raw", "rel_market", "rel_industry")}
    revenue_values = [p["stock"]["revenue"] for p in prepared]
    eps_values = [p["fin"]["eps"] for p in prepared if p["fin"]]
    margin_values = [p["fin"]["operating_margin"] for p in prepared if p["fin"] and p["fin"]["operating_margin"] is not None]
    rows = []
    for p in prepared:
        stock, code, fin = p["stock"], p["stock"]["code"], p["fin"]
        technical = None if p["rel_market"] is None or p["rel_industry"] is None else (
            .35 * stock["trend"] + .25 * pct(distributions["rel_market"], p["rel_market"]) +
            .25 * pct(distributions["rel_industry"], p["rel_industry"]) + .15 * (100 if p["breakout"] else 0))
        institution_score = pct(distributions["inst_raw"], p["inst_raw"])
        fundamental = None if not fin or fin.get("operating_margin") is None else (.45 * pct(revenue_values, stock["revenue"]) +
            .30 * pct(margin_values, fin["operating_margin"]) + .25 * pct(eps_values, fin["eps"]))
        margin = risks["margin"].get(code, {})
        lending = risks["lending"].get(code, {})
        daytrade = risks["daytrade"].get(code, {})
        margin_prev, margin_now = app.number(margin.get("融資前日餘額")), app.number(margin.get("融資今日餘額"))
        lend_prev = app.number(lending.get("前日借券餘額(1)股")); lend_now = app.number(lending.get("本日借券餘額股(4)=(1)+(2)-(3)"))
        day_value = (app.number(daytrade.get("當日沖銷交易買進成交金額")) + app.number(daytrade.get("當日沖銷交易賣出成交金額"))) / 2
        day_ratio = day_value / stock["avg_value"] if stock["avg_value"] else 0
        risk_score = 100.0
        if margin_prev and margin_now / margin_prev - 1 > .10 and stock["momentum"] < 0: risk_score -= 25
        if lend_prev and lend_now / lend_prev - 1 > .10: risk_score -= 20
        if day_ratio > .50: risk_score -= 20
        if stock["atr_pct"] >= 4.5: risk_score -= 20
        scores = {"technical": technical, "institutional": institution_score, "fundamental": fundamental,
                  "risk": max(0, risk_score), "domestic": overlays["domestic"].get(code),
                  "international": overlays["international"].get(code), "us": overlays["us"].get(code)}
        can_score = complete and not p["missing"] and all(value is not None for value in scores.values())
        total = sum(WEIGHTS[k] * scores[k] for k in WEIGHTS) if can_score else None
        rows.append({"code": code, "name": stock["name"], "enhanced_score": total, "scores": scores,
                     "details": {"relative_market_5d": p["rel_market"], "relative_industry_5d": p["rel_industry"],
                                 "volume_breakout_20d": p["breakout"], "institutional_intensity_5d": p["inst_raw"],
                                 "financial_period": fin.get("period") if fin else None,
                                 "eps": fin.get("eps") if fin else None, "eps_yoy": None,
                                 "operating_margin": fin.get("operating_margin") if fin else None,
                                 "margin_change_1d": (margin_now / margin_prev - 1) * 100 if margin_prev else None,
                                 "lending_change_1d": (lend_now / lend_prev - 1) * 100 if lend_prev else None,
                                 "daytrade_value_ratio": day_ratio * 100}, "missing": p["missing"]})
    return sorted(rows, key=lambda r: (r["enhanced_score"] is None, -(r["enhanced_score"] or 0), r["code"]))


def track(snapshots, history):
    for snapshot in snapshots:
        sessions = [d for d in history if d and d[0]["date"] > snapshot["observed_at"][:10]]
        for variant in ("unified", "enhanced"):
            for pick in snapshot[variant]:
                saved = pick.setdefault("outcomes", {})
                for horizon, outcome in returns_for(pick, sessions).items():
                    if saved.get(horizon, {}).get("status") != "complete": saved[horizon] = outcome


def performance(snapshots):
    rows = []
    for variant, label in (("unified", "原整合版"), ("enhanced", "強化版")):
        for horizon in ("3", "4", "5"):
            outcomes = [p.get("outcomes", {}).get(horizon, {}) for s in snapshots for p in s[variant]]
            values = [o["net_return_pct"] for o in outcomes if o.get("status") == "complete"]
            average = f"{sum(values) / len(values):.2f}%" if values else "累積中"
            rows.append(f"<tr><td>{label}</td><td>{horizon}日</td><td>{len(values)}</td><td>{average}</td></tr>")
    return "".join(rows)


def render(data, snapshots):
    cards = []
    for row in data["ranking"][:5] if data["complete"] else []:
        s, d = row["scores"], row["details"]
        cards.append(f'''<section><h3>{row['code']} {html.escape(row['name'])}</h3><b class="total">強化總分 {row['enhanced_score']:.2f}</b>
<p>趨勢強度 {s['technical']:.1f} · 法人籌碼 {s['institutional']:.1f} · 財務品質 {s['fundamental']:.1f} · 風險 {s['risk']:.1f} · 國內消息 {s['domestic']:.1f} · 國際 {s['international']:.1f} · 美股 {s['us']:.1f}</p>
<p>相對大盤5日 {d['relative_market_5d']:+.2f}% · 相對產業 {d['relative_industry_5d']:+.2f}% · 20日放量突破 {'是' if d['volume_breakout_20d'] else '否'} · 營業利益率 {d['operating_margin']:.2f}%</p></section>''')
    body = "".join(cards) if data["complete"] else f"<section>資料不完整，本次不發布排名：{html.escape('；'.join(data['problems']))}</section>"
    return f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>台股強化選股</title><style>body{{background:#f4f6f9;font-family:system-ui;color:#172033}}main{{max-width:950px;margin:auto;padding:20px}}section{{background:white;padding:18px;border-radius:15px;margin:12px 0}}p{{line-height:1.7}}.total{{font-size:22px;color:#1d4ed8}}a{{color:#2563eb}}</style><main>
<h1>台股強化選股 Top 5（實驗）</h1><p>行情日 {data['market_date']} · 評估 {data['as_of']} · {'資料完整' if data['complete'] else '資料不完整'}</p>
<p>價格趨勢25%、法人籌碼20%、營收與財務品質20%、風險10%、國內消息10%、國際事件5%、美股10%。分數不是上漲機率。</p>{body}
<p>法人為最近5個交易日外資＋投信（投信係數1.5）相對成交量排名；突破需收盤突破前19日高點且量比≥1.5。融資、借券、當沖與波動只作風險扣分。EPS年增目前標示未知，未以當期EPS冒充去年同期成長。</p>
<p>當沖資料在T日與T+1仍可能調整，T+2才是最終值，因此只作風險提示。新權重不取代整合版。</p>
<h2>3～5日向前比較</h2><table><tr><th>版本</th><th>持有</th><th>完成筆數</th><th>平均扣費報酬</th></tr>{performance(snapshots)}</table>
<a href="enhanced_rank.json">完整資料</a> · <a href="enhanced_signals.json">驗證快照</a> · <a href="unified_rank.html">原整合版</a> · <a href="index.html">首頁</a></main></html>'''


def run(docs, now=None):
    now = now or dt.datetime.now(TZ)
    source = load(docs / "results.json", {}); history = load(docs / "market_history.json", [])
    news = load(docs / "news_results.json", {}); international = load(docs / "international_rank.json", {}); us = load(docs / "us_market.json", {})
    dates = [d[0]["date"] for d in history if d]
    institutional, inst_errors, inst_ok = collect_institutional(dates)
    risks, risk_errors, risk_ok = collect_risk(source.get("market_date", ""))
    try: industries, financial, fin_errors, fin_ok = collect_fundamentals()
    except Exception as error: industries, financial, fin_errors, fin_ok = {}, {}, [type(error).__name__], False
    same_date = bool(source.get("market_date")) and all(x.get("market_date") == source["market_date"] for x in (news, international, us))
    overlay_ok = bool(news.get("sources")) and all(x.get("status") == "ok" for x in news.get("sources", [])) and international.get("source_status") == "正常" and us.get("source_status") == "正常"
    problems = inst_errors + risk_errors + fin_errors
    if not same_date: problems.append("各排名行情日不一致")
    if not overlay_ok: problems.append("消息／國際／美股來源不完整")
    complete = inst_ok and risk_ok and fin_ok and same_date and overlay_ok and not source.get("data_warnings")
    overlays = {"domestic": {r["code"]: r["message_score"] for r in news.get("ranking", [])},
                "international": {r["code"]: r["event_score"] for r in international.get("ranking", [])},
                "us": {r["code"]: r["us_score"] for r in us.get("ranking", [])}}
    ranking = compute(source.get("stocks", []), history, industries, financial, institutional, risks, overlays, complete)
    data = {"rules_version": VERSION, "weights": WEIGHTS, "market_date": source.get("market_date"), "as_of": now.isoformat(),
            "complete": complete, "problems": problems, "coverage": {"ranked": sum(r["enhanced_score"] is not None for r in ranking), "candidates": len(ranking)}, "ranking": ranking}
    snapshots = load(docs / "enhanced_signals.json", []); track(snapshots, history)
    if complete and source.get("market_date") == now.date().isoformat() and not any(s["market_date"] == source["market_date"] for s in snapshots):
        unified = load(docs / "unified_rank.json", {}).get("ranking", [])[:5]
        snapshots.append({"rules_version": VERSION, "market_date": source["market_date"], "observed_at": now.isoformat(),
                          "unified": [{"code": r["code"], "name": r["name"]} for r in unified],
                          "enhanced": [{"code": r["code"], "name": r["name"]} for r in ranking[:5]]})
    (docs / "enhanced_rank.json").write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (docs / "enhanced_signals.json").write_text(json.dumps(snapshots, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (docs / "enhanced_rank.html").write_text(render(data, snapshots), encoding="utf-8")
    print(f"強化版：{'完整' if complete else '資料不完整'}；可評分 {data['coverage']['ranked']}/{data['coverage']['candidates']}")
    return data


if __name__ == "__main__": run(Path(__file__).resolve().parent / "docs")
