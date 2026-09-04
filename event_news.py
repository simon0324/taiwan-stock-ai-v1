"""Auditable, rule-based event overlay. No LLM, paid API or trading access."""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

TZ = dt.timezone(dt.timedelta(hours=8))
ROOT = Path(__file__).resolve().parent
OFFICIAL = "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"
FEEDS = {"中央通訊社・產經": "https://feeds.feedburner.com/rsscna/finance",
         "中央通訊社・科技": "https://feeds.feedburner.com/rsscna/technology"}
CATEGORIES = ("訂單", "新聞話題", "龍頭地位", "專利技術")
RULES = {
    "訂單": (r"取得.{0,12}訂單|接獲.{0,12}訂單|簽訂.{0,12}(?:供貨|採購|銷售)合約|得標", r"取消.{0,8}訂單|訂單.{0,8}取消|終止.{0,8}(?:供貨|採購|銷售)合約"),
    "新聞話題": (r"(?:新產品|新廠|新產線).{0,12}(?:正式量產|開始量產|正式投產)|(?:產品|新藥).{0,12}(?:獲准上市|取得上市許可)", r"(?:工廠|產線).{0,12}(?:停工|停產)|產品.{0,8}召回"),
    "龍頭地位": (r"市占率.{0,20}(?:全球第一|全球第1|排名第一|位居第一)|(?:全球第一|全球第1).{0,15}市占率", r"市占率.{0,12}(?:下滑|下降|衰退)"),
    "專利技術": (r"(?:取得|獲得|獲准).{0,12}(?:發明專利|專利授權)|專利.{0,8}(?:核准|獲准)|通過.{0,12}客戶認證|技術授權合約", r"專利.{0,12}(?:無效|撤銷|敗訴)|(?:技術|產品).{0,12}認證失敗"),
}


def load(path, default):
    # Corrupt existing audit data must not silently disappear.
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Taiwan-stock-research/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read(4_000_000)


def classification(title):
    text = re.sub(r"\s+", "", title)
    if re.search(r"傳聞|市場傳出|傳出|傳言|有望|可望|預計|擬|尚未|未獲|未取得|否認|澄清|不實|並無|並未", text):
        return "待查證", 0, "含推測、否認或澄清語句，僅列入觀察"
    for category, (_, negative) in RULES.items():
        if re.search(negative, text):
            return category, -1, "標題命中負面事件規則；非全文語意查證"
    for category, (positive, _) in RULES.items():
        if re.search(positive, text):
            return category, 1, "標題命中具體事件規則；非獨立事實查證"
    return "待查證", 0, "未達具體事件門檻；熱門話題、龍頭稱號及專利申請本身不加分"


def event(code, title, published, source, url, official, now):
    category, direction, reason = classification(title)
    normalized = re.sub(r"[\W_]", "", title).lower()
    identifier = hashlib.sha256(f"{code}:{normalized}".encode()).hexdigest()[:24]
    return {"id": identifier, "code": code, "title": title[:240], "published_at": published.isoformat(),
            "first_seen_at": now.isoformat(), "source": source, "url": url, "official": official,
            "category": category, "direction": direction, "reason": reason}


def collect(stocks, now, fetcher=fetch):
    events, health = [], []
    codes = {r["code"] for r in stocks}
    try:
        raw = json.loads(fetcher(OFFICIAL))
        if not isinstance(raw, list) or not raw:
            raise ValueError("官方資料為空或格式異常")
        invalid = 0
        for row in raw:
            if str(row.get("公司代號")) not in codes:
                continue
            try:
                date = re.sub(r"\D", "", row["發言日期"])
                clock = str(row["發言時間"]).zfill(6)
                published = dt.datetime(int(date[:3]) + 1911, int(date[3:5]), int(date[5:]),
                                        int(clock[:2]), int(clock[2:4]), int(clock[4:]), tzinfo=TZ)
                title = next(v for k, v in row.items() if k.strip() == "主旨")
                events.append(event(str(row["公司代號"]), title, published, "證交所・公司重大訊息", OFFICIAL, True, now))
            except (KeyError, ValueError, StopIteration):
                invalid += 1
        health.append({"source": "證交所・公司重大訊息", "status": "partial" if invalid else "ok", "invalid_rows": invalid})
    except Exception as error:
        health.append({"source": "證交所・公司重大訊息", "status": "failed", "error": type(error).__name__})
    for source, url in FEEDS.items():
        try:
            root = ET.fromstring(fetcher(url))
            items = root.findall("./channel/item")
            if not items:
                raise ValueError("RSS 無項目")
            invalid = 0
            for item in items:
                title, link = item.findtext("title", ""), item.findtext("link", "")
                if not link.startswith("https://www.cna.com.tw/"):
                    continue
                try:
                    published = parsedate_to_datetime(item.findtext("pubDate", "")).astimezone(TZ)
                except (ValueError, TypeError):
                    invalid += 1
                    continue
                # Conservative title matching: ambiguous multi-company stories are review-only.
                matches = [r for r in stocks if (len(r["name"]) >= 3 and r["name"] in title)
                           or (len(r["name"]) == 2 and r["name"] not in {"全新", "中華", "統一", "世界", "國產"}
                               and title.startswith(r["name"]))
                           or re.search(r"[（(]" + re.escape(r["code"]) + r"[)）]", title)]
                if len(matches) == 1:
                    events.append(event(matches[0]["code"], title, published, source, link, False, now))
            health.append({"source": source, "status": "partial" if invalid else "ok", "invalid_rows": invalid})
        except Exception as error:
            health.append({"source": source, "status": "failed", "error": type(error).__name__})
    return events, health


def merge_events(old, new, now):
    merged = {r["id"]: r for r in old}
    for item in new:
        if item["id"] not in merged:
            merged[item["id"]] = item
        elif item["official"] and not merged[item["id"]]["official"]:
            merged[item["id"]] = {**item, "first_seen_at": merged[item["id"]]["first_seen_at"]}
    return sorted((r for r in merged.values() if 0 <= (now - dt.datetime.fromisoformat(r["published_at"])).total_seconds() <= 14 * 86400),
                  key=lambda r: (r["published_at"], r["id"]), reverse=True)


def score(stock, events, now):
    relevant = [r for r in events if r["code"] == stock["code"]]
    contributions = []
    for category in CATEGORIES:
        positives, negatives = [], []
        for item in relevant:
            if item["category"] != category:
                continue
            age = (now - dt.datetime.fromisoformat(item["published_at"])).total_seconds() / 86400
            if not 0 <= age <= 14:
                continue
            weight = (1 if item["official"] else .6) * 2 ** (-age / 7)
            (positives if item["direction"] > 0 else negatives).append(weight)
        # A category cap also suppresses differently worded syndications; conservative by design.
        contributions.append({"category": category, "points": 12.5 * (max(positives, default=0) - max(negatives, default=0))})
    delta = sum(r["points"] for r in contributions)
    news_score = max(0, min(100, 50 + delta))
    return {"code": stock["code"], "name": stock["name"], "original_score": stock["overall"],
            "message_score": news_score, "score": .9 * stock["overall"] + .1 * news_score,
            "evidence_status": "有規則訊號" if any(r["direction"] for r in relevant) else "未知／未達門檻",
            "contributions": contributions, "events": relevant}


def render(payload):
    health = "、".join(f"{html.escape(r['source'])}：{r['status']}" for r in payload["sources"])
    cards = []
    original = "、".join(html.escape(r["code"] + " " + r["name"]) for r in payload["original_top5"])
    for r in payload["ranking"][:5]:
        links = "".join(f"<li>{html.escape(e['category'])}：<a href='{html.escape(e['url'], quote=True)}'>{html.escape(e['title'])}</a>"
                        f" — {html.escape(e['source'])}，{html.escape(e['published_at'])}<br>{html.escape(e['reason'])}</li>" for e in r["events"][:8])
        points = "／".join(f"{x['category']} {x['points']:+.2f}" for x in r["contributions"])
        cards.append(f"<section><h2>{html.escape(r['code'])} {html.escape(r['name'])} · {r['score']:.2f} 分</h2>"
                     f"<p>原版 {r['original_score']:.2f} · 消息 {r['message_score']:.2f} · {r['evidence_status']}</p><p>{points}</p><ul>{links or '<li>沒有符合門檻的消息；不代表沒有相關事件。</li>'}</ul></section>")
    return f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>消息加分版 Top 5</title><style>body{{background:#f4f6f9;color:#172033;font-family:system-ui;margin:0}}main{{max-width:900px;margin:auto;padding:24px 16px}}section{{background:white;border-radius:16px;padding:20px;margin:16px 0}}p,li{{line-height:1.8}}a{{color:#2563eb;overflow-wrap:anywhere}}h2{{font-size:21px}}</style><main>
<h1>消息加分版 Top 5（實驗）</h1><p>行情：{payload['market_date']} · 消息擷取：{payload['as_of']}</p><p>{health}</p><p>原版綜合 Top 5（固定取 5 檔作比較）：{original}</p>
<section><p>原版綜合分 90%＋事件分 10%。事件分中性為 50；每類正／負最多 12.5 分，7 日半衰、14 日到期。官方權重 1、媒體 0.6；同類取最強一筆，不因重複轉載疊加。</p>
<p>這是標題規則篩選，不是 AI 全文查證、專利法律效力或龍頭地位認證。查不到資料為未知；來源失敗時仍可能不完整。公司公開資料目前限重大訊息，沒有全面爬取公司官網或專利資料庫。</p>
<p>原版本身已含重大訊息頻次分；這裡的 10% 是新增事件層，不代表所有新聞影響只有 10%。僅觀察，不自動下單、不覆寫原版排名。</p></section>{''.join(cards)}
<section><p>每日快照只在行情日期等於擷取日期時新增，保存首次擷取時間；同日重跑不改快照。兩版各 Top 5 獨立保存，目前不宣稱已有績效驗證。</p>
<a href="news_signals.json">每日對照快照</a> · <a href="news_results.json">所有評分與證據</a> · <a href="index.html">每日選股</a></section></main></html>'''


def run(docs, now=None, fetcher=fetch):
    now = now or dt.datetime.now(TZ)
    source = load(docs / "results.json", {})
    stocks = source["stocks"]
    new, health = collect(stocks, now, fetcher)
    events = merge_events(load(docs / "news_events.json", []), new, now)
    ranking = sorted((score(r, events, now) for r in stocks), key=lambda r: (-r["score"], r["code"]))
    payload = {"rules_version": "events-1", "market_date": source["market_date"], "as_of": now.isoformat(),
               "sources": health, "ranking": ranking,
               "original_top5": sorted(stocks, key=lambda r: (-r["overall"], r["code"]))[:5]}
    snapshots = load(docs / "news_signals.json", [])
    day = source["market_date"]
    if day == now.date().isoformat() and not any(s["market_date"] == day for s in snapshots):
        snapshots.append({"market_date": day, "observed_at": now.isoformat(), "rules_version": "events-1", "sources": health,
                          "original": sorted(stocks, key=lambda r: (-r["overall"], r["code"]))[:5],
                          "message": ranking[:5]})
    for filename, value in (("news_events.json", events), ("news_results.json", payload), ("news_signals.json", snapshots)):
        (docs / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (docs / "news.html").write_text(render(payload), encoding="utf-8")
    print(f"消息版完成：{len(events)} 則來源紀錄；{len(snapshots)} 天快照；來源狀態 {health}")
    return payload


if __name__ == "__main__":
    run(ROOT / "docs")
