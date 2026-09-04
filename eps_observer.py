"""Read-only announcement evidence; never changes stock scores."""
import datetime as dt
import hashlib
import html
import json
import re
from pathlib import Path

from event_news import OFFICIAL, TZ, fetch


def extract(body):
    # Only accept an explicit single-month value on one short line.
    # Multi-period tables, units and attribution require human review.
    candidates = []
    for line in body.splitlines():
        match = re.fullmatch(r"\s*(?:單月|本月)(?:EPS|每股盈餘)\s*[:：為]?\s*(-?\d+(?:\.\d+)?)\s*元[。]?\s*", line, re.I)
        if match:
            candidates.append({"value": float(match[1]), "evidence": line})
    values = {r["value"] for r in candidates}
    return {"monthly_eps_candidate": candidates[0]["value"] if len(values) == 1 else None,
            "evidence_lines": [r["evidence"] for r in candidates],
            "review_status": "待核對期間、幣別與合併口徑；不加分",
            "period": None, "currency": None, "basis": None,
            "audit_status": "未知"}


def build(raw, codes, now):
    result = []
    for row in raw:
        code = str(row.get("公司代號", "")).strip()
        body = str(row.get("說明", ""))
        title = next((str(v) for k, v in row.items() if k.strip() == "主旨"), "")
        if code not in codes or not re.search(r"每股盈餘|EPS", title + body, re.I):
            continue
        identifier = hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]
        result.append({"id": identifier, "code": code, "name": str(row.get("公司名稱", "")),
                       "title": title, "published_date_raw": row.get("發言日期"),
                       "published_time_raw": row.get("發言時間"), "observed_at": now.isoformat(),
                       "source_url": OFFICIAL, "source_note": "證交所重大訊息資料集；以公司、發言日及主旨定位",
                       "body": body, "raw_announcement": row, "extraction": extract(body),
                       "is_correction": bool(re.search(r"更正|修正", title)),
                       "correction_of": None})
    return result


def render(data):
    cards = []
    for r in reversed(data["records"][-100:]):
        e = r["extraction"]
        value = "未知／需讀原文" if e["monthly_eps_candidate"] is None else str(e["monthly_eps_candidate"]) + "（文字候選值，尚未確認）"
        cards.append(f"<section><h2>{html.escape(r['code'])} {html.escape(r['name'])}</h2><p>{html.escape(r['title'])}</p>"
                     f"<p>發言日期（民國）：{html.escape(str(r['published_date_raw']))} · 時間：{html.escape(str(r['published_time_raw']))}</p>"
                     f"<p>單月 EPS：{value}。{e['review_status']}</p><p>更正公告：{'是；原公告關聯待核對' if r['is_correction'] else '未由主旨辨識'}</p>"
                     f"<details><summary>公告原文</summary><pre>{html.escape(r['body'])}</pre></details>"
                     f"<a href='{OFFICIAL}'>證交所來源資料集</a></section>")
    return f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>公告與單月 EPS 觀察表</title><style>body{{font-family:system-ui;background:#f4f6f9;color:#172033}}main{{max-width:950px;margin:auto;padding:20px}}section{{background:white;padding:18px;margin:14px 0;border-radius:14px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}p{{line-height:1.7}}</style><main>
<h1>公告原文＋單月 EPS 觀察表</h1><p>來源狀態：{html.escape(data['source_status'])} · 擷取：{data['as_of']}</p>
<p>僅觀察，不加分、不年化、不計算預估本益比。數字缺漏及多期間表格均保留原文待核對。來源為目前候選股票的公司重大訊息，不代表全部公司每月 EPS。</p>
<p>累積 {len(data['records'])} 則；頁面顯示最近最多 100 則，全部紀錄見 <a href="eps_observations.json">JSON</a>。更正不覆寫舊公告。</p>
{''.join(cards) or '<p>目前沒有符合条件的公告；不代表沒有相關資料。</p>'}<a href="index.html">返回每日選股</a></main></html>'''


def run(docs, fetcher=fetch, now=None):
    now = now or dt.datetime.now(TZ)
    path = docs / "eps_observations.json"
    previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"records": []}
    stocks = json.loads((docs / "results.json").read_text(encoding="utf-8"))["stocks"]
    records = {r["id"]: r for r in previous["records"]}
    try:
        raw = json.loads(fetcher(OFFICIAL))
        if not isinstance(raw, list) or not raw:
            raise ValueError("empty source")
        for r in build(raw, {s["code"] for s in stocks}, now):
            records.setdefault(r["id"], r)
        status = "正常"
    except Exception as error:
        status = "來源失敗，保留舊資料：" + type(error).__name__
    payload = {"as_of": now.isoformat(), "source_status": status, "records": list(records.values())}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (docs / "eps_observer.html").write_text(render(payload), encoding="utf-8")
    print(f"EPS 觀察表：{status}；{len(records)} 則")
    return payload


if __name__ == "__main__":
    run(Path(__file__).resolve().parent / "docs")
