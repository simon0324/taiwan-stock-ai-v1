"""Prospective revenue-vs-EPS tracking. Decisions are immutable after signal day."""
from __future__ import annotations

import html
import json
import math
import statistics
from pathlib import Path

COMMISSION = .001425
TAX = .003
HORIZON = 5


def pct(values, value):
    return 100 * sum(v <= value for v in values) / len(values) if values else 0


def ranked_picks(rows):
    distributions = {key: [r[key] for r in rows] for key in ("momentum", "volume_ratio", "revenue", "eps")}
    scored = []
    for row in rows:
        momentum = pct(distributions["momentum"], row["momentum"])
        volume = pct(distributions["volume_ratio"], row["volume_ratio"])
        revenue = pct(distributions["revenue"], row["revenue"])
        eps = pct(distributions["eps"], row["eps"])
        baseline = .30 * row["trend"] + .30 * momentum + .15 * volume + .25 * revenue
        scored.append({"code": row["code"], "name": row["name"],
                       "revenue_only": baseline, "with_eps": .8 * baseline + .2 * eps})
    return {variant: sorted(scored, key=lambda r: (-r[variant], r["code"]))[:5]
            for variant in ("revenue_only", "with_eps")}


def fees(entry, exit_price, shares):
    buy_value, sell_value = entry * shares, exit_price * shares
    return max(20, round(buy_value * COMMISSION)) + max(20, round(sell_value * COMMISSION)) + round(sell_value * TAX)


def complete(record, exit_price, reason, date):
    shares = record["shares"]
    entry_value = record["entry_price"] * shares
    cost = fees(record["entry_price"], exit_price, shares)
    record.update({"status": "completed", "exit_date": date, "exit_price": exit_price, "reason": reason,
                   "return_pct": ((exit_price * shares - entry_value - cost) / entry_value * 100) if entry_value else 0})


def advance(records, market_date, market_rows):
    market = {r["code"]: r for r in market_rows}
    for record in records:
        if record["status"] == "completed" or record["signal_date"] >= market_date:
            continue
        row = market.get(record["code"])
        if not row:
            continue
        if record["status"] == "waiting_entry":
            entry = row["close"]
            # A nominal NT$100,000 position keeps minimum-fee effects comparable.
            shares = math.floor(100_000 / entry)
            if shares:
                record.update({"status": "active", "entry_date": market_date, "entry_price": entry,
                               "shares": shares, "held_days": 0})
            continue
        record["held_days"] += 1
        stop, target = record["entry_price"] * .95, record["entry_price"] * 1.08
        if row["low"] <= stop:
            complete(record, stop, "stop", market_date)
        elif row["high"] >= target:
            complete(record, target, "target", market_date)
        elif record["held_days"] >= HORIZON:
            complete(record, row["close"], "five_day_close", market_date)


def summary(records, variant):
    done = [r for r in records if r["variant"] == variant and r["status"] == "completed"]
    returns = [r["return_pct"] for r in done]
    return {"completed": len(done), "win_rate": (sum(x > 0 for x in returns) / len(returns) * 100) if returns else None,
            "average_return": statistics.mean(returns) if returns else None,
            "signals": len({r["signal_date"] for r in records if r["variant"] == variant})}


def render(payload):
    labels = {"revenue_only": "營收基準", "with_eps": "基準 80%＋EPS 20%"}
    rows = []
    for variant in labels:
        item = payload["summary"][variant]
        win = "累積中" if item["win_rate"] is None else f"{item['win_rate']:.1f}%"
        avg = "累積中" if item["average_return"] is None else f"{item['average_return']:.2f}%"
        rows.append(f"<tr><td>{labels[variant]}</td><td>{item['signals']}</td><td>{item['completed']}</td><td>{win}</td><td>{avg}</td></tr>")
    return f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>每日向前驗證</title><style>body{{font-family:system-ui,sans-serif;background:#f4f6f9;color:#172033;margin:0}}main{{max-width:850px;margin:auto;padding:24px 16px}}
section{{background:#fff;border-radius:16px;padding:20px;margin:16px 0;overflow:auto}}table{{width:100%;border-collapse:collapse;min-width:570px}}th,td{{padding:12px;border-bottom:1px solid #ddd;text-align:right}}th:first-child,td:first-child{{text-align:left}}p,li{{line-height:1.7}}a{{color:#2563eb}}</style></head>
<body><main><h1>每日向前驗證</h1><p>資料日：{payload['market_date']} · 成長 Top 5 · 5 個交易日</p><section><table><thead><tr><th>版本</th><th>訊號日數</th><th>完成筆數</th><th>勝率</th><th>平均淨報酬</th></tr></thead><tbody>{''.join(rows)}</tbody></table></section>
<section><p>每個新交易日才新增一次訊號。訊號的下一個有行情交易日以收盤價進場，再觀察 5 個後續交易日；停損 -5%、停利 +8%，同日同時觸及先算停損。</p>
<p>每檔以名目 NT$100,000 計算整股，扣除買賣手續費各 0.1425%（最低 20 元）及賣出稅 0.3%。這是每日重疊的訊號研究，不是單一資金帳戶績效，也不會改變網站排名或下單。</p>
<p>資料會逐日累積，滿足進場日及 5 個後續交易日後才列入結果。免費資料或網站停止更新時，驗證也會停止累積。</p>
<p><a href="forward_validation.json">下載完整不可回填紀錄</a> · <a href="comparison.html">歷史同條件比較</a> · <a href="index.html">每日選股</a></p></section></main></body></html>'''


def update(rows, market_date, market_rows, docs: Path):
    docs.mkdir(parents=True, exist_ok=True)
    path = docs / "forward_validation.json"
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
        records = old.get("records", [])
    except (OSError, ValueError):
        records = []
    advance(records, market_date, market_rows)
    existing = {(r["signal_date"], r["variant"], r["code"]) for r in records}
    for variant, picks in ranked_picks(rows).items():
        for rank, pick in enumerate(picks, 1):
            key = (market_date, variant, pick["code"])
            if key not in existing:
                records.append({"signal_date": market_date, "variant": variant, "rank": rank,
                                "code": pick["code"], "name": pick["name"], "score": pick[variant],
                                "status": "waiting_entry"})
    payload = {"market_date": market_date, "rules_version": 1,
               "summary": {v: summary(records, v) for v in ("revenue_only", "with_eps")},
               "records": records}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (docs / "forward_validation.html").write_text(render(payload), encoding="utf-8")
    return payload
