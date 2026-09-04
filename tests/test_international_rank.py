import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
import international_rank as r

NOW = dt.datetime(2026,9,5,15,tzinfo=r.TZ)


def event(title, days=0):
    return {'id':title, 'title':title, 'url':'https://www.cna.com.tw/test',
            'published_at':(NOW-dt.timedelta(days=days)).isoformat()}


class RankTests(unittest.TestCase):
    def test_direction_rejects_forecasts_and_conflicts(self):
        self.assertEqual(r.direction('油價下跌3%'), ('fuel',-1))
        self.assertEqual(r.direction('SCFI上漲5%'), ('container',1))
        for title in ('油價可能上漲','油價先漲後跌','油價下跌後上漲','關稅提高','聯準會降息','油價止跌'):
            self.assertIsNone(r.direction(title))

    def test_evidence_required_and_reposts_do_not_stack(self):
        stocks=[{'code':'2610','name':'華航','overall':70},{'code':'9999','name':'油價公司','overall':70}]
        one = r.rank(stocks,[event('油價下跌3%')],NOW)
        self.assertGreater(one[0]['event_adjustment'],0)
        self.assertEqual(one[1]['event_adjustment'],0)
        self.assertEqual(one[0]['weighted_score'],r.rank(stocks,[event('油價下跌3%'),event('油價大跌')],NOW)[0]['weighted_score'])
        self.assertTrue(all(x['event_adjustment']==0 for x in r.rank(stocks,[event('油價下跌',8)],NOW)))
        self.assertLess(r.rank(stocks[:1],[event('油價上漲')],NOW)[0]['event_adjustment'],0)

    def test_opposing_evidence_offsets(self):
        stock=[{'code':'2609','name':'陽明','overall':70}]
        out=r.rank(stock,[event('SCFI上漲'),event('貨櫃運價下跌')],NOW)
        self.assertEqual(out[0]['event_adjustment'],0)

    def test_tracking_entry_after_observation_and_missing_not_shifted(self):
        history=[[{'code':'2610','date':f'2026-09-{day:02}','close':100+day}] for day in range(5,12)]
        snapshots=[{'observed_at':NOW.isoformat(),'original':[{'code':'2610'}],'weighted':[{'code':'2610'}]}]
        r.track(snapshots,history)
        outcome=snapshots[0]['weighted'][0]['outcomes']['3']
        self.assertEqual(outcome['entry_date'],'2026-09-06')
        self.assertEqual(outcome['exit_date'],'2026-09-09')
        saved=json.dumps(snapshots)
        r.track(snapshots,history)
        self.assertEqual(saved,json.dumps(snapshots))
        sessions=[d[:] for d in history[1:]]
        sessions[2]=[{'code':'OTHER','date':'2026-09-08','close':100}]
        self.assertEqual(r.returns_for({'code':'2610'},sessions)['3']['status'],'missing_price')

    def test_snapshot_immutable_and_failure_suppresses_new_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'results.json').write_text(json.dumps({'market_date':'2026-09-05','stocks':[{'code':'2610','name':'華航','overall':70}]}),encoding='utf-8')
            radar={'as_of':NOW.isoformat(),'sources':[{'status':'ok'}],'events':[event('油價下跌')]}
            (p/'global_events.json').write_text(json.dumps(radar),encoding='utf-8')
            r.run(p,NOW)
            initial=json.loads((p/'international_signals.json').read_text())
            r.run(p,NOW+dt.timedelta(hours=1))
            after=json.loads((p/'international_signals.json').read_text())
            self.assertEqual(initial[0]['weighted'],after[0]['weighted'])
            radar['sources'][0]['status']='failed'
            (p/'global_events.json').write_text(json.dumps(radar),encoding='utf-8')
            self.assertEqual(r.run(p,NOW)['ranking'][0]['event_adjustment'],0)
