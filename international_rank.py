"""Evidence-backed experimental international-event overlay; no trading."""
import datetime as dt
import html
import json
import math
import re
from pathlib import Path

from event_news import TZ

VERSION = "international-1"
PROFILES = {
    "2610": {"factor": "fuel", "name": "華航",
             "url": "https://calec.china-airlines.com/csr/en/management_risk.html",
             "evidence": "公司風險管理說明將燃油列為主要成本及國際經濟敏感風險。",
             "risk": "原油非航空燃油；需求、匯率、避險及票價轉嫁可能抵銷成本效果。"},
    "2618": {"factor": "fuel", "name": "長榮航",
             "url": "https://www.evaair.com/en-global/images/investor-conference-2024q2-en_tcm33-92897.pdf",
             "evidence": "公司簡報第 7 頁列示燃油均價、耗用量、燃油成本及避險覆蓋。僅支持成本關聯，不代表目前避險比例。",
             "risk": "歷史簡報只支持業務關聯；須再核對最新避險、需求與航空燃油價格。"},
    "2609": {"factor": "container", "name": "陽明",
             "url": "https://e-solution.yangming.com/News/press_release/PressContent.aspx?BulletinType=PressRelease&localSiteD=&uid=14788",
             "evidence": "2025/03/12 公司財報新聞稿說明 2024 年貨量與運價上升的營運環境。",
             "risk": "即期指數不等於公司長約運價；航線、貨量、船舶供給及成本可能抵銷效果。"},
}


def load(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def direction(title):
    """Conservative headline pattern. Conflicts/forecasts are not actionable."""
    if re.search(r"可能|預計|預測|預期|可望|有望|將|恐|擬|傳聞|傳出|考慮|否認|澄清|不會|未|止跌|止漲", title):
        return None
    factors = [("fuel", r"航空燃油|油價|原油價格"), ("container", r"SCFI|貨櫃運價|貨櫃運費")]
    hits = []
    for factor, subject in factors:
        for match in re.finditer("(?:" + subject + r")[^，。；、!?！？]{0,16}", title, re.I):
            clause = match.group()
            up = bool(re.search(r"上漲|大漲|走高|攀升|飆升|漲幅|升高|漲\d", clause))
            down = bool(re.search(r"下跌|大跌|走低|下滑|回落|跌幅|下降|跌\d", clause))
            if up != down:
                hits.append((factor, 1 if up else -1))
            elif up and down:
                return None
    return hits[0] if len(set(hits)) == 1 else None


def rank(stocks, events, now):
    result = []
    for stock in stocks:
        profile = PROFILES.get(stock["code"])
        links = []
        for event in events:
            parsed = direction(event["title"])
            if not profile or not parsed or profile["factor"] != parsed[0]:
                continue
            published = dt.datetime.fromisoformat(event["published_at"])
            age = (now - published).total_seconds() / 86400
            if not 0 <= age <= 7:
                continue
            sign = -parsed[1] if parsed[0] == "fuel" else parsed[1]
            # Media-only evidence: max experimental event tilt is half scale.
            points = sign * 25 * 2 ** (-age / 3)
            links.append({"event_id": event["id"], "title": event["title"], "url": event["url"],
                          "published_at": event["published_at"], "points": points,
                          "inference": "成本降低／運價上升的條件式偏多推論" if sign > 0 else "成本提高／運價下降的條件式偏空推論"})
        # Same-factor reposts do not stack. Opposing evidence offsets explicitly.
        plus = max((e["points"] for e in links if e["points"] > 0), default=0)
        minus = min((e["points"] for e in links if e["points"] < 0), default=0)
        event_score = 50 + plus + minus
        result.append({"code": stock["code"], "name": stock["name"], "original_score": stock["overall"],
                       "event_score": event_score, "event_adjustment": .1 * (plus + minus),
                       "weighted_score": .9 * stock["overall"] + .1 * event_score,
                       "company_evidence": profile, "events": links})
    return sorted(result, key=lambda r: (-r["weighted_score"], r["code"]))


def returns_for(pick, sessions):
    maps = [{r["code"]: r for r in day} for day in sessions]
    out = {}
    for horizon in (3, 4, 5):
        if len(maps) <= horizon:
            out[str(horizon)] = {"status": "waiting"}
            continue
        code = pick["code"]
        if any(code not in m for m in maps[:horizon + 1]):
            out[str(horizon)] = {"status": "missing_price"}
            continue
        entry, exit_price = maps[0][code]["close"], maps[horizon][code]["close"]
        shares = math.floor(100000 / (entry * 1.001425))
        while shares > 0 and shares * entry + max(20, round(shares * entry * .001425)) > 100000:
            shares -= 1
        if not shares:
            out[str(horizon)] = {"status": "insufficient_cash"}
            continue
        buy, sell = shares * entry, shares * exit_price
        fee = max(20, round(buy * .001425)) + max(20, round(sell * .001425)) + round(sell * .003)
        out[str(horizon)] = {"status": "complete", "entry_date": maps[0][code]["date"],
                             "exit_date": maps[horizon][code]["date"], "entry_price": entry,
                             "exit_price": exit_price, "net_return_pct": (sell - buy - fee) / buy * 100}
    return out


def track(snapshots, history):
    for snapshot in snapshots:
        # Never treat prices preceding real observation time as a simulated entry.
        day = snapshot["observed_at"][:10]
        if not history or day < history[0][0]["date"]:
            snapshot["tracking_status"] = "history_window_missing"
            continue
        sessions = [d for d in history if d and d[0]["date"] > day]
        snapshot["tracking_status"] = "tracking"
        for variant in ("original", "weighted"):
            for pick in snapshot[variant]:
                calculated = returns_for(pick, sessions)
                saved = pick.setdefault("outcomes", {})
                for horizon, outcome in calculated.items():
                    if saved.get(horizon, {}).get("status") != "complete":
                        saved[horizon] = outcome


def render(data, snapshots):
    def cards(rows):
        blocks = []
        for r in rows:
            p = r["company_evidence"]
            evidence = (f"<p>業務證據：<a href='{html.escape(p['url'], quote=True)}'>{html.escape(p['evidence'])}</a><br>反面因素：{html.escape(p['risk'])}</p>" if p else "<p>未建立公司曝險對照，不給事件加減分。</p>")
            events = ''.join(f"<li><a href='{html.escape(e['url'], quote=True)}'>{html.escape(e['title'])}</a>（{e['published_at']}）<br>{e['inference']}</li>" for e in r['events'])
            blocks.append(f"<section><h3>{r['code']} {html.escape(r['name'])}</h3><p>原分 {r['original_score']:.2f} · 事件分 {r['event_score']:.2f} · 事件淨影響 {r['event_adjustment']:+.2f} · 加權分 {r['weighted_score']:.2f}</p>{evidence}<ul>{events}</ul></section>")
        return ''.join(blocks) or '<p>目前沒有符合證據與方向規則的個股，不硬湊五檔。</p>'
    rows = []
    for variant, label in (("original", "原版"), ("weighted", "國際加權版")):
        for horizon in ('3', '4', '5'):
            outcomes = [p.get('outcomes', {}).get(horizon, {}) for s in snapshots for p in s[variant]]
            values = [o['net_return_pct'] for o in outcomes if o.get('status') == 'complete']
            avg = f"{sum(values)/len(values):.2f}%" if values else '累積中'
            rows.append(f'<tr><td>{label}</td><td>{horizon}日</td><td>{len(values)}</td><td>{avg}</td></tr>')
    positives = [r for r in data['ranking'] if r['event_adjustment'] > 0][:5]
    negatives = sorted((r for r in data['ranking'] if r['event_adjustment'] < 0), key=lambda r: r['event_adjustment'])[:5]
    return f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>國際事件加權選股</title><style>body{{background:#f4f6f9;font-family:system-ui;color:#172033}}main{{max-width:950px;margin:auto;padding:20px}}section{{background:white;border-radius:16px;padding:18px;margin:12px 0}}p,li{{line-height:1.8}}td,th{{padding:10px}}a{{color:#2563eb}}</style><main>
<h1>國際事件加權版（實驗）</h1><p>行情 {data['market_date']} · 評估 {data['as_of']}</p><p>新增國際事件層占 10%，原綜合分占 90%；無訊號事件分為 50。媒體方向訊號最多造成 ±2.5 分差異，3 日半衰、7 日到期，同類不堆疊。這是待驗證權重，不是上漲機率。</p>
<p>目前證據庫僅涵蓋華航、長榮航的燃油成本及陽明的貨櫃運價。其他公司保持中性；政策／衝突缺少明確方向或公司曝險則不加分。官方資料支持業務關聯，新聞方向與獲利影響仍是推論。原分已含消息因素，總新聞影響不只 10%。</p>
<p>來源狀態：{html.escape(data['source_status'])}。原版排名不變，不下單。</p><h2>事件偏多觀察（最多五檔）</h2>{cards(positives)}<h2>事件風險觀察（最多五檔）</h2>{cards(negatives)}
<h2>全候選股票加權 Top 5</h2>{cards(data['ranking'][:5])}<h2>原版與加權版向前比較</h2>
<p>只在當日行情與全部雷達來源正常時建立首次快照；觀察日後下一個交易日收盤進場，後續 3／4／5 日收盤比較，無停損停利。每筆名目 10 萬元，買賣手續費各 0.1425%（最低 20 元）與賣出稅 0.3%。尚未計滑價、股息、漲跌停；重疊訊號不是單一帳戶績效。</p>
<table><tr><th>版本</th><th>持有</th><th>完成筆數</th><th>平均扣費報酬</th></tr>{''.join(rows)}</table><p>同日快照不覆寫；缺行情不挪動進出場日，歷史視窗不足標示缺漏。需累積結果後才能判斷加權是否改善。</p>
<a href="international_rank.json">評分與來源</a> · <a href="international_signals.json">快照與所有追蹤狀態</a> · <a href="global_events.html">國際雷達</a> · <a href="index.html">原版選股</a></main></html>'''


def run(docs, now=None):
    now = now or dt.datetime.now(TZ)
    source = load(docs / 'results.json', {})
    radar = load(docs / 'global_events.json', {})
    healthy = bool(radar.get('sources')) and all(s['status'] == 'ok' for s in radar['sources'])
    fresh = bool(radar.get('as_of')) and 0 <= (now - dt.datetime.fromisoformat(radar['as_of'])).total_seconds() <= 86400
    events = radar.get('events', []) if healthy and fresh else []
    data = {'rules_version': VERSION, 'market_date': source['market_date'], 'as_of': now.isoformat(),
            'source_status': '正常' if healthy and fresh else '缺漏／過期：停用事件加分與新快照',
            'ranking': rank(source['stocks'], events, now)}
    snapshots = load(docs / 'international_signals.json', [])
    history = load(docs / 'market_history.json', [])
    track(snapshots, history)
    if healthy and fresh and source['market_date'] == now.date().isoformat() and not any(s['market_date'] == source['market_date'] for s in snapshots):
        snapshots.append({'rules_version': VERSION, 'market_date': source['market_date'], 'observed_at': now.isoformat(),
                          'original': sorted(source['stocks'], key=lambda r: (-r['overall'],r['code']))[:5],
                          'weighted': data['ranking'][:5]})
    for file, value in (('international_rank.json', data), ('international_signals.json', snapshots)):
        (docs / file).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    (docs / 'international_rank.html').write_text(render(data,snapshots),encoding='utf-8')
    print(f"國際加權：{sum(r['event_adjustment'] != 0 for r in data['ranking'])} 檔有加減分，{len(snapshots)} 天快照")
    return data


if __name__ == '__main__':
    run(Path(__file__).resolve().parent / 'docs')
