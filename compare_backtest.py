"""Cache-only, paired EPS ablation. Never downloads data or reads a token."""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
from collections import defaultdict
from pathlib import Path

import backtest as bt


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"快取缺少或無法讀取：{path}；請先完成歷史資料收集。") from error


def fingerprint(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_inputs(root: Path):
    codes = sorted({str(r["code"]) for r in read_json(root / "docs/results.json")["stocks"]})
    if not codes:
        raise RuntimeError("股票池為空，不能比較。")
    prices, revenue, eps = {}, {}, {}
    for code in codes:
        prices[code] = sorted(read_json(root / f"work/finmind/{code}.json"), key=lambda r: r["date"])
        if not prices[code]:
            raise RuntimeError(f"{code} 行情快取為空。")
        seen = set()
        for row in prices[code]:
            if row["code"] != code or row["date"] in seen or row["close"] <= 0:
                raise RuntimeError(f"{code} 行情包含錯誤代碼、重複日期或非正價格。")
            seen.add(row["date"])
        for folder, target in (("finmind_revenue", revenue), ("finmind_eps", eps)):
            records = read_json(root / f"work/{folder}/{code}.json")
            target[code] = sorted(records, key=lambda r: (r["available"], r["yoy"]))
            if len({r["available"] for r in records}) != len(records):
                raise RuntimeError(f"{code} {folder} 有重複可用日期，請先確認資料口徑。")

    # Use the oldest per-symbol final date so a stale tail cannot change the pool.
    ends = {code: rows[-1]["date"] for code, rows in prices.items()}
    common_end = min(ends.values())
    by_date = defaultdict(list)
    for code in codes:
        for row in prices[code]:
            if row["date"] <= common_end:
                by_date[row["date"]].append(row)
    dates = sorted(by_date)[-780:]
    if len(dates) < 700:
        raise RuntimeError(f"共同截止日期 {common_end} 前只有 {len(dates)} 個交易日，不足 700 日。")
    history = [sorted(by_date[date], key=lambda r: r["code"]) for date in dates]
    manifest = {
        "codes": codes, "stock_count": len(codes), "trading_days": len(history),
        "warmup_start": dates[0], "signal_start": dates[20], "end": common_end,
        "cache_end_by_code": ends,
        "empty_revenue_codes": [c for c in codes if not revenue[c]],
        "empty_eps_codes": [c for c in codes if not eps[c]],
        "missing_policy": "無已可用營收或 EPS 年增率時以 0 計分；兩組均保留該股票。",
    }
    manifest["input_sha256"] = fingerprint({"history": history, "revenue": revenue, "eps": eps})
    return history, revenue, eps, manifest


def compare(history, revenue, eps):
    results = {}
    for label, weight in (("revenue_only", 0.0), ("with_eps", 0.2)):
        print(f"執行 {label}，EPS 權重 {weight:.0%}", flush=True)
        raw = bt.run_backtest(history, revenue, eps, eps_weight=weight, strategies=("growth",))
        for item in raw.values():
            item["portfolio_return"] = (item["ending_equity"] / bt.CAPITAL - 1) * 100
            # The reused later period is descriptive, not an independent test.
            later = item.pop("validation")
            item.pop("development")
            item["later_period"] = {key: later[key] for key in ("trades", "win_rate", "avg_return")}
        results[label] = raw
    return results


def render(payload):
    m = payload["manifest"]
    rows = []
    for horizon in (3, 4, 5):
        for variant, label in (("revenue_only", "營收基準"), ("with_eps", "基準 80%＋EPS 20%")):
            r = payload["results"][variant][f"growth_{horizon}"]
            later = r["later_period"]
            rows.append(f"<tr><td>{horizon} 日</td><td>{label}</td><td>{r['trades']}</td>"
                        f"<td>{r['portfolio_return']:.2f}%</td><td>{r['ending_equity']:,.0f}</td>"
                        f"<td>{r['max_drawdown']:.2f}%</td><td>{r['avg_return']:.2f}%</td>"
                        f"<td>{later['avg_return']:.2f}%</td></tr>")
    return f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>EPS 同條件比較</title>
<style>body{{font-family:system-ui,sans-serif;background:#f4f6f9;color:#172033;margin:0}}
main{{max-width:1100px;margin:auto;padding:24px 16px}}section{{background:white;padding:20px;border-radius:16px;margin:16px 0}}
.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;min-width:850px}}td,th{{padding:12px;text-align:right;border-bottom:1px solid #ddd}}
td:nth-child(2),th:nth-child(2){{text-align:left}}p,li{{line-height:1.7}}code{{overflow-wrap:anywhere}}a{{color:#2563eb}}</style></head>
<body><main><h1>EPS 同條件比較</h1><p>成長策略 · {m['signal_start']}～{m['end']} · {m['stock_count']} 檔共同股票池</p>
<section><p>共用同一份行情、基本面與交易規則，各自從 NT$ {bt.CAPITAL:,} 開始。固定比較，不自動挑選勝出策略、不下單。</p>
<p>營收基準：均線 30%、動能 30%、量能 15%、營收 25%。EPS 版本：上述基準乘 80%，再加 EPS 年增率百分位分數 20%。
這是新定義的受控比較，不是重現舊 EPS 報告的權重。</p></section>
<section class="scroll"><table><thead><tr><th>持有</th><th>版本</th><th>交易數</th><th>資金報酬</th><th>期末資金</th>
<th>批次結算回撤</th><th>單筆平均</th><th>後段單筆平均</th></tr></thead><tbody>{''.join(rows)}</tbody></table></section>
<section><h2>閱讀限制</h2><ul>
<li>共用訊號排程，訊號次日收盤價進場；Top 5 等額配置，均線廣度低於 45% 暫停進場。同分以股票代碼排序。</li>
<li>固定停損 -5%、停利 +8%；同日兩者觸及先停損。持有 3／4／5 個後續交易日；不計滑價、跳空、漲跌停及成交限制。</li>
<li>沿用舊模擬：買賣手續費各 0.1425%、最低 20 元，賣出稅 0.3%；買入股數未預留手續費。缺少完整出場行情的交易略過，可能產生偏差。</li>
<li>回撤僅以每批交易結算計算，不是每日市值或盤中最大回撤；後段為最後約三分之一期間，已反覆檢視，不是獨立驗證。</li>
<li>以當前候選股票池回看歷史，存在存活者與選樣偏差；沒有歷史處置／全額交割名單，尚不能完整還原當時排除條件。</li>
<li>營收、EPS 使用估計可用日期，未核對實際公告時刻及事後財報修訂，不能保證無前視偏差。無已可用值時以 0 計分；
營收空資料 {len(m['empty_revenue_codes'])} 檔、EPS 空資料 {len(m['empty_eps_codes'])} 檔，不代表歷史完整覆蓋。</li>
<li>共同截止日採各股票快取末日的最早一天；不代表最新行情。回測不代表未來績效，也不能證明 EPS 有因果效果。</li></ul></section>
<section><p>資料指紋 SHA-256：<code>{html.escape(m['input_sha256'])}</code></p>
<p><a href="comparison.json">下載結果與股票池清單</a> · <a href="backtest.html">原回測報告</a> · <a href="index.html">每日選股</a></p></section>
</main></body></html>'''


def main():
    history, revenue, eps, manifest = load_inputs(bt.ROOT)
    payload = {"created_at": dt.datetime.now(dt.timezone.utc).isoformat(), "manifest": manifest,
               "eps_weight": 0.2, "baseline_weights": {"trend": .30, "momentum": .30, "volume": .15, "revenue": .25},
               "capital": bt.CAPITAL, "results": compare(history, revenue, eps)}
    output = bt.ROOT / "docs"
    (output / "comparison.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (output / "comparison.html").write_text(render(payload), encoding="utf-8")
    print(f"EPS 比較完成；共同截止日 {manifest['end']}；{manifest['stock_count']} 檔。", flush=True)


if __name__ == "__main__":
    main()
