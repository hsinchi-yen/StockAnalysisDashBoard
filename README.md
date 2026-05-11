# 台股個股分析 Dashboard

FastAPI + 靜態前端（Plotly）台股深度分析工具，支援 PC 瀏覽器與 Android App（WebView + Chaquopy）並行執行。

資料源：[FinMind](https://finmindtrade.com/) API（需免費/付費帳號取得 API Key）。

## 功能概覽

### 分頁式分析（5 個 Tab）

| Tab | 內容 |
|-----|------|
| 基本資訊 | 最新股價、法人買賣超、外資持股比例趨勢、EPS 季度趨勢 |
| 營收分析 | 月營收 + MA(3/6/12)、股價 vs 營收對照、YoY 年增率、趨勢摘要 |
| 獲利能力 | 毛利率 / 營業利益率 / 淨利率、ROE / ROA 趨勢 |
| 財務健康 | 流動比率 / 速動比率、每股淨值 (BVPS)、負債比率、自由現金流 |
| 估值 | DCF 內在價值、本益比河流圖、PEG Ratio、葛拉漢數 (Graham Number) |

### 投資訊號燈

查詢完成後自動顯示三項訊號燈（綠/黃/紅）：

- **成長力**：近 4 季 EPS 年增率平均
- **財務健康**：流動比率
- **估值合理性**：葛拉漢安全邊際

### UI 特性

- 頂部進度條（藍→綠/紅），即時顯示載入進度
- Skeleton 佔位動畫，各卡片獨立載入不互相阻塞
- 桌面 / 手機：頂部 Tab 導航（手機為 sticky，避免被系統導覽列遮擋）
- 行動版支援上下滑動瀏覽、雙擊放大，並提供 100% 還原與「返回上一分頁」快捷鍵
- 響應式 Plotly 圖表（觸控瀏覽優化）
- 錯誤區塊含重試按鈕

---

## 快速開始（PC Web）

```powershell
cd "c:\Users\lance.tn\AI Project\StockAnalysisDashBoard"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
.\run.cmd
# 打開瀏覽器 → http://localhost:8000
```

首次使用需要輸入 FinMind API Key 並按「儲存 Key」（儲存至 localStorage，下次自動帶入）。

---

## 測試

```powershell
pytest -q
```

---

## Android App

### 架構

- Android App 用 **WebView** 呈現與 PC 相同的靜態前端
- App 啟動時在裝置本機啟動 **Python / FastAPI** 服務（透過 Chaquopy）
- WebView 連到 `http://127.0.0.1:8000`，完全重用後端 API 與前端檔案
- Android 環境自動將 FinMind API 逾時拉長至 **60 秒**（行動網路延遲補償）

### 建置前提

- Android Studio + Android SDK（API 36）
- JDK 17

### 首次建置

```powershell
# 同步 Python 原始檔 + 靜態資源到 android_app/
.\android_prepare.cmd

# 用 Android Studio 開啟 android_app/
# → Gradle Sync → Run on device / emulator
```

### 日常更新同步（只改了前端或 Python 檔案）

```powershell
.\android_sync.cmd
# 然後在 Android Studio 重新 Build APK
```

`android_sync.cmd` 會同步：
- `api.py`, `cache.py`, `datasource_*.py`, `series_builder.py`, `charts.py`, `android_launcher.py`
- `static/index.html`, `static/app.js`, `static/styles.css`

### Android 目前限制

- Goodinfo 備援在部分 Android Python 環境中可能因 `lxml` 相依性而不可用
- Release APK 需補齊 keystore 簽章流程

---

## API 端點

| 端點 | 說明 |
|------|------|
| `GET /api/stocks/{id}/latest` | 最新股價 + 法人買賣超 |
| `GET /api/stocks/{id}/revenue` | 月營收 + MA 均線 |
| `GET /api/stocks/{id}/price_history` | 月收盤價歷史 |
| `GET /api/stocks/{id}/dividend_yield` | 殖利率歷史 |
| `GET /api/stocks/{id}/dividends_cash` | 現金股利 |
| `GET /api/stocks/{id}/roe_roa` | ROE / ROA 趨勢 |
| `GET /api/stocks/{id}/debt_ratio` | 負債比率 |
| `GET /api/stocks/{id}/free_cash_flow` | 自由現金流 |
| `GET /api/stocks/{id}/dcf` | DCF 內在價值試算 |
| `GET /api/stocks/{id}/pe_river` | 本益比河流圖 |
| `GET /api/stocks/{id}/shareholding` | 董監事持股 |
| `GET /api/stocks/{id}/turnover_days` | 存貨 / 應收款週轉天數 |
| `GET /api/stocks/{id}/margins` | 毛利率 / 營業利益率 / 淨利率 |
| `GET /api/stocks/{id}/eps_trend` | EPS 季度趨勢 + YoY |
| `GET /api/stocks/{id}/liquidity` | 流動比率 / 速動比率 / BVPS |
| `GET /api/stocks/{id}/foreign_holding` | 外資持股比例趨勢 |
| `GET /api/stocks/{id}/valuation_extra` | PEG Ratio + 葛拉漢數 |
| `GET /api/token_usage` | API Key 剩餘用量 |

所有端點接受 `?token=<api_key>` 或 `X-FinMind-Token` header。

---

## 環境變數

| 變數 | 預設 | 說明 |
|------|------|------|
| `FINMIND_API_KEY` | — | FinMind API Key（選填，也可在前端輸入） |
| `MICROECO_CACHE_DIR` | `./cache` | 快取目錄路徑 |
| `MICROECO_STATIC_DIR` | `./static` | 靜態檔案目錄 |
| `MICROECO_FINMIND_TIMEOUT` | `30` | FinMind API 逾時秒數（Android launcher 預設 60） |

---

## 部署（Docker）

```powershell
docker build -t stock-dashboard .
docker run --rm -p 8000:8000 -e FINMIND_API_KEY="<your_key>" stock-dashboard
# → http://localhost:8000
```

如需部署到 Render / Fly.io / Azure 等平台，請告知平台名稱。
