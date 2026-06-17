# News Reader

一個純 Python 標準函式庫專案，可在終端機中：

- 抓取即時網路新聞並分行列出
- 監控台股四大指數
- 顯示四大指數終端走勢圖
- 顯示市值前五大 ETF 即時股價資訊

## 專案結構

```text
News Reader/
├─ main.py
├─ requirements.txt
├─ requirement.txt
├─ .gitignore
├─ README.md
├─ news_reader.py
├─ stock_index_monitor.py
├─ run_news_reader.bat
├─ run_stock_index_monitor.bat
└─ src/
   └─ news_reader/
      ├─ __init__.py
      ├─ news.py
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

## 全部參數

### main.py

```powershell
python .\main.py [command] [args]
```

| 參數 | 預設 | 說明 |
|---|---:|---|
| `command` | `news` | 要執行的功能。可用 `news` 或 `stocks`。 |
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

範例：

```powershell
python .\main.py news AI 財經 -n 10 --links
```

### stocks

```powershell
python .\main.py stocks [options]
```

| 參數 | 預設 | 說明 |
|---|---:|---|
| `-i`, `--interval` | `10` | 每幾秒更新一次指數與 ETF 資料。 |
| `--timeout` | `15` | 網路請求逾時秒數。 |
| `--no-clear` | 關閉 | 不使用原地更新，保留每次更新紀錄。 |
| `--once` | 關閉 | 只抓取一次快照後結束。 |
| `--history` | `60` | 每個指數圖表保留幾個歷史資料點。 |
| `--chart-width` | `60` | 圖表寬度；2x2 排版時單張圖會自動限制在適合並排的寬度。 |
| `--chart-height` | `8` | 圖表高度。 |
| `--no-chart` | 關閉 | 只顯示表格，不顯示四大指數走勢圖。 |
| `--no-etf` | 關閉 | 不顯示市值前五大 ETF 股價資訊。 |
| `--verify-ssl` | 關閉 | 強制驗證 TWSE SSL 憑證；若本機憑證鏈不完整，可能導致抓取失敗。 |

監控模式預設使用原地更新，減少終端閃屏。若想保留每次更新紀錄，可加上 `--no-clear`。

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
- `run_stock_index_monitor.bat`

## 資料來源

- 新聞：Google News RSS
- 台股指數與 ETF：臺灣證券交易所 MIS 即時行情端點
