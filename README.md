# 台股 AI 選股系統 V2

僅篩選臺灣上市股票，產生穩健、成長、強勢及綜合四組 Top 5。主要評估週期為 3～5 個交易日。

V2 增加進場參考區間、固定與波動停損／目標價、個股風險提示、大盤弱勢降為 Top 3，以及 5 個交易日推薦績效追蹤。

V2B 可由 GitHub Actions 手動執行三年技術面子策略回測，設定 50 萬元本金、Top 5 平均配置，並扣除標準手續費與交易稅。FinMind Token 只透過 GitHub Secret `FINMIND_TOKEN` 傳入。

V2C 修正為依當期可用資金配置，加入市場廣度濾網。最後約三分之一資料已反覆檢視，只能視為後段描述期間，不能視為獨立驗證。

## EPS 同條件比較

在 Actions 選「EPS 同條件比較」，以 main 執行新的 Run workflow。此工作只還原既有歷史快取，不使用 Token、不重新下載；快取缺漏或共同交易日不足會停止。

共用行情、股票池、日期和成本，並列成長策略 3／4／5 日的營收基準與「基準 80%＋EPS 20%」。基準比例為均線 30%、動能 30%、量能 15%、營收 25%，與舊 EPS 報告不是相同權重。兩組各自从 50 萬元開始，結果不會套用到每日選股。

完成後查看 [比較報告](https://simon0324.github.io/taiwan-stock-ai-v1/comparison.html)。JSON 附股票池、快取末日與輸入指紋。歷史行情沒有下載至本機時，可先執行 `python -m unittest discover -s tests -v` 測試合成資料；完整比較以 `python compare_backtest.py` 執行。

限制：當前股票池有存活者偏差；未還原歷史處置／全額交割狀態；基本面可用日期為估計值，未核對實際公告及修訂。回撤僅批次結算，不是每日市值回撤，其他成交假設請見比較報告。

## 本機更新

```powershell
python app.py
python -m http.server 8000 --directory docs
```

瀏覽 `http://localhost:8000`。程式只使用 Python 內建套件。

## 公開網站

將專案上傳 GitHub 後，在倉庫的 Settings → Pages 中，選擇 `Deploy from a branch`、`main` 分支及 `/docs` 資料夾。GitHub Actions 會在週一至週五台灣時間 14:50 自動更新。

## 評分與限制

- 排除近 20 個交易日平均成交金額低於新臺幣 5,000 萬元的股票。
- 排除證交所處置及變更交易股票。
- 依營收年增率、EPS、外資買賣超、成交量、均線與重大訊息計分。
- 免費資料可能延遲；頁面會標示實際行情日期。
- 本系統僅供研究，不構成投資建議。
