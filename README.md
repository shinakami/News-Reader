# News Reader

一個純 Python 標準函式庫專案，可在終端機中：

- 抓取即時網路新聞並分行列出
- 監控台股四大指數
- 顯示四大指數終端走勢圖
- 顯示市值前五大 ETF 即時股價資訊
- 用目前抓到的行情快照產生 HTML Dashboard
- 啟動股票動態視窗介面，查看價格、K 線與移動平均線

## 專案結構

```text
News Reader/
├─ main.py
├─ requirements.txt
├─ requirement.txt
├─ .gitignore
├─ README.md
├─ news_reader.py
├─ stock_dynamic.py
├─ stock_index_monitor.py
├─ run_news_reader.bat
├─ run_dashboard.bat
├─ run_stock_dynamic.bat
├─ run_stock_index_monitor.bat
└─ src/
   └─ news_reader/
      ├─ __init__.py
      ├─ stock_market_dashboard.py
      ├─ news.py
      ├─ stock_dynamic.py
      └─ stock_monitor.py
```

## 安裝

此專案目前不需要第三方套件。若仍想用標準流程安裝需求檔：

```powershell
pip install -r requirements.txt
```

## 統一入口

抓即時新聞：

```powershell
python .\main.py news
```

抓指定新聞關鍵字：

```powershell
python .\main.py news AI 財經 -n 10 --links
```

監控台股四大指數與 ETF：

```powershell
python .\main.py stocks
```

產生 HTML Dashboard：

```powershell
python .\main.py dashboard
```

啟動股票動態視窗：

```powershell
python .\main.py dynamic
```

股票動態主視窗及其詳細視窗固定使用深夜黑色主題，不提供白天／黑夜切換功能。
右上角的「切換美股」按鈕會在原視窗切換到美股頁，不另外開啟視窗；按下「返回台股」即可回到原本的台股 Dashboard。美股頁只顯示 S&P 500、道瓊工業、NASDAQ、費城半導體四張 K 線圖；美股開盤期間自動顯示 5 分 K，休市時顯示日 K。
右上角的「台指期貨」按鈕會另開一個視窗，以 2x2 版面顯示台指近期、小型台指近期、電子近期、金融近期四張近月 5 分 K 圖。資料來源改為 Yahoo 股市台灣期貨行情，四個商品透過同一批請求同步更新，預設更新間隔與主 Dashboard 相同（10 秒）；也可按「同步更新四圖」立即更新，主 Dashboard 的「立即更新」亦會同步更新已開啟的期貨視窗。沒有盤中成交的商品會保留既有圖形並標示暫無成交，不會以昨收偽造 K 棒。
美股頁右側包含目前美國交易所及 OTC 市場的台灣企業 ADR／ADS 清單，以及常用美國個股。個股區可輸入 Yahoo 支援的美股代號並即時加入，例如 `PLTR` 或 `BRK-B`；ADR、美股個股與四大指數最新報價會透過 Yahoo 股市批次行情同步取得，預設每 10 秒更新。個股若暫時沒有行情，仍會保留代號並標示無資料；指數歷史 K 線仍由 Yahoo Finance 圖表服務提供。
台股頁右側新增「台股個股」區塊，預載台積電、鴻海、聯發科等常用標的；可輸入上市或上櫃股票代號加入清單，例如 `2330` 或 `6488`。台股個股改用 Yahoo 股市台灣批次行情，上市與上櫃標的會依回傳的交易所資訊辨識，整批行情預設每 10 秒同步更新。清單顯示現價、漲跌、漲跌幅、成交量、行情時間與來源，雙擊個股可開啟使用相同 K 棒與均線版型的詳細視窗。
主視窗、ETF 與台股個股詳細視窗會響應視窗縮放：指數卡片會在 4 欄、2 欄、1 欄之間切換，圖表與 ETF／個股表格也會在寬視窗左右排列、窄視窗上下排列。
四大指數 K 線圖以 2x2 區塊呈現，每個指數使用獨立座標範圍，並採用類似 Yahoo 股市的圖表樣式：真實 OHLC 紅綠 K 棒、MA5／MA10／MA20、昨收基準線、最高價標記、右側價格軸與時間標籤。工作日 09:00–13:35 會自動顯示 5 分 K，收盤後自動切換為日 K；程式持續開啟並跨越切換時間時，會在下一次刷新重新載入適合的週期。若盤中歷史分 K 暫時無法取得，會標示「5 分 K（即時建立）」並從即時報價開始累積。圖表區、ETF 表格區，以及四個指數圖表區塊之間都有可拖曳分隔線，可手動調整大小。
ETF 區塊會載入目前 TWSE OpenAPI 與 e添富可取得的台灣上市 ETF 清單，依資產規模由大到小排列，並提供滾動條瀏覽。雙擊任一 ETF 列，可開啟該 ETF 的詳細視窗；ETF 圖表與四大指數使用相同的 K 棒、MA5／MA10／MA20、昨收線、價格軸、時間標籤及自動週期切換。

## 全部參數

### main.py

```powershell
python .\main.py [command] [args]
```

| 參數 | 預設 | 說明 |
|---|---:|---|
| `command` | `news` | 要執行的功能。可用 `news`、`stocks`、`dashboard` 或 `dynamic`。 |
| `args` | 無 | 傳給 `news` 或 `stocks` 的後續參數。 |

### news

```powershell
python .\main.py news [queries...] [options]
```

| 參數 | 預設 | 說明 |
|---|---:|---|
| `queries` | `台灣 國際 科技 商業` | 新聞搜尋關鍵字，可輸入多個。 |
| `-n`, `--limit` | `5` | 每個關鍵字列出幾則新聞。 |
| `--language` | `zh-TW` | Google News 語言參數。 |
| `--region` | `TW` | Google News 地區參數。 |
| `--timeout` | `15` | 網路請求逾時秒數。 |
| `--links` | 關閉 | 同時列出新聞連結。 |
| `--dashboard` | 關閉 | 將本次抓到的新聞產生為 HTML Dashboard。 |
| `--open` | 關閉 | 產生 Dashboard 後用預設瀏覽器開啟；使用此參數時不必另外指定 `--dashboard`。 |
| `-o`, `--output` | `news_dashboard.html` | 新聞 Dashboard 的輸出路徑。 |

範例：

```powershell
python .\main.py news AI 財經 -n 10 --links
```

產生新聞 Dashboard，並用預設瀏覽器開啟：

```powershell
python .\main.py news --dashboard --open
```

Dashboard 支援分類篩選與文字搜尋；點擊新聞圖片、標題或「閱讀新聞」會在新分頁開啟原始新聞連結。

### stocks

```powershell
python .\main.py stocks [options]
```

| 參數 | 預設 | 說明 |
|---|---:|---|
| `-i`, `--interval` | `10` | 每幾秒更新一次指數與 ETF 資料。 |
| `--timeout` | `15` | 網路請求逾時秒數。 |
| `--retries` | `2` | 連線失敗時自動重試幾次，可降低 TWSE 短暫斷線造成的錯誤。 |
| `--source` | `auto` | 資料來源，可用 `auto`、`twse`、`yahoo`。`auto` 會先用 TWSE，失敗時切 Yahoo。 |
| `--no-clear` | 關閉 | 不使用原地更新，保留每次更新紀錄。 |
| `--once` | 關閉 | 只抓取一次快照後結束。 |
| `--history` | `60` | 每個指數圖表保留幾個歷史資料點。 |
| `--chart-width` | `60` | 圖表寬度；2x2 排版時單張圖會自動限制在適合並排的寬度。 |
| `--chart-height` | `8` | 圖表高度。 |
| `--no-chart` | 關閉 | 只顯示表格，不顯示四大指數走勢圖。 |
| `--no-etf` | 關閉 | 不顯示市值前五大 ETF 股價資訊。 |
| `--verify-ssl` | 關閉 | 強制驗證 TWSE SSL 憑證；若本機憑證鏈不完整，可能導致抓取失敗。 |

監控模式預設使用原地更新，減少終端閃屏。若想保留每次更新紀錄，可加上 `--no-clear`。

若出現 `Remote end closed connection without response`，代表 TWSE 伺服器暫時中斷連線。可提高更新間隔或重試次數：

```powershell
python .\main.py stocks -i 15 --retries 4
```

也可直接使用 Yahoo 備援資料源：

```powershell
python .\main.py stocks --source yahoo
```

`auto` 模式會優先使用 TWSE；若 TWSE 連線失敗，會自動切換到 Yahoo，畫面會顯示目前資料來源。

### dashboard

```powershell
python .\main.py dashboard [options]
```

| 參數 | 預設 | 說明 |
|---|---:|---|
| `-o`, `--output` | `stock_market_dashboard.html` | 輸出的 HTML Dashboard 檔案路徑。 |
| `--source` | `auto` | 資料來源，可用 `auto`、`twse`、`yahoo`。 |
| `--timeout` | `15` | 網路請求逾時秒數。 |
| `--retries` | `2` | 連線失敗時自動重試幾次。 |
| `--no-etf` | 關閉 | 不顯示 ETF 區塊。 |
| `--verify-ssl` | 關閉 | 強制驗證 TWSE SSL 憑證。 |
| `--open` | 關閉 | 產生 Dashboard 後用預設瀏覽器開啟。 |

範例：

```powershell
python .\main.py dashboard --source auto --open
```

指定輸出檔案：

```powershell
python .\main.py dashboard -o .\stock_market_dashboard.html
```

### dynamic

```powershell
python .\main.py dynamic [options]
```

| 參數 | 預設 | 說明 |
|---|---:|---|
| `-i`, `--interval` | `10` | 視窗資料更新間隔秒數。 |
| `--source` | `auto` | 資料來源，可用 `auto`、`twse`、`yahoo`。 |
| `--timeout` | `15` | 網路請求逾時秒數。 |
| `--retries` | `2` | 連線失敗時自動重試幾次。 |
| `--history` | `80` | K 線圖最多保留幾根 K 棒。 |
| `--no-etf` | 關閉 | 不顯示 ETF 清單。 |
| `--verify-ssl` | 關閉 | 強制驗證 TWSE SSL 憑證。 |

範例：

```powershell
python .\main.py dynamic -i 15 --source auto --history 120
```

## 股票監控常用參數

```powershell
python .\main.py stocks -i 5
```

每 5 秒更新一次。

```powershell
python .\main.py stocks --history 120 --chart-width 80 --chart-height 10
```

保留 120 筆歷史點，放大終端 2x2 走勢圖。

```powershell
python .\main.py stocks --no-chart
```

只顯示表格，不顯示四大指數走勢圖。

```powershell
python .\main.py stocks --no-etf
```

不顯示市值前五大 ETF 股價資訊。

```powershell
python .\main.py stocks --once --no-clear
```

只抓一次快照，並保留畫面輸出。

## 相容入口

舊指令仍可使用：

```powershell
python .\news_reader.py
python .\stock_index_monitor.py
```

也可直接雙擊：

- `run_news_reader.bat`
- `run_news_dashboard.bat`
- `run_dashboard.bat`
- `run_stock_dynamic.bat`
- `run_stock_index_monitor.bat`

## 資料來源

- 新聞：Google News RSS
- 台股指數與 ETF：臺灣證券交易所 MIS 即時行情端點
