"""Free headline-rule radar: research hypotheses, never a trading score."""
import datetime as dt
import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

from event_news import TZ, fetch

FEEDS = {
    "中央通訊社・國際": "https://feeds.feedburner.com/rsscna/intworld",
    "中央通訊社・產經": "https://feeds.feedburner.com/rsscna/finance",
}
# Conditional research channels, not empirical return forecasts.
RULES = [
    ("貨櫃運價", r"SCFI|貨櫃運價|貨櫃運費", "貨櫃航運", "若實際適用運價提高可能有利收入。", "貨量、成本與長約可能抵銷效果。", "核對公司航線與合約口徑。"),
    ("出口管制", r"出口管制|晶片禁令|限制.{0,6}晶片出口",
     "半導體、設備與電子供應鏈", "若替代採購發生，合規替代供應商可能受益。", "受限客戶或產品可能減少需求。", "核對管制清單、生效日、公司產品與客戶曝險。"),
    ("關稅／貿易", r"關稅|貿易戰|貿易協議",
     "出口製造、電子、機械", "若關稅下降或訂單轉移，部分出口商可能受益。", "若成本提高且無法轉嫁，利潤可能承壓。", "核對原產地、稅率、生效日與企業產地；不能將降稅與加稅視為同方向。"),
    ("利率政策", r"(?:聯準會|Fed|美國|歐洲央行|日本央行).{0,20}(?:升息|降息|利率)|(?:升息|降息).{0,15}(?:聯準會|美國|日本央行)",
     "金融、負債敏感與出口產業", "若融資成本下降，負債敏感企業可能改善。", "若利差收窄或匯率不利，部分企業可能承壓。", "核對實際決議與預期、幣別及企業負債結構。"),
    ("能源供應", r"油價|原油|天然氣|石油|OPEC",
     "能源、石化、航空與運輸", "若能源售價上升且成本可控，部分上游企業可能受益。", "若燃料成本提高，未避險的用能企業可能承壓。", "核對價格方向、持續性、庫存、避險與成本轉嫁，油價下跌時可能相反。"),
    ("衝突／航運", r"戰爭|空襲|軍事衝突|航道.{0,8}封鎖|荷莫茲|紅海",
     "航運、物流、能源與出口產業", "若供給受限推升運價，部分運輸商可能受益。", "繞航、保險、交期與燃料成本也可能增加。", "核對事件是否已發生、航線曝險與實際成本，不把衝突直接當買進訊號。"),
]


def analyze(title):
    uncertain = bool(re.search(r"傳聞|傳出|可能|預計|可望|擬|否認|澄清|考慮", title))
    channels = [{"category": name, "industry": industry, "possible_benefit": benefit,
                 "possible_risk": risk, "verify_next": check}
                for name, pattern, industry, benefit, risk, check in RULES if re.search(pattern, title, re.I)]
    return channels, "推測／待確認" if uncertain else "媒體標題報導；未獨立查證"


def collect(now, fetcher=fetch):
    events, health = [], []
    for source, url in FEEDS.items():
        try:
            root = ET.fromstring(fetcher(url))
            items = root.findall("./channel/item")
            if not items:
                raise ValueError("empty feed")
            invalid = 0
            for item in items:
                title, link = item.findtext("title", ""), item.findtext("link", "")
                if not link.startswith("https://www.cna.com.tw/"):
                    continue
                try:
                    published = parsedate_to_datetime(item.findtext("pubDate", ""))
                    if published.tzinfo is None:
                        raise ValueError("timezone missing")
                    published = published.astimezone(TZ)
                except (TypeError, ValueError):
                    invalid += 1
                    continue
                if not 0 <= (now - published).total_seconds() <= 7 * 86400:
                    continue
                channels, certainty = analyze(title)
                if not channels:
                    continue
                identifier = hashlib.sha256(re.sub(r"\W", "", title).encode()).hexdigest()[:24]
                events.append({"id": identifier, "title": title, "url": link, "source": source,
                               "published_at": published.isoformat(), "first_seen_at": now.isoformat(),
                               "certainty": certainty, "channels": channels,
                               "beneficiary_stocks": [], "adversely_affected_stocks": [],
                               "company_evidence_status": "未取得可核對的公司營運關聯證據，不自動推定個股受益／受損"})
            health.append({"source": source, "status": "partial" if invalid else "ok", "invalid_dates": invalid})
        except Exception as error:
            health.append({"source": source, "status": "failed", "error": type(error).__name__})
    return events, health


def render(payload):
    blocks = []
    for event in payload["events"]:
        channels = "".join(f"<li><b>{html.escape(c['category'])}：{html.escape(c['industry'])}</b><br>可能受益：{html.escape(c['possible_benefit'])}"
                           f"<br>可能受損：{html.escape(c['possible_risk'])}<br>查證：{html.escape(c['verify_next'])}</li>" for c in event["channels"])
        blocks.append(f"<section><h2><a href='{html.escape(event['url'], quote=True)}'>{html.escape(event['title'])}</a></h2>"
                      f"<p>{html.escape(event['source'])} · {html.escape(event['published_at'])} · {event['certainty']}</p><ul>{channels}</ul>"
                      f"<p>{event['company_evidence_status']}</p></section>")
    health = "；".join(f"{r['source']}：{r['status']}" for r in payload["sources"])
    return f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>國際重大事件雷達</title><style>body{{font-family:system-ui;background:#f4f6f9;color:#172033}}main{{max-width:960px;margin:auto;padding:20px}}section{{background:white;border-radius:16px;padding:20px;margin:16px 0}}h2{{font-size:20px}}p,li{{line-height:1.8}}a{{color:#2563eb}}</style><main>
<h1>國際重大事件雷達（免費規則版）</h1><p>擷取：{payload['as_of']} · {health}</p>
<p>最近 7 日蒐集到的新聞，依時間排序，不是重要性或買進排名。同標題去重；不同標題可能仍為同一事件，不累加分數。</p>
<p>僅用中央社 RSS 標題，不是全文 AI 分析、全球新聞全覆蓋或事實查核。以下產業影響為固定條件式研究提示，不代表本次事件一定如此。需開來源核對政策方向、原始公告及公司關聯。不更動任何選股分數，不下單。</p>
{''.join(blocks) or '<section>暫無符合規則的可用新聞；請同時檢查來源狀態。</section>'}
<p>來源失敗時保留仍在 7 日內的舊資料，以上發布日期不可當作最新消息。</p><a href="global_events.json">下載資料</a> · <a href="index.html">每日選股</a></main></html>'''


def run(docs, now=None, fetcher=fetch):
    now = now or dt.datetime.now(TZ)
    path = docs / "global_events.json"
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"events": []}
    incoming, health = collect(now, fetcher)
    merged = {e["id"]: e for e in old["events"]}
    for e in incoming:
        merged.setdefault(e["id"], e)
    events = sorted((e for e in merged.values() if 0 <= (now - dt.datetime.fromisoformat(e['published_at'])).total_seconds() <= 7 * 86400),
                    key=lambda e: (e["published_at"], e["id"]), reverse=True)
    payload = {"rules_version": "global-rules-1", "as_of": now.isoformat(), "sources": health, "events": events}
    docs.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (docs / "global_events.html").write_text(render(payload), encoding="utf-8")
    print(f"國際雷達：{len(events)} 則，來源 {health}")
    return payload


if __name__ == "__main__":
    run(Path(__file__).resolve().parent / "docs")
