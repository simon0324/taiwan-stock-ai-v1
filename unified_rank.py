"""將既有獨立訊號合成一個可解釋、可向前驗證的實驗總分。"""
from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path

from event_news import TZ
from international_rank import returns_for

VERSION = "unified-1"
WEIGHTS = {"base": .75, "domestic": .10, "international": .05, "us": .10}


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def health(source, news, international, us, now):
    dates = {source.get("market_date"), news.get("market_date"), international.get("market_date"), us.get("market_date")}
    problems = []
    if None in dates or len(dates) != 1:
        problems.append("各評分檔案的台股行情日不一致")
    if source.get("data_warnings"):
        problems.append("原始選股使用備援資料")
    if not news.get("sources") or not all(x.get("status") == "ok" for x in news.get("sources", [])):
        problems.append("國內公告／新聞來源不完整")
    if international.get("source_status") != "正常":
        problems.append("國際事件來源不完整")
    if us.get("source_status") != "正常":
        problems.append("美股來源不完整")
    for label, payload in (("國內消息", news), ("國際事件", international), ("美股", us)):
        try:
            age = (now - dt.datetime.fromisoformat(payload["as_of"])).total_seconds()
            if not 0 <= age <= 86400:
                problems.append(f"{label}資料超過 24 小時")
        except (KeyError, TypeError, ValueError):
            problems.append(f"{label}缺少有效更新時間")
    return not problems, problems


def combine(stocks, news_rows, international_rows, us_rows, complete):
    news = {r["code"]: r for r in news_rows}
    international = {r["code"]: r for r in international_rows}
    us = {r["code"]: r for r in us_rows}
    rows = []
    for stock in stocks:
        code = stock["code"]
        missing = [name for name, table in (("國內消息", news), ("國際事件", international), ("美股", us)) if code not in table]
        news_score = min(float(stock.get("news", 0)) * 20, 100)
        # 原 overall 內含 5% 公告次數；先移除並重新正規化，避免消息重複計分。
        base = max(0.0, min(100.0, (float(stock["overall"]) - .05 * news_score) / .95))
        can_score = complete and not missing
        component_scores = {
            "base": base,
            "domestic": news.get(code, {}).get("message_score"),
            "international": international.get(code, {}).get("event_score"),
            "us": us.get(code, {}).get("us_score"),
        }
        total = sum(WEIGHTS[k] * component_scores[k] for k in WEIGHTS) if can_score else None
        rows.append({"code": code, "name": stock["name"], "original_score": stock["overall"],
                     "base_score_without_news": base, "domestic_score": component_scores["domestic"],
                     "international_score": component_scores["international"], "us_score": component_scores["us"],
                     "unified_score": total, "missing": missing,
                     "domestic_events": news.get(code, {}).get("events", []),
                     "international_events": international.get(code, {}).get("events", []),
                     "us_components": us.get(code, {}).get("components", [])})
    return sorted(rows, key=lambda r: (r["unified_score"] is None, -(r["unified_score"] or 0), r["code"]))


def track(snapshots, history):
    for snapshot in snapshots:
        observed_day = snapshot["observed_at"][:10]
        if not history or observed_day < history[0][0]["date"]:
            snapshot["tracking_status"] = "history_window_missing"
            continue
        sessions = [day for day in history if day and day[0]["date"] > observed_day]
        snapshot["tracking_status"] = "tracking"
        for variant in ("original", "unified"):
            for pick in snapshot[variant]:
                calculated = returns_for(pick, sessions)
                saved = pick.setdefault("outcomes", {})
                for horizon, outcome in calculated.items():
                    if saved.get(horizon, {}).get("status") != "complete":
                        saved[horizon] = outcome


def summary_rows(snapshots):
    rows = []
    for variant, label in (("original", "原版"), ("unified", "整合版")):
        for horizon in ("3", "4", "5"):
            outcomes = [p.get("outcomes", {}).get(horizon, {}) for s in snapshots for p in s[variant]]
            values = [o["net_return_pct"] for o in outcomes if o.get("status") == "complete"]
            average = f"{sum(values)/len(values):.2f}%" if values else "累積中"
            rows.append(f"<tr><td>{label}</td><td>{horizon} 日</td><td>{len(values)}</td><td>{average}</td></tr>")
    return "".join(rows)


def render(data, snapshots):
    if data["complete"]:
        cards = []
        for row in data["ranking"][:5]:
            cards.append(f'''<section><h3>{row['code']} {html.escape(row['name'])}</h3>
<p class="total">整合總分 {row['unified_score']:.2f}</p>
<p>基本／技術 {row['base_score_without_news']:.2f} × 75% · 國內消息 {row['domestic_score']:.2f} × 10% · 國際事件 {row['international_score']:.2f} × 5% · 美股隔夜 {row['us_score']:.2f} × 10%</p>
<p>美股構成：{html.escape('、'.join(row['us_components']))}；國內事件 {len(row['domestic_events'])} 則，國際事件 {len(row['international_events'])} 則。</p></section>''')
        top = "".join(cards)
    else:
        top = "<section><h3>本次不產生整合排名</h3><p>" + "；".join(html.escape(x) for x in data["problems"]) + "</p></section>"
    return f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>台股整合總分</title>
<style>body{{background:#f4f6f9;font-family:system-ui;color:#172033}}main{{max-width:950px;margin:auto;padding:20px}}section,table{{background:white;border-radius:14px;padding:17px;margin:12px 0}}p{{line-height:1.7}}.total{{font-size:22px;color:#1d4ed8;font-weight:800}}td,th{{padding:9px}}a{{color:#2563eb}}</style><main>
<h1>台股整合總分 Top 5（實驗）</h1><p>行情日 {data['market_date']} · 評估 {data['as_of']} · 狀態：{'完整' if data['complete'] else '資料不完整'}</p>
<p>基本／技術 75%＋國內消息 10%＋國際事件 5%＋美股隔夜 10%。原始分數既有的 5% 公告次數已移除後正規化，避免重複加權。分數是比較排序，不是上漲機率。</p>{top}
<h2>3～5 日向前比較</h2><table><tr><th>版本</th><th>持有</th><th>完成筆數</th><th>平均扣費報酬</th></tr>{summary_rows(snapshots)}</table>
<p>只有所有來源完整且行情日一致，才建立當日不可覆寫快照。觀察後下一交易日收盤進場，名目 10 萬元，納入手續費與交易稅；尚未計滑價、股息與漲跌停。這是新假設，需累積結果後才能判斷是否優於原版。</p>
<a href="unified_rank.json">完整分數</a> · <a href="unified_signals.json">驗證快照</a> · <a href="index.html">原版</a> · <a href="news.html">國內消息</a> · <a href="international_rank.html">國際事件</a> · <a href="us_market.html">美股隔夜</a></main></html>'''


def run(docs, now=None):
    now = now or dt.datetime.now(TZ)
    source = load(docs / "results.json", {})
    news = load(docs / "news_results.json", {})
    international = load(docs / "international_rank.json", {})
    us = load(docs / "us_market.json", {})
    complete, problems = health(source, news, international, us, now)
    ranking = combine(source.get("stocks", []), news.get("ranking", []), international.get("ranking", []), us.get("ranking", []), complete)
    data = {"rules_version": VERSION, "weights": WEIGHTS, "market_date": source.get("market_date"),
            "as_of": now.isoformat(), "complete": complete, "problems": problems, "ranking": ranking}
    snapshots = load(docs / "unified_signals.json", [])
    history = load(docs / "market_history.json", [])
    track(snapshots, history)
    market_date = source.get("market_date")
    if complete and market_date == now.date().isoformat() and not any(s["market_date"] == market_date for s in snapshots):
        original = sorted(source["stocks"], key=lambda r: (-r["overall"], r["code"]))[:5]
        snapshots.append({"rules_version": VERSION, "market_date": market_date, "observed_at": now.isoformat(),
                          "original": [{"code": r["code"], "name": r["name"]} for r in original],
                          "unified": [{"code": r["code"], "name": r["name"]} for r in ranking[:5]]})
    (docs / "unified_rank.json").write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (docs / "unified_signals.json").write_text(json.dumps(snapshots, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (docs / "unified_rank.html").write_text(render(data, snapshots), encoding="utf-8")
    print(f"整合總分：{'完整' if complete else '資料不完整'}；{len(snapshots)} 天快照")
    return data


if __name__ == "__main__":
    run(Path(__file__).resolve().parent / "docs")
