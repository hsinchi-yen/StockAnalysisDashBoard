# Implementation Plan: 台股分析 Dashboard 強化

**版本:** 1.0  
**日期:** 2026-04-25  
**平台:** Web (PC Host) + Android (同一 WebView，共用 HTML/CSS/JS)

---

## Overview

在現有 FastAPI + Plotly 架構上，分三條平行主軸強化：

1. **財務分析深度** — 新增 6 組指標覆蓋利潤率、成長力、償債力、籌碼、估值
2. **Web UI 體驗** — 分頁導航、骨架屏、讀取進度、錯誤重試
3. **Android 相容** — 響應式 CSS、Plotly 觸控優化、API 靜態資源同步

由於 Android 透過 WebView 消費同一套 `static/`，**Web UI 改善自動惠及 Android**，Android 額外只需解決：超時問題、靜態資源同步、觸控優化。

---

## Architecture — 依賴圖

```
FinMind API (外部)
       │
datasource_finmind.py  ← 新增 fetch_* 方法
       │
   api.py (FastAPI)    ← 新增 /api/* 端點
       │
static/index.html      ← 新增圖表區塊 + 分頁 UI
static/styles.css      ← 響應式 + 骨架屏 + 行動版
       │
  ┌────┴────┐
  │         │
PC 瀏覽器  Android WebView
           │
android_app/src/main/assets/static/  ← 同步 static/
```

**實作順序原則：** 後端先，前端後；新端點先，UI 改版後；響應式 CSS 先，Android 細節後。

---

## Architecture Decisions

- **不新增資料庫** — 所有新指標沿用現有 FinMind 資料集 + 本地 JSON 快取 (`cache.py`)
- **不重構 api.py 結構** — 新端點追加於現有檔案末尾，避免破壞現有測試
- **響應式 CSS 採 Mobile-First** — 統一 breakpoint `@media (max-width: 768px)`，Android WebView 直接受益
- **骨架屏用純 CSS** — 不引入額外 JS 框架，保持 CDN 最小化
- **Plotly 全部加 `responsive: true`** — 讓圖表自動適應 WebView 寬度
- **Android 靜態資源** — 每次 Web 改版後須手動同步 `static/` → `android_app/.../assets/static/`；計劃最後提供 sync 腳本

---

## 新增財務指標總覽

| 指標組 | 數據來源 | 新 API 端點 | 投資意義 |
|--------|---------|------------|---------|
| 利潤率三兄弟 (毛利/營業/淨利) | TaiwanStockFinancialStatements | `/api/margins` | 判斷競爭優勢護城河 |
| EPS 季度趨勢 + YoY 成長率 | TaiwanStockFinancialStatements | `/api/eps_trend` | 獲利動能強弱 |
| 流動比率 / 速動比率 / BVPS | TaiwanStockBalanceSheet | `/api/liquidity` | 短期償債 + 帳面價值 |
| 外資持股比例趨勢 | TaiwanStockShareholdingByForeignInstitutions | `/api/foreign_holding` | 籌碼動向 |
| PEG 比率 + Graham Number | 衍生計算 (EPS + PER + BVPS) | `/api/valuation_extra` | 成長型估值參考 |
| 月營收 YoY 成長率 | TaiwanStockMonthRevenue (已有) | 加入現有 `/api/revenue` | 營收成長動能 |

---

## Task List

### Phase 1: 後端新財務指標 (Foundation)

---

#### Task 1.1 — 利潤率三兄弟 API
**描述:** 在 `datasource_finmind.py` 新增 `fetch_margin_ratios()`，從 `TaiwanStockFinancialStatements` 提取 `GrossProfit`、`OperatingIncome`/`OperatingProfit`、`IncomeAfterTaxes` 與 `OperatingRevenue`，計算季度毛利率 / 營業利益率 / 淨利率。在 `api.py` 新增 `GET /api/margins?stock_id=&years=`。

**Acceptance criteria:**
- [ ] `fetch_margin_ratios()` 回傳 DataFrame，欄位: `quarter_label`, `gross_margin`, `operating_margin`, `net_margin` (皆為 0-100 的 % 值)
- [ ] 當 FinMind 欄位缺失時回傳空 DataFrame 而非拋出例外
- [ ] `/api/margins` 端點回傳 JSON `{quarters: [...], gross: [...], operating: [...], net: [...]}`
- [ ] 有快取（TTL 與其他端點一致）

**Verification:**
- [ ] `pytest tests/test_api.py -k margins` 通過（Mock FinMind）
- [ ] 手動呼叫 `curl localhost:8000/api/margins?stock_id=2330&years=3` 有資料

**Dependencies:** 無  
**Files:** `datasource_finmind.py`, `api.py`, `tests/test_api.py`  
**Scope:** M

---

#### Task 1.2 — EPS 季度趨勢 + YoY 成長率 API
**描述:** 新增 `fetch_eps_trend()` 方法，輸出每季 EPS 原始值與 YoY 成長率（同季比）。同時在現有月營收邏輯中計算月 YoY 成長率，加入 `/api/revenue` 回傳欄位。新增 `GET /api/eps_trend`。

**Acceptance criteria:**
- [ ] `fetch_eps_trend()` 回傳 `quarter_label`, `eps`, `eps_yoy` (%)
- [ ] YoY = (本季 EPS − 去年同季 EPS) / |去年同季 EPS| × 100，去年同季缺失時為 null
- [ ] `/api/eps_trend` 回傳 `{quarters, eps_values, eps_yoy}`
- [ ] `/api/revenue` 增加 `revenue_yoy` 欄位（月 YoY 成長率 %）

**Verification:**
- [ ] `pytest tests/test_api.py -k eps_trend` 通過
- [ ] 人工驗證 2330 近 4 季 EPS 數值與公開資料吻合

**Dependencies:** 無  
**Files:** `datasource_finmind.py`, `api.py`, `tests/test_api.py`  
**Scope:** M

---

#### Task 1.3 — 流動比率 / 速動比率 / 每股淨值 API
**描述:** 新增 `fetch_liquidity_ratios()` 從 BalanceSheet 提取 `CurrentAssets`、`CurrentLiabilities`、`Inventories`、`EquityAttributableToOwnersOfParent`、`IssuedCapital`，計算流動比率、速動比率、BVPS。新增 `GET /api/liquidity`。

**Acceptance criteria:**
- [ ] 回傳 `quarter_label`, `current_ratio`, `quick_ratio`, `bvps`
- [ ] 流動比率 = CurrentAssets / CurrentLiabilities
- [ ] 速動比率 = (CurrentAssets − Inventories) / CurrentLiabilities
- [ ] BVPS = Equity / (IssuedCapital × 100)（股數換算）
- [ ] 欄位缺失時對應值為 null，不影響其他欄位

**Verification:**
- [ ] `pytest tests/test_api.py -k liquidity` 通過
- [ ] 驗證 2330 BVPS 與 Goodinfo 數值在 5% 誤差內

**Dependencies:** 無  
**Files:** `datasource_finmind.py`, `api.py`, `tests/test_api.py`  
**Scope:** M

---

#### Task 1.4 — 外資持股比例 API
**描述:** 新增 `fetch_foreign_holding()` 使用 FinMind 的 `TaiwanStockShareholdingByForeignInstitutions` 資料集，回傳月/季持股比例趨勢。新增 `GET /api/foreign_holding`。

**Acceptance criteria:**
- [ ] 回傳 `{dates: [...], holding_pct: [...]}`（持股佔流通股 % 的月度時序）
- [ ] 資料集不存在或 quota 超限時，回傳 `{dates: [], holding_pct: [], error: "..."}`
- [ ] 快取 24 小時（持股資料更新較慢）

**Verification:**
- [ ] `pytest tests/test_api.py -k foreign_holding` 通過
- [ ] 手動查 2330 外資持股比例與公開資料吻合

**Dependencies:** 無  
**Files:** `datasource_finmind.py`, `api.py`, `tests/test_api.py`  
**Scope:** S

---

#### Task 1.5 — PEG 比率 + Graham Number 計算端點
**描述:** 新增 `GET /api/valuation_extra`，後端組合現有 EPS（年均）、PER、BVPS，計算 PEG Ratio 與 Graham Number，一起回傳。PEG = PER / EPS_YoY_Growth；Graham = √(22.5 × EPS_avg × BVPS_latest)。

**Acceptance criteria:**
- [ ] 回傳 `{peg: float|null, graham_number: float|null, current_price: float, margin_of_safety_pct: float|null}`
- [ ] EPS 成長率採近 3 年複合年成長率（CAGR）
- [ ] 任一輸入為 null 時對應輸出為 null，不中斷回應
- [ ] Graham Number 提供與當前股價的安全邊際 %

**Verification:**
- [ ] `pytest tests/test_api.py -k valuation_extra` 通過
- [ ] 手動驗算 2330 Graham Number 與公開試算吻合

**Dependencies:** Task 1.2（EPS trend 邏輯）, Task 1.3（BVPS）  
**Files:** `api.py`, `tests/test_api.py`  
**Scope:** S

---

### Checkpoint A — Phase 1 完成
- [ ] `pytest -q` 全部通過（含新測試）
- [ ] 所有 5 個新端點可正常回應（curl 或 httpie 手動測試）
- [ ] 快取正常運作（第二次呼叫無 FinMind 請求）
- [ ] **人工確認後再進入 Phase 2**

---

### Phase 2: Web UI — 新財務指標顯示

---

#### Task 2.1 — 利潤率三兄弟圖 + 表格
**描述:** 在 `static/index.html` 新增「利潤率」卡片區，呼叫 `/api/margins`，以 Plotly 折線圖顯示毛利率 / 營業利益率 / 淨利率季度趨勢；下方附可展開資料表。

**Acceptance criteria:**
- [ ] 三條線顏色有區分，圖例清楚
- [ ] 游標 hover 顯示季度標籤 + 三項 % 值
- [ ] 空資料時顯示 "本股無法取得利潤率資料" 提示
- [ ] 資料表可展開/收合，欄位: 季度 | 毛利率 | 營業利益率 | 淨利率

**Verification:**
- [ ] 瀏覽器開啟 localhost:8000，查詢 2330，利潤率圖正確顯示
- [ ] 查詢無資料股票（如 ETF），顯示提示而非空白

**Dependencies:** Task 1.1  
**Files:** `static/index.html`, `static/styles.css`  
**Scope:** M

---

#### Task 2.2 — EPS 趨勢圖 + 月營收 YoY 成長率圖
**描述:** 新增「EPS 季度趨勢」卡片（折線 + 柱狀組合圖，EPS 值 + YoY 成長率雙軸）。在現有月營收圖下方追加「月營收 YoY 成長率」柱狀圖（正成長綠色、負成長紅色）。

**Acceptance criteria:**
- [ ] EPS 圖：左軸 EPS 值（折線），右軸 YoY% （柱狀），季度標籤
- [ ] YoY 成長率柱狀圖：顏色正負分離，hover 顯示具體 %
- [ ] 兩圖均有 responsive: true

**Verification:**
- [ ] 瀏覽器查詢 2330，EPS 圖數據與公開季報吻合
- [ ] YoY 柱狀圖顏色正確（正綠負紅）

**Dependencies:** Task 1.2  
**Files:** `static/index.html`, `static/styles.css`  
**Scope:** M

---

#### Task 2.3 — 流動/速動比率 + BVPS 圖
**描述:** 新增「財務健康」卡片，上半部雙折線圖（流動比率 / 速動比率），下半部 BVPS 趨勢折線圖。

**Acceptance criteria:**
- [ ] 流動比率 / 速動比率雙線圖，標示安全閾值（流動 > 2、速動 > 1）水平虛線
- [ ] BVPS 折線圖，hover 顯示季度 + BVPS 值
- [ ] 空資料顯示提示

**Verification:**
- [ ] 查詢 2330，數值與公開資料合理吻合
- [ ] 安全閾值線正確渲染

**Dependencies:** Task 1.3  
**Files:** `static/index.html`, `static/styles.css`  
**Scope:** M

---

#### Task 2.4 — 外資持股比例趨勢圖
**描述:** 在「法人買賣」卡片下方新增「外資持股比例趨勢」折線圖，呼叫 `/api/foreign_holding`。

**Acceptance criteria:**
- [ ] 折線圖顯示月度外資持股 %，x 軸為日期
- [ ] 顯示當前持股 % 的數值標籤
- [ ] 若端點回傳 error 欄位，顯示提示訊息

**Verification:**
- [ ] 查詢 2330，外資持股 % 與公開資料合理吻合

**Dependencies:** Task 1.4  
**Files:** `static/index.html`, `static/styles.css`  
**Scope:** S

---

#### Task 2.5 — Graham Number + PEG 估值面板擴充
**描述:** 在現有 DCF 互動區旁新增估值比較卡，顯示 Graham Number、PEG Ratio、Graham 安全邊際 %，並與 DCF 公平價並排對比。

**Acceptance criteria:**
- [ ] 卡片顯示: Graham Number (元) | PEG Ratio | Graham 安全邊際 %
- [ ] 顏色語義：安全邊際 > 20% 綠色，0-20% 黃色，< 0% 紅色
- [ ] null 值顯示 "N/A" 而非空白或 NaN

**Verification:**
- [ ] 手動驗算 2330 Graham Number 與頁面顯示吻合
- [ ] 顏色判斷邏輯正確

**Dependencies:** Task 1.5  
**Files:** `static/index.html`, `static/styles.css`  
**Scope:** S

---

#### Task 2.6 — 投資策略摘要卡（綜合決策輔助）
**描述:** 在頁面頂部「趨勢摘要」下方新增「投資策略燈號」卡，整合所有指標輸出 3 個信號燈：成長力（EPS YoY + 營收 YoY）、財務健康（ROE + 流動比 + 負債比）、估值合理性（DCF MOS + Graham MOS + PEG）。

**Acceptance criteria:**
- [ ] 每個燈號 3 色：🟢 正面 / 🟡 中性 / 🔴 警示
- [ ] hover/展開可查看組成指標的具體數值與閾值說明
- [ ] 整合邏輯在前端 JS 完成（不新增後端端點）
- [ ] 資料未載入完成時顯示 "計算中..."

**Verification:**
- [ ] 查詢多檔股票，燈號顏色與預期一致
- [ ] 手機視窗下排版不破版

**Dependencies:** Task 2.1–2.5  
**Files:** `static/index.html`, `static/styles.css`  
**Scope:** M

---

### Checkpoint B — Phase 2 完成
- [ ] PC 瀏覽器全部 6 個新功能正確顯示
- [ ] 所有新圖表在 1280px / 768px / 375px 視窗下不破版
- [ ] 空資料情境均有提示文字
- [ ] **人工確認後再進入 Phase 3**

---

### Phase 3: Web UI 體驗優化

---

#### Task 3.1 — 分頁導航 (Tab Navigation)
**描述:** 在 `static/index.html` 頂部新增 Tab 列，把所有卡片分組到 5 個分頁：基本資訊 | 營收分析 | 獲利能力 | 財務健康 | 估值。Tab 切換以純 CSS class 控制顯示/隱藏，無頁面刷新。

**分頁內容配置:**
- 基本資訊: 最新股價、法人買賣、外資持股比例
- 營收分析: 月營收 + MA、月 YoY 成長、股價 vs 營收、時間趨勢對照
- 獲利能力: EPS 趨勢、利潤率三兄弟、ROE/ROA
- 財務健康: 流動/速動比率、BVPS、負債比、自由現金流、營運週轉天數
- 估值: DCF 互動、Graham/PEG 面板、本益比河流圖、配息表

**Acceptance criteria:**
- [ ] Tab 列固定在查詢表單下方，分頁切換流暢
- [ ] 首次查詢自動跳到「基本資訊」Tab
- [ ] 行動裝置 (< 768px) Tab 可橫向滾動
- [ ] 當前 Tab 有明顯視覺標示

**Verification:**
- [ ] 瀏覽器 375px 視窗下 Tab 可正常切換
- [ ] Plotly 圖表在 Tab 切換後重新 resize（呼叫 `Plotly.relayout`）

**Dependencies:** Task 2.1–2.6  
**Files:** `static/index.html`, `static/styles.css`  
**Scope:** M

---

#### Task 3.2 — 骨架屏 (Skeleton Loading)
**描述:** 在 CSS 新增 `.skeleton` 動畫元件，查詢觸發後立即替換各卡片內容為骨架屏，資料回傳後替換為真實內容。

**Acceptance criteria:**
- [ ] 每個圖表區域有高度佔位的骨架屏（灰色脈動動畫）
- [ ] 查詢按鈕按下後 < 100ms 顯示骨架屏
- [ ] 資料載入完成後骨架屏無閃爍地切換為真實內容
- [ ] 骨架屏使用純 CSS（`@keyframes shimmer`），無額外 JS 庫

**Verification:**
- [ ] 網速限速到 Slow 3G，骨架屏可見且動畫正常
- [ ] 所有圖表區域均有骨架屏（目視確認）

**Dependencies:** Task 3.1  
**Files:** `static/styles.css`, `static/index.html`  
**Scope:** S

---

#### Task 3.3 — 全局讀取進度條 + 錯誤重試
**描述:** 在頁面頂端新增細條進度條（類 GitHub 頂部進度條），API 請求期間逐步填充，完成後淡出。對所有 fetch 呼叫包裝統一 error handler，失敗時在卡片顯示「載入失敗，點此重試」按鈕。

**Acceptance criteria:**
- [ ] 進度條在查詢開始時出現，完成時消失（CSS transition）
- [ ] 進度條顏色：進行中藍色、成功消失、失敗紅色閃一下
- [ ] 所有 API 區塊失敗時顯示 retry 按鈕而非空白
- [ ] retry 按鈕只重試失敗的端點，不重新查詢全部

**Verification:**
- [ ] 斷網測試，進度條變紅，retry 按鈕出現
- [ ] 網路恢復後 retry 正確重載該區塊

**Dependencies:** Task 3.2  
**Files:** `static/index.html`, `static/styles.css`  
**Scope:** S

---

### Checkpoint C — Phase 3 完成
- [ ] PC 瀏覽器完整功能流程跑通（查詢 → 骨架屏 → 資料 → 分頁切換）
- [ ] 斷網 + 重試流程正常
- [ ] **人工確認後再進入 Phase 4**

---

### Phase 4: Android UI 優化 + 修復

---

#### Task 4.1 — 響應式 CSS 行動版優化
**描述:** 在 `static/styles.css` 完善 `@media (max-width: 768px)` 規則，確保所有卡片、表格、Tab 在手機視窗下正確排版。行動版 Tab 改為固定底部導航列（Bottom Nav）。

**Acceptance criteria:**
- [ ] 375px 寬度下無橫向 overflow
- [ ] 所有表格在行動版轉為卡片式或橫向捲動
- [ ] 底部導航列 5 個分頁圖示清晰可點（最小點擊區 44px）
- [ ] 字體大小行動版不低於 14px

**Verification:**
- [ ] Chrome DevTools 切換到 iPhone 14 尺寸，目視確認各區塊排版
- [ ] 在 Android WebView 中開啟，Tab 可正常切換

**Dependencies:** Task 3.1  
**Files:** `static/styles.css`  
**Scope:** M

---

#### Task 4.2 — Plotly 圖表觸控 + resize 優化
**描述:** 所有 `Plotly.newPlot()` 呼叫加入 `responsive: true`，config 加入 `{scrollZoom: false, displayModeBar: false}` (行動版隱藏工具列)，並在 WebView resize 事件時呼叫 `Plotly.relayout`。

**Acceptance criteria:**
- [ ] 所有圖表在旋轉螢幕後自動重新適應寬度
- [ ] 行動版工具列隱藏，觸控縮放正常
- [ ] 圖表 hover tooltip 在觸控時也能正常顯示（改用 `hovermode: 'x unified'`）

**Verification:**
- [ ] Android WebView 中旋轉螢幕，圖表正確 resize
- [ ] 觸控圖表 hover tooltip 可見

**Dependencies:** Task 4.1  
**Files:** `static/index.html`  
**Scope:** S

---

#### Task 4.3 — Android API 超時修復 + 靜態資源同步腳本
**描述:** 在 `android_launcher.py` 和 `api.py` 中增加 Android 環境偵測邏輯（檢查環境變數 `ANDROID_ENV`），對慢速網路端點增加 timeout 至 60s，並新增 `android_sync.cmd` 腳本將 `static/` 同步到 `android_app/src/main/assets/static/`。

**Acceptance criteria:**
- [ ] `android_sync.cmd` 執行後 android_app 的 static 資料夾與根目錄 static 完全一致
- [ ] Android 環境下 FinMind API timeout 預設 60s（而非現有 30s）
- [ ] 網路超時時 Android WebView 顯示 retry 按鈕（利用 Task 3.3 機制）

**Verification:**
- [ ] 執行 `android_sync.cmd` 後比對兩目錄 md5 一致
- [ ] 在 Android 模擬器或實機中查詢 2330，主要指標正常顯示

**Dependencies:** Task 3.3, Task 4.2  
**Files:** `android_launcher.py`, `api.py`, `android_sync.cmd`  
**Scope:** S

---

### Checkpoint D — Phase 4 完成 (Final)
- [ ] Android WebView 主要功能全數正常顯示
- [ ] PC 與 Android 版本功能一致
- [ ] `android_sync.cmd` 可重複執行
- [ ] `pytest -q` 全部通過
- [ ] 所有新端點有快取，避免重複呼叫 FinMind
- [ ] **最終人工驗收**

---

## Parallelization Opportunities

| 可並行 | 說明 |
|--------|------|
| Task 1.1 + 1.2 + 1.3 + 1.4 | 四個後端端點互不依賴，可同時開發 |
| Task 2.1 + 2.2 + 2.3 + 2.4 | 各自依賴不同的 Phase 1 Task，UI 可並行 |
| Task 3.2 + 3.3 | 骨架屏和進度條獨立，可同時開發 |

| 必須序列 | 說明 |
|----------|------|
| Phase 1 → Phase 2 | 後端端點必須先存在 |
| Task 1.5 依賴 1.2 + 1.3 | Graham Number 需要 BVPS 和 EPS 邏輯 |
| Task 2.6 依賴 2.1–2.5 | 策略燈號整合所有指標 |
| Task 3.1 → 3.2 → 3.3 | UI 結構先定，再加動畫，再加錯誤處理 |
| Task 4.1 → 4.2 → 4.3 | CSS 先，Plotly 後，同步最後 |

---

## Risks and Mitigations

| 風險 | 影響 | 緩解策略 |
|------|------|---------|
| FinMind `TaiwanStockShareholdingByForeignInstitutions` 可能需要付費帳號 | Task 1.4 資料取不到 | 端點設計 graceful fallback，UI 顯示 "需付費帳號" |
| 部分股票（如 ETF）缺少財務報表欄位 | 利潤率、流動比率回空 | 所有指標有空資料提示，不影響其他圖表 |
| Android Chaquopy 的 Python 版本可能不支援新套件 | Task 4.3 失敗 | 不新增 requirements.txt 套件，全用現有資源 |
| api.py 已達 52,762 行，搜尋困難 | 新端點難以找到插入點 | 在檔尾新增，並加 `# === NEW: [功能名] ===` 注釋標記 |
| Plotly 圖表在 WebView 的記憶體消耗 | Android 低階裝置 OOM | 行動版限制每圖最多 200 資料點，超過時降採樣 |

---

## Open Questions

1. **外資持股資料集**: `TaiwanStockShareholdingByForeignInstitutions` 是否在目前帳號權限內？需實際測試後確認 Task 1.4 可行性。
2. **Android 實機測試**: 目前無 Android SDK 環境，Task 4.3 的實機驗證需要在有 Android Studio 的環境進行，或提供模擬器截圖。
3. **分頁初始 Tab**: 首次查詢後是否自動跳 Tab，還是讓使用者自己切？建議預設停在「基本資訊」，但需確認。
4. **利潤率資料可用性**: 部分傳產股 `OperatingIncome` 在 FinMind 的 type 名稱可能不同，需實際查驗幾檔股票後確認 fallback 欄位清單。
