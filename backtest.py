from __future__ import annotations

import datetime as dt
import json
import math
import os
import statistics
import time
import urllib.parse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import app


ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "work" / "backtest_market.json"
OUTPUT = ROOT / "docs" / "backtest.json"
CAPITAL = 500_000
COMMISSION = 0.001425
TAX = 0.003


def load_cache():
    try:
        data = json.loads(CACHE.read_text(encoding="utf-8"))
        return {day[0]["date"]: day for day in data if day}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def collect_three_years():
    token = os.environ.get("FINMIND_TOKEN", "").strip()
    if token:
        return collect_finmind(token)
    by_date = load_cache()
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
    start = today - dt.timedelta(days=365 * 3 + 70)
    cursor = start
    missing = []
    while cursor <= today:
        key = cursor.isoformat()
        if cursor.weekday() < 5 and key not in by_date:
            missing.append(cursor)
        cursor += dt.timedelta(days=1)
    for offset in range(0, len(missing), 40):
        batch = missing[offset:offset + 40]
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(app.fetch_market_day, day): day for day in batch}
            for future in as_completed(futures):
                day = futures[future]
                try:
                    rows = future.result()
                except Exception as error:
                    print(f"略過 {day}: {error}", flush=True)
                    continue
                if rows:
                    by_date[day.isoformat()] = rows
        save_cache(by_date)
        print(f"下載進度 {min(offset + len(batch), len(missing))}/{len(missing)}，有效交易日 {len(by_date)}", flush=True)
        time.sleep(1)
    save_cache(by_date)
    dates = sorted(by_date)
    if len(dates) < 700:
        raise RuntimeError(f"三年行情不完整：只有 {len(dates)} 個交易日")
    return [by_date[d] for d in dates[-780:]]


def collect_finmind(token):
    source = json.loads((ROOT / "docs" / "results.json").read_text(encoding="utf-8"))["stocks"]
    names = {row["code"]: row["name"] for row in source}
    codes = sorted(names)
    end = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
    start = end - dt.timedelta(days=365 * 3 + 70)

    def fetch(code):
        params = urllib.parse.urlencode({"dataset": "TaiwanStockPrice", "data_id": code,
                                         "start_date": start.isoformat(), "end_date": end.isoformat(),
                                         "token": token})
        payload = app.get_json(f"https://api.finmindtrade.com/api/v4/data?{params}")
        if payload.get("status") != 200:
            raise RuntimeError(payload.get("msg", f"FinMind {code} 讀取失敗"))
        converted = []
        for row in payload.get("data", []):
            close = app.number(row.get("close"))
            if close:
                converted.append({"code": code, "name": names[code], "date": row["date"], "close": close,
                                  "high": app.number(row.get("max"), close), "low": app.number(row.get("min"), close),
                                  "value": app.number(row.get("Trading_money")), "volume": app.number(row.get("Trading_Volume"))})
        return converted

    by_date = defaultdict(list)
    completed = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fetch, code): code for code in codes}
        for future in as_completed(futures):
            code = futures[future]
            rows = future.result()
            for row in rows:
                by_date[row["date"]].append(row)
            completed += 1
            if completed % 25 == 0:
                print(f"FinMind 下載進度 {completed}/{len(codes)}", flush=True)
    history = [by_date[d] for d in sorted(by_date) if by_date[d]]
    if len(history) < 700:
        raise RuntimeError(f"FinMind 三年行情不完整：只有 {len(history)} 個交易日")
    return history[-780:]


def save_cache(by_date):
    CACHE.parent.mkdir(exist_ok=True)
    ordered = [by_date[d] for d in sorted(by_date)]
    CACHE.write_text(json.dumps(ordered, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def ranks(history, index):
    window = history[index - 20:index + 1]
    series = defaultdict(list)
    for daily in window:
        for row in daily:
            series[row["code"]].append(row)
    candidates = []
    for code, rows in series.items():
        if len(rows) < 20:
            continue
        values = [r["value"] for r in rows[-20:]]
        if statistics.mean(values) < 50_000_000:
            continue
        close = [r["close"] for r in rows]
        returns = [(close[i] / close[i-1] - 1) * 100 for i in range(1, len(close))]
        volatility = statistics.pstdev(returns) or .01
        momentum5 = (close[-1] / close[-6] - 1) * 100
        ma5, ma10, ma20 = statistics.mean(close[-5:]), statistics.mean(close[-10:]), statistics.mean(close[-20:])
        trend = (int(close[-1] > ma5) + int(ma5 > ma10) + int(ma10 > ma20)) / 3
        volume_ratio = values[-1] / statistics.mean(values)
        candidates.append({"code": code, "name": rows[-1]["name"], "volatility": volatility,
                           "momentum": momentum5, "trend": trend, "volume_ratio": volume_ratio})
    def pct(key, value, reverse=False):
        values = [r[key] for r in candidates]
        score = 100 * sum(v <= value for v in values) / len(values)
        return 100 - score if reverse else score
    for row in candidates:
        low_vol = pct("volatility", row["volatility"], True)
        momentum = pct("momentum", row["momentum"])
        volume = pct("volume_ratio", row["volume_ratio"])
        row["stable"] = .50 * row["trend"] * 100 + .35 * low_vol + .15 * momentum
        row["growth"] = .45 * row["trend"] * 100 + .40 * momentum + .15 * volume
        row["strong"] = .30 * row["trend"] * 100 + .40 * momentum + .30 * volume
    return candidates


def trade(entry, future, horizon, position_cash):
    shares = math.floor(position_cash / entry["close"])
    if shares <= 0:
        return None
    entry_value = shares * entry["close"]
    exit_price = future[-1]["close"]
    reason = f"持有{horizon}日"
    stop, target = entry["close"] * .95, entry["close"] * 1.08
    for day in future:
        if day["low"] <= stop:
            exit_price, reason = stop, "停損"
            break
        if day["high"] >= target:
            exit_price, reason = target, "停利"
            break
    exit_value = shares * exit_price
    buy_fee = max(20, round(entry_value * COMMISSION))
    sell_fee = max(20, round(exit_value * COMMISSION))
    tax = round(exit_value * TAX)
    net = exit_value - entry_value - buy_fee - sell_fee - tax
    return {"return_pct": net / entry_value * 100, "net": net, "reason": reason}


def summarize(trades, initial_capital):
    if not trades:
        return {"trades": 0, "win_rate": 0, "avg_return": 0, "median_return": 0,
                "total_net": 0, "ending_equity": initial_capital, "max_drawdown": 0,
                "stop_rate": 0, "target_rate": 0}
    returns = [t["return_pct"] for t in trades]
    equity, peak, max_drawdown = initial_capital, initial_capital, 0
    batches = defaultdict(list)
    for item in trades:
        batches[item["batch"]].append(item)
    for batch in sorted(batches):
        equity += sum(t["net"] for t in batches[batch])
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, (equity / peak - 1) * 100)
    return {"trades": len(trades), "win_rate": sum(x > 0 for x in returns) / len(returns) * 100,
            "avg_return": statistics.mean(returns), "median_return": statistics.median(returns),
            "total_net": sum(t["net"] for t in trades), "ending_equity": equity,
            "max_drawdown": max(-100, max_drawdown),
            "stop_rate": sum(t["reason"] == "停損" for t in trades) / len(trades) * 100,
            "target_rate": sum(t["reason"] == "停利" for t in trades) / len(trades) * 100}


def run_backtest(history):
    maps = [{r["code"]: r for r in day} for day in history]
    results = {}
    for horizon in (3, 4, 5):
        for strategy in ("stable", "growth", "strong"):
            trades = []
            index = 20
            equity = CAPITAL
            batch_no = 0
            while index + horizon + 1 < len(history):
                signal = ranks(history, index)
                breadth = sum(r["trend"] >= 2 / 3 for r in signal) / len(signal) * 100 if signal else 0
                if breadth < 45 or equity <= 0:
                    index += horizon + 1
                    continue
                selected = sorted(signal, key=lambda r: r[strategy], reverse=True)[:5]
                entry_map = maps[index + 1]
                batch = []
                position_cash = equity / 5
                for pick in selected:
                    code = pick["code"]
                    if code not in entry_map:
                        continue
                    future = [maps[j][code] for j in range(index + 2, index + horizon + 2) if code in maps[j]]
                    if len(future) == horizon:
                        item = trade(entry_map[code], future, horizon, position_cash)
                        if item:
                            item["batch"] = batch_no
                            item["date"] = history[index][0]["date"]
                            batch.append(item)
                trades.extend(batch)
                equity += sum(item["net"] for item in batch)
                batch_no += 1
                index += horizon + 1
            split_date = history[int(len(history) * 2 / 3)][0]["date"]
            development = [t for t in trades if t["date"] < split_date]
            validation = [t for t in trades if t["date"] >= split_date]
            results[f"{strategy}_{horizon}"] = summarize(trades, CAPITAL)
            results[f"{strategy}_{horizon}"]["development"] = summarize(development, CAPITAL)
            results[f"{strategy}_{horizon}"]["validation"] = summarize(validation, CAPITAL)
            results[f"{strategy}_{horizon}"]["split_date"] = split_date
    return results


def main():
    history = collect_three_years()
    results = run_backtest(history)
    payload = {"start": history[20][0]["date"], "end": history[-1][0]["date"], "capital": CAPITAL,
               "commission": COMMISSION, "tax": TAX, "method": "技術面子策略（不含歷史營收、EPS及外資）",
               "results": results}
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    render_report(payload)
    print(f"回測完成：{payload['start']}～{payload['end']}，共 {len(history)} 個交易日")


def render_report(payload):
    labels = {"stable": "穩健", "growth": "成長", "strong": "強勢"}
    rows = []
    for strategy in ("stable", "growth", "strong"):
        for horizon in (3, 4, 5):
            item = payload["results"][f"{strategy}_{horizon}"]
            test = item["validation"]
            rows.append(f'''<tr><td>{labels[strategy]}</td><td>{horizon} 日</td><td>{item['trades']}</td><td>{item['win_rate']:.1f}%</td><td>{item['avg_return']:.2f}%</td><td>{item['ending_equity']:,.0f}</td><td>{item['max_drawdown']:.2f}%</td><td>{test['win_rate']:.1f}%</td><td>{test['avg_return']:.2f}%</td></tr>''')
    page = f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>V2C 策略驗證</title><style>body{{margin:0;background:#f4f6f9;color:#172033;font-family:system-ui,"Noto Sans TC",sans-serif}}main{{max-width:1050px;margin:auto;padding:24px 14px}}header{{padding:26px;border-radius:20px;background:linear-gradient(135deg,#312e81,#2563eb);color:white}}.card{{background:white;border-radius:16px;padding:18px;margin-top:18px;overflow:auto}}table{{width:100%;border-collapse:collapse;min-width:850px}}th,td{{padding:12px 9px;text-align:right;border-bottom:1px solid #e5e7eb}}th:first-child,td:first-child{{text-align:left}}.note{{color:#667085;line-height:1.7}}a{{color:#2563eb}}</style></head><body><main><header><h1>V2C 策略驗證</h1><p>{payload['start']}～{payload['end']} · 本金 NT$ {payload['capital']:,} · 大盤弱勢暫停進場</p></header><div class="card"><table><thead><tr><th>策略</th><th>持有</th><th>交易數</th><th>全期勝率</th><th>全期平均</th><th>期末資金</th><th>最大回撤</th><th>驗證期勝率</th><th>驗證期平均</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div><div class="card note"><b>回測口徑</b><p>每批 Top 5 平均配置可用資金；手續費 0.1425%、賣出交易稅 0.3%、最低手續費 20 元；固定停損 -5%、固定停利 +8%。均線廣度低於 45% 時不進場。最後約三分之一期間列為獨立驗證期。現階段仍是價格、成交量及均線子策略，不含歷史營收、EPS、外資，且以目前仍在候選池的股票回測，可能有存活者偏差。結果不代表未來績效。</p><a href="index.html">返回每日選股</a></div></main></body></html>'''
    (ROOT / "docs" / "backtest.html").write_text(page, encoding="utf-8")


if __name__ == "__main__":
    main()
