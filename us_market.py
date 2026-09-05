"""美股收盤後的台股實驗加權；免費來源、無下單。"""
from __future__ import annotations

import datetime as dt
import html
import json
import math
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

from event_news import TZ

VERSION = "us-overnight-1"
NY = ZoneInfo("America/New_York")
HEADERS = {"User-Agent": "Mozilla/5.0 Taiwan-stock-ai-v1/1.0"}
SYMBOLS = {
    "sp500": ("^GSPC", "S&P 500"), "nasdaq": ("^IXIC", "Nasdaq Composite"),
    "sox": ("^SOX", "費城半導體"), "vix": ("^VIX", "VIX"),
    "tsm": ("TSM", "台積電 ADR"), "nvda": ("NVDA", "NVIDIA"),
    "amd": ("AMD", "AMD"), "avgo": ("AVGO", "Broadcom"), "mu": ("MU", "Micron"),
}

# 官方公司頁只證明產業分類；SOX 對個股的短期方向仍是待驗證假設。
SEMIS = {
    "2330": ("台積電", "https://investor.tsmc.com/english", "公司投資人網站與美國存託憑證 TSM 可核對同一公司。"),
    "2303": ("聯電", "https://www.umc.com/en/Static/company_overview", "公司自述為全球半導體晶圓專工業者。"),
    "2454": ("聯發科", "https://www.mediatek.com/about", "公司自述為全球無晶圓廠半導體公司。"),
    "3034": ("聯詠", "https://www.novatek.com.tw/en-global/about/about", "公司頁說明顯示與影像處理 IC 業務。"),
    "3661": ("世芯-KY", "https://www.alchip.com/en/about.php", "公司自述提供 ASIC 與 SoC 設計服務。"),
    "3711": ("日月光投控", "https://www.aseglobal.com/about-us/", "公司頁說明半導體封裝、測試及系統服務。"),
}


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def fetch_chart(symbol, now):
    encoded = urllib.parse.quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=10d&interval=1d"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as response:
        payload = json.load(response)
    result = payload["chart"]["result"][0]
    closes = result["indicators"]["quote"][0]["close"]
    sessions = []
    ny_now = now.astimezone(NY)
    for stamp, close in zip(result["timestamp"], closes):
        if close is None:
            continue
        day = dt.datetime.fromtimestamp(stamp, NY).date()
        # 當日盤中日線不算完成；16:20 ET 後才接受。
        if day == ny_now.date() and ny_now.time() < dt.time(16, 20):
            continue
        sessions.append((day, float(close)))
    if len(sessions) < 6:
        raise RuntimeError(f"{symbol} 完整日線不足")
    latest, previous = sessions[-1], sessions[-2]
    return {"symbol": symbol, "date": latest[0].isoformat(), "close": latest[1],
            "return_1d": (latest[1] / previous[1] - 1) * 100,
            "return_5d": (latest[1] / sessions[-6][1] - 1) * 100, "url": url}


def collect(now):
    quotes, errors = {}, []
    for key, (symbol, name) in SYMBOLS.items():
        try:
            quote = fetch_chart(symbol, now)
            quote["name"] = name
            quotes[key] = quote
        except Exception as error:
            errors.append(f"{name}: {type(error).__name__}")
    required = {"sp500", "nasdaq", "sox", "vix"}
    dates = {quotes[k]["date"] for k in required if k in quotes}
    age = None
    if len(dates) == 1:
        age = (now.astimezone(NY).date() - dt.date.fromisoformat(next(iter(dates)))).days
    healthy = required <= quotes.keys() and len(dates) == 1 and age is not None and 0 <= age <= 4
    return quotes, errors, healthy


def unit(value, scale):
    return max(-1.0, min(1.0, value / scale))


def rank(stocks, quotes, healthy):
    if not healthy:
        return [{"code": s["code"], "name": s["name"], "original_score": s["overall"],
                 "us_score": 50, "us_adjustment": 0, "weighted_score": s["overall"],
                 "semiconductor_evidence": None, "components": []} for s in stocks]
    market = (10 * unit(quotes["sp500"]["return_1d"], 2) +
              15 * unit(quotes["nasdaq"]["return_1d"], 2.5) -
              10 * unit(quotes["vix"]["return_1d"], 10))
    rows = []
    for stock in stocks:
        evidence = SEMIS.get(stock["code"])
        sector = 0.0
        components = ["S&P 500", "Nasdaq", "VIX（反向）"]
        if evidence:
            proxy = quotes["sox"]["return_1d"]
            if stock["code"] == "2330" and "tsm" in quotes and quotes["tsm"]["date"] == quotes["sox"]["date"]:
                proxy = (proxy + quotes["tsm"]["return_1d"]) / 2
                components.append("SOX＋TSM ADR")
            else:
                components.append("SOX")
            sector = 15 * unit(proxy, 3)
        tilt = max(-50.0, min(50.0, market + sector))
        adjustment = .1 * tilt
        rows.append({"code": stock["code"], "name": stock["name"], "original_score": stock["overall"],
                     "us_score": 50 + tilt, "us_adjustment": adjustment,
                     "weighted_score": .9 * stock["overall"] + .1 * (50 + tilt),
                     "semiconductor_evidence": ({"url": evidence[1], "text": evidence[2], "risk": "產業同向只是待驗證假設；公司基本面、客戶、產品與台股開盤跳空可造成不同走勢。"} if evidence else None),
                     "components": components})
    return sorted(rows, key=lambda r: (-r["weighted_score"], r["code"]))


def render(data):
    quote_rows = "".join(f"<tr><td>{html.escape(q['name'])}</td><td>{q['date']}</td><td>{q['close']:.2f}</td><td>{q['return_1d']:+.2f}%</td><td>{q['return_5d']:+.2f}%</td></tr>" for q in data["quotes"].values())
    cards = []
    for row in data["ranking"][:5]:
        ev = row["semiconductor_evidence"]
        evidence = f"<p>產業證據：<a href='{html.escape(ev['url'], quote=True)}'>{html.escape(ev['text'])}</a><br>限制：{html.escape(ev['risk'])}</p>" if ev else "<p>僅使用全市場隔夜訊號，沒有額外產業加權。</p>"
        cards.append(f"<section><h3>{row['code']} {html.escape(row['name'])}</h3><p>原分 {row['original_score']:.2f} · 美股淨影響 {row['us_adjustment']:+.2f} · 加權分 {row['weighted_score']:.2f}</p><p>構成：{'、'.join(row['components'])}</p>{evidence}</section>")
    return f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>美股隔夜加權</title>
<style>body{{background:#f4f6f9;font-family:system-ui;color:#172033}}main{{max-width:950px;margin:auto;padding:20px}}section,table{{background:white;border-radius:14px;padding:16px;margin:12px 0}}td,th{{padding:8px;text-align:left}}p{{line-height:1.7}}a{{color:#2563eb}}</style><main>
<h1>美股隔夜加權 Top 5（實驗）</h1><p>評估時間：{data['as_of']} · 美股交易日：{data.get('us_market_date') or '未知'} · 狀態：{html.escape(data['source_status'])}</p>
<p>原台股綜合分 90%＋美股訊號 10%，最高只調整 ±5 分。使用已完成日線；這不是上漲機率，也不是開盤買進指令。</p>
<table><tr><th>指標／股票</th><th>日期</th><th>收盤</th><th>1 日</th><th>5 日</th></tr>{quote_rows}</table>
<h2>隔夜加權 Top 5</h2>{''.join(cards)}<p>免費行情為 Yahoo Finance 非官方介面，可能延遲或變更；四個必要指標缺一或日期不一致時，停用全部美股加分。NVDA、AMD、Broadcom、Micron 僅展示，不在缺乏公司供應關係證據時直接對應台股。</p>
<a href="us_market.json">完整資料</a> · <a href="index.html">回原版選股</a></main></html>'''


def run(docs, now=None):
    now = now or dt.datetime.now(TZ)
    source = load(docs / "results.json", {})
    quotes, errors, healthy = collect(now)
    required_dates = {quotes[k]["date"] for k in ("sp500", "nasdaq", "sox", "vix") if k in quotes}
    data = {"rules_version": VERSION, "as_of": now.isoformat(), "market_date": source.get("market_date"),
            "us_market_date": next(iter(required_dates)) if healthy else None,
            "source_status": "正常" if healthy else "缺漏／日期不一致：停用美股加分",
            "errors": errors, "quotes": quotes, "ranking": rank(source.get("stocks", []), quotes, healthy)}
    (docs / "us_market.json").write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (docs / "us_market.html").write_text(render(data), encoding="utf-8")
    print(f"美股隔夜版：{data['source_status']}；{sum(r['us_adjustment'] != 0 for r in data['ranking'])} 檔有調整")
    return data


if __name__ == "__main__":
    run(Path(__file__).resolve().parent / "docs")
