"""以歷史相似價量型態估計未來 3／5 日機率與區間；不是精確 K 線預言。"""
from __future__ import annotations

import datetime as dt
import html
import json
import math
import statistics
from pathlib import Path

from event_news import TZ
from international_rank import returns_for

VERSION = "kline-probability-1"


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def feature(rows, index):
    if index < 19:
        return None
    window = rows[index-19:index+1]
    closes = [float(r["close"]) for r in window]
    values = [float(r.get("value", 0)) for r in window]
    price = closes[-1]
    ma5, ma10, ma20 = statistics.mean(closes[-5:]), statistics.mean(closes[-10:]), statistics.mean(closes)
    momentum5 = (price / closes[-6] - 1) * 100
    position20 = (price / max(closes) - 1) * 100
    volume_ratio = values[-1] / statistics.mean(values) if statistics.mean(values) else 1.0
    trend = (int(price > ma5) + int(ma5 > ma10) + int(ma10 > ma20)) / 3 * 100
    ranges = []
    for i in range(max(1, len(window)-14), len(window)):
        high, low, previous = float(window[i]["high"]), float(window[i]["low"]), closes[i-1]
        ranges.append(max(high-low, abs(high-previous), abs(low-previous)))
    atr_pct = statistics.mean(ranges) / price * 100 if ranges and price else 0
    return (momentum5, position20, volume_ratio, trend, atr_pct)


def samples(history, horizon):
    by_code = {}
    for daily in history:
        for row in daily:
            by_code.setdefault(row["code"], []).append(row)
    result = []
    for rows in by_code.values():
        for index in range(19, len(rows)-horizon):
            x = feature(rows, index)
            start, end = float(rows[index]["close"]), float(rows[index+horizon]["close"])
            if x and start > 0:
                result.append((x, (end/start-1)*100))
    return result


def distance(a, b):
    scales = (10, 12, 1.5, 100, 5)
    return math.sqrt(sum(((x-y)/scale)**2 for x, y, scale in zip(a, b, scales)))


def quantile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[round((len(ordered)-1)*fraction)]


def estimate(current, training, neighbors=200):
    nearest = sorted(training, key=lambda item: distance(current, item[0]))[:neighbors]
    returns = [item[1] for item in nearest]
    if not returns:
        return None
    return {"probability_up": 100*sum(r > 0 for r in returns)/len(returns),
            "expected_return_pct": statistics.mean(returns), "low_return_pct": quantile(returns, .10),
            "high_return_pct": quantile(returns, .90), "neighbor_count": len(returns)}


def apply_history_shrinkage(forecast, market_days):
    """資料未滿約三年時，把極端機率收縮至中性，避免短期行情造成假信心。"""
    coverage = min(1.0, market_days / 750)
    raw = forecast["probability_up"]
    forecast["raw_probability_up"] = raw
    forecast["probability_up"] = 50 + (raw-50)*coverage
    forecast["expected_return_pct"] *= coverage
    forecast["history_coverage"] = coverage


def build(results, history):
    by_code = {}
    for daily in history:
        for row in daily:
            by_code.setdefault(row["code"], []).append(row)
    train3, train5 = samples(history, 3), samples(history, 5)
    rows = []
    for stock in results.get("stocks", []):
        # K線只負責判斷時機，不讓虧損、營收衰退或低品質股票單靠反彈型態進榜。
        if float(stock.get("eps", 0)) <= 0 or float(stock.get("revenue", 0)) <= 0 or float(stock.get("overall", 0)) < 55:
            continue
        series = by_code.get(stock["code"], [])
        x = feature(series, len(series)-1) if series else None
        if not x:
            continue
        f3, f5 = estimate(x, train3), estimate(x, train5)
        if not f3 or not f5:
            continue
        apply_history_shrinkage(f3, len(history))
        apply_history_shrinkage(f5, len(history))
        price = float(stock["price"])
        f5["price_low"] = price*(1+f5["low_return_pct"]/100)
        f5["price_high"] = price*(1+f5["high_return_pct"]/100)
        rows.append({"code": stock["code"], "name": stock["name"], "price": price,
                     "forecast_3d": f3, "forecast_5d": f5,
                     "signals": {"momentum_5d": x[0], "position_from_20d_high": x[1],
                                 "volume_ratio": x[2], "trend_score": x[3], "atr_pct": x[4]}})
    rows.sort(key=lambda r: (-r["forecast_5d"]["probability_up"], -r["forecast_5d"]["expected_return_pct"], r["code"]))
    return rows, {"3d_samples": len(train3), "5d_samples": len(train5), "unique_market_days": len(history)}


def track(snapshots, history):
    for snapshot in snapshots:
        future = [d for d in history if d and d[0]["date"] > snapshot["market_date"]]
        for pick in snapshot["picks"]:
            saved = pick.setdefault("outcomes", {})
            for horizon, outcome in returns_for(pick, future).items():
                if saved.get(horizon, {}).get("status") != "complete":
                    saved[horizon] = outcome


def confidence(meta):
    if meta["unique_market_days"] >= 750 and meta["5d_samples"] >= 50000:
        return "中"
    return "低（歷史期間不足3年）"


def render(data, snapshots):
    cards = []
    for row in data["ranking"][:5]:
        a, b, s = row["forecast_3d"], row["forecast_5d"], row["signals"]
        state = "偏多" if b["probability_up"] >= 58 else "偏空" if b["probability_up"] <= 42 else "盤整"
        cards.append(f'''<section><h2>{row['code']} {html.escape(row['name'])}</h2><p class="prob">5日上漲機率 {b['probability_up']:.1f}% · {state}</p>
<p>3日機率 {a['probability_up']:.1f}%；5日期望報酬 {b['expected_return_pct']:.2f}%（短歷史資料已向50%中性機率收縮）</p>
<p>5日統計區間（第10～90百分位）：{b['price_low']:.2f}～{b['price_high']:.2f}</p>
<p>5日動能 {s['momentum_5d']:.1f}% · 距20日高點 {s['position_from_20d_high']:.1f}% · 量比 {s['volume_ratio']:.2f} · 趨勢 {s['trend_score']:.0f} · ATR {s['atr_pct']:.1f}%</p></section>''')
    outcomes = [p.get("outcomes", {}).get("5", {}) for snap in snapshots for p in snap["picks"]]
    done = [o for o in outcomes if o.get("status") == "complete"]
    accuracy = f"完成 {len(done)} 筆，方向命中 {100*sum(o['net_return_pct'] > 0 for o in done)/len(done):.1f}%" if done else "尚在累積向前驗證"
    return f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>K線機率預測</title>
<style>body{{background:#f4f6f9;color:#172033;font-family:system-ui}}main{{max-width:920px;margin:auto;padding:20px}}section{{background:white;border-radius:15px;padding:17px;margin:12px 0}}.prob{{font-size:22px;color:#1d4ed8;font-weight:800}}p{{line-height:1.65}}a{{color:#2563eb}}</style><main>
<h1>K線機率預測 Top 5（觀察版）</h1><p>行情日 {data['market_date']} · 模型信心：{data['confidence']}</p>
<p>依歷史相似的價量、均線位置與波動估計，不是未來精確K線，也不是保證上漲。先套用正EPS、營收正成長及原綜合分至少55分的品質門檻。現有資料僅 {data['training']['unique_market_days']} 個交易日，因此只可觀察。</p>
{''.join(cards)}<section><h2>向前驗證</h2><p>{accuracy}</p><p>每日固定保存預測，追蹤3、4、5個交易日；未累積足夠樣本前不加入正式總分。</p></section>
<a href="kline_forecast.json">完整資料</a> · <a href="index.html">返回首頁</a></main></html>'''


def run(docs, now=None):
    now = now or dt.datetime.now(TZ)
    results, history = load(docs/"results.json", {}), load(docs/"market_history.json", [])
    ranking, training = build(results, history)
    market_date = results.get("market_date")
    snapshots = load(docs/"kline_signals.json", [])
    track(snapshots, history)
    if market_date == now.date().isoformat() and not any(s.get("market_date") == market_date for s in snapshots):
        snapshots.append({"rules_version": VERSION, "market_date": market_date, "observed_at": now.isoformat(),
                          "picks": [{"code": r["code"], "name": r["name"], "predicted_up_probability": r["forecast_5d"]["probability_up"]} for r in ranking[:5]]})
    data = {"rules_version": VERSION, "market_date": market_date, "as_of": now.isoformat(), "confidence": confidence(training),
            "training": training, "ranking": ranking, "note": "獨立觀察，不納入正式總分"}
    (docs/"kline_forecast.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (docs/"kline_signals.json").write_text(json.dumps(snapshots, ensure_ascii=False, indent=2), encoding="utf-8")
    (docs/"kline_forecast.html").write_text(render(data, snapshots), encoding="utf-8")
    print(f"K線機率預測：{len(ranking)} 檔；信心 {data['confidence']}")
    return data


if __name__ == "__main__":
    run(Path(__file__).resolve().parent/"docs")
