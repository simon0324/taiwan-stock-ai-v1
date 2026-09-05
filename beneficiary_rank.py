"""台積電／聯發科供應鏈受惠觀察表（獨立研究因子，不改正式排名）。"""
from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path

from event_news import TZ
from international_rank import returns_for

VERSION = "beneficiary-1"

# 僅收錄可由公司／交易所資料說明營運環節的上市公司；「關聯」不等於客戶訂單保證。
UNIVERSE = {
    "2449": {"themes": ["台積電先進製程", "聯發科AI／ASIC"], "link": "AI/HPC與先進製程晶片測試", "evidence": 90,
             "source": "https://www.kyec.com.tw/Upfiles/EDUp/files/%E8%82%A1%E6%9D%B1%E6%9C%83/20250527%E8%AD%B0%E4%BA%8B%E9%8C%84%28%E8%8B%B1%E6%96%87AGM%29.pdf"},
    "3711": {"themes": ["台積電先進封裝", "聯發科AI／ASIC"], "link": "AI/HPC先進封裝與測試", "evidence": 85,
             "source": "https://ase.aseglobal.com/VIPack/"},
    "3037": {"themes": ["台積電先進封裝", "聯發科AI／ASIC"], "link": "AI晶片與高階封裝所需ABF載板", "evidence": 80,
             "source": "https://investoredu.twse.com.tw/FileSystem/FileUpload/101afb1f-e736-40f2-a62a-95864b34b35f.pdf"},
    "3583": {"themes": ["台積電擴產"], "link": "半導體製程與先進封裝設備", "evidence": 75,
             "source": "https://www.twse.com.tw/staticFiles/news/event/ff808081786812360178c8ed241a0132.pdf"},
    "1560": {"themes": ["台積電先進製程"], "link": "晶圓製程耗材與再生晶圓", "evidence": 65,
             "source": "https://www.kinik.com.tw/"},
    "2383": {"themes": ["AI/HPC需求擴散"], "link": "高速運算用高階銅箔基板", "evidence": 65,
             "source": "https://www.emctw.com/"},
    "6239": {"themes": ["AI/HPC需求擴散"], "link": "記憶體與邏輯晶片封裝測試", "evidence": 60,
             "source": "https://www.pti.com.tw/"},
}


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def percentile(values, value):
    return 100 * sum(v <= value for v in values) / len(values) if values else 50.0


def pullback_score(drawdown):
    """偏好溫和回檔；避免把快速崩跌誤認成低檔。"""
    depth = abs(min(0.0, drawdown))
    if 5 <= depth <= 15:
        return 100.0
    if depth < 5:
        return 55.0
    if depth <= 25:
        return 65.0
    return 25.0


def build(results, history):
    stocks = {r["code"]: r for r in results.get("stocks", [])}
    all_rows = list(stocks.values())
    revs = [float(r.get("revenue", 0)) for r in all_rows]
    epss = [float(r.get("eps", 0)) for r in all_rows]
    foreign_available = any(abs(float(r.get("foreign", 0))) > 0 for r in all_rows)
    foreigns = [float(r.get("foreign", 0)) for r in all_rows]
    price_series = {}
    for daily in history:
        for row in daily:
            price_series.setdefault(row["code"], []).append(float(row["close"]))
    rows = []
    for code, meta in UNIVERSE.items():
        stock = stocks.get(code)
        if not stock:
            continue
        prices = price_series.get(code, [])[-20:]
        high = max(prices) if prices else float(stock["price"])
        drawdown = (float(stock["price"]) / high - 1) * 100 if high else 0.0
        quality = (percentile(revs, float(stock.get("revenue", 0))) + percentile(epss, float(stock.get("eps", 0)))) / 2
        components = {"evidence": meta["evidence"], "quality": quality, "pullback": pullback_score(drawdown),
                      "reversal": float(stock.get("trend", 0)), "foreign": percentile(foreigns, float(stock.get("foreign", 0))) if foreign_available else None}
        weighted = .30*components["evidence"] + .25*quality + .15*components["pullback"] + .20*components["reversal"]
        score = weighted / .90 if not foreign_available else weighted + .10*components["foreign"]
        rows.append({"code": code, "name": stock["name"], "price": stock["price"], "themes": meta["themes"],
                     "relationship": meta["link"], "source": meta["source"], "components": components,
                     "drawdown_20d_pct": drawdown, "revenue_yoy_pct": stock.get("revenue"), "eps": stock.get("eps"),
                     "observation_score": score, "foreign_data_available": foreign_available})
    return sorted(rows, key=lambda r: (-r["observation_score"], r["code"]))


def track(snapshots, history):
    for snapshot in snapshots:
        sessions = [d for d in history if d and d[0]["date"] > snapshot["market_date"]]
        for pick in snapshot["picks"]:
            outcomes = pick.setdefault("outcomes", {})
            for horizon, outcome in returns_for(pick, sessions).items():
                if outcomes.get(horizon, {}).get("status") != "complete":
                    outcomes[horizon] = outcome


def render(data, snapshots):
    cards = []
    for row in data["ranking"][:5]:
        c = row["components"]
        foreign = f"{c['foreign']:.1f}" if c["foreign"] is not None else "缺資料（其餘90%正規化）"
        cards.append(f'''<section><h2>{row['code']} {html.escape(row['name'])}</h2><p class="score">受惠觀察分 {row['observation_score']:.1f}</p>
<p><b>{html.escape(row['relationship'])}</b> · {html.escape('、'.join(row['themes']))}</p>
<p>證據 {c['evidence']:.0f}×30% · 財務品質 {c['quality']:.1f}×25% · 20日回檔位置 {c['pullback']:.1f}×15% · 均線止跌 {c['reversal']:.1f}×20% · 法人 {foreign}</p>
<p>目前價格 {row['price']:.2f}；距20日高點 {row['drawdown_20d_pct']:.1f}%；營收年增 {row['revenue_yoy_pct']:.1f}%；EPS {row['eps']:.2f}</p>
<a href="{html.escape(row['source'])}">關聯證據</a></section>''')
    completed = [p.get("outcomes", {}).get("5", {}) for s in snapshots for p in s["picks"]]
    values = [o["net_return_pct"] for o in completed if o.get("status") == "complete"]
    validation = f"已完成 {len(values)} 筆5日觀察，平均扣費報酬 {sum(values)/len(values):.2f}%" if values else "尚在累積3～5交易日向前結果"
    return f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>供應鏈受惠觀察</title>
<style>body{{background:#f4f6f9;color:#172033;font-family:system-ui}}main{{max-width:920px;margin:auto;padding:20px}}section{{background:#fff;border-radius:15px;padding:17px;margin:12px 0}}.score{{font-size:22px;color:#1d4ed8;font-weight:800}}p{{line-height:1.65}}a{{color:#2563eb}}</style><main>
<h1>台積電／聯發科供應鏈受惠 Top 5（觀察版）</h1><p>行情日 {data['market_date']} · 規則 {data['rules_version']}</p>
<p>這是產業關聯研究，不代表公司已取得特定客戶訂單，也不是上漲機率。溫和回檔得分較高；急跌不會被當成便宜。</p>{''.join(cards)}
<section><h2>向前驗證</h2><p>{validation}</p><p>觀察當日收盤後建立快照，追蹤3、4、5個交易日；未驗證前不併入正式Top 5。</p></section>
<a href="beneficiary_rank.json">完整資料</a> · <a href="index.html">返回首頁</a></main></html>'''


def run(docs, now=None):
    now = now or dt.datetime.now(TZ)
    results = load(docs / "results.json", {})
    history = load(docs / "market_history.json", [])
    ranking = build(results, history)
    market_date = results.get("market_date")
    snapshots = load(docs / "beneficiary_signals.json", [])
    track(snapshots, history)
    if market_date == now.date().isoformat() and not any(s.get("market_date") == market_date for s in snapshots):
        snapshots.append({"rules_version": VERSION, "market_date": market_date, "observed_at": now.isoformat(),
                          "picks": [{"code": r["code"], "name": r["name"]} for r in ranking[:5]]})
    data = {"rules_version": VERSION, "market_date": market_date, "as_of": now.isoformat(), "ranking": ranking,
            "note": "獨立觀察因子，未納入正式選股總分"}
    (docs / "beneficiary_rank.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (docs / "beneficiary_signals.json").write_text(json.dumps(snapshots, ensure_ascii=False, indent=2), encoding="utf-8")
    (docs / "beneficiary_rank.html").write_text(render(data, snapshots), encoding="utf-8")
    print(f"供應鏈受惠觀察：{len(ranking)} 檔候選")
    return data


if __name__ == "__main__":
    run(Path(__file__).resolve().parent / "docs")
