# Enhancement Plan: 台股分析 Dashboard — 快速分析頁、效能優化與待辦收尾

**版本:** 2.0
**日期:** 2026-11-01
**平台:** Web (PC Host) + Android (同一 WebView，共用 `static/`)
**前置文件:** `tasks/plan-2026-04-25.md`（v1.0 基礎建設）、`DCF_GUIDE.md`
**取代:** enhancement_plan.md v1.0（2026-05-22）；其已完成項目列於「v1.0 已完成」，未完成項目併入 Phase 4

---

## Overview

在既有 FastAPI + Plotly + JSON 快取架構上，分四條主軸：

1. **快速分析頁** — 新增第一個分頁，查詢後預設顯示：價格階梯圖（安全邊際價 ← 現價 → 成長估值價）、風險/上檔象限圖、一句話判定、三燈號。既有六個分頁原樣保留。
2. **兩個估值指標** — `安全邊際估值`（floor）與 `成長期估值`（ceiling），皆由既有端點輸出組合而成，不新增資料抓取。
3. **效能** — 後端 FinMind dataset 層共用快取（省呼叫、省配額）、端點耗時可觀測、gzip；前端分頁延遲繪圖、快速分頁優先渲染、先顯示上次結果。
4. **v1.0 待辦收尾** — TWSE OpenAPI、融資券、R1/R2、隔夜排程、快取 TTL、MOPS 重訊。

**實作順序：** 效能量測 → 快速分析後端 → 快速分析前端 → 效能優化 → v1.0 待辦。前三個 Phase 是交付重點。

## v1.0 已完成（不再列任務）

Task 1.3 Goodinfo 防擋、1.4 法人資料日期、2.2–2.4 R4–R8 + 風險分組 UI、2.5 EPS CAGR 修正、3.1–3.3 產業感知評分、R3 Sloan。

## Architecture Decisions

- **估值組合用中位數，不用平均** — floor / ceiling 各取可用候選值的中位數，單一模型極端值不致拉偏；候選不足 1 個時該側為 `null`，UI 顯示「資料不足」而非假數字。
- **快速分析後端在既有端點函式之上聚合，不重構它們** — 新端點 `/quick_summary` 在進程內直接呼叫 `dcf()`, `valuation_extra()`, `forward_rolling_eps()`, `pe_river()`, `latest()`（它們是普通函式，且各自有 CacheStore 快取），用 `ThreadPoolExecutor` 並行，再交給純函式 `quick_analysis.compute_summary()` 組合。純函式可單測；端點薄。
- **風險層不阻塞估值層** — `buy_score` 最慢，`/quick_summary` 不等它。前端先畫階梯圖（估值到達即畫），`buy_score` 到達後再疊上風險著色與象限圖點位、更新判定句。
- **階梯圖用 HTML/CSS，象限圖用 Plotly** — 階梯圖是首屏，純 DOM 零依賴即時可見；象限圖在其下方，Plotly 已載入。
- **效能先量測再優化** — Task 0 先加耗時紀錄與基準數字，所有效能任務以此驗收。
- **dataset 層快取是效能主力** — 在 `FinMindClient._get_dataset()` 加跨請求的磁碟快取（`CacheStore`，key = dataset|stock|start|end，當日有效），所有端點零改動即受益；比重構 22 個端點共用 client 便宜且安全。
- **不在 `buy_score` 內並行 FinMind 呼叫** — 先前 commit 已為 rate-limit 加 `_FETCH_DELAY`；dataset 快取讓其他端點先把資料拉進快取後，`buy_score` 的多數 fetch 直接命中，效果等同並行而無風險。

## 快速分析頁 — 指標定義

**安全邊際估值（floor）** = median of 可用候選：
| 候選 | 來源 | 條件 |
|---|---|---|
| DCF 進場價 | `dcf.entry_price` | 非 null、> 0 |
| 葛拉漢數 | `valuation_extra.graham_number` | 非 null、> 0 |
| 低檔本益比價 | `pe_river.bands.p25 × eps_ttm`（`eps_ttm_rows` 最後一筆） | 兩者 > 0 |

**成長期估值（ceiling）** = median of 可用候選：
| 候選 | 來源 | 條件 |
|---|---|---|
| 前瞻估值 M1 中位 | `forward_rolling_eps.price_mid` | 非 null |
| 前瞻估值 M2 中位 | `forward_rolling_eps.price_mid_m2` | 非 null |
| PEG=1 合理價 | `eps_ttm × min(eps_cagr, 30)` | `eps_cagr > 0` |
| 高檔本益比價 | `pe_river.bands.p75 × eps_ttm` | 兩者 > 0 |

**衍生欄位**：`downside_pct = (floor − price)/price`、`upside_pct = (ceiling − price)/price`、`gap_pct = (ceiling − floor)/price`、`position = (price − floor)/(ceiling − floor)`（clamp 0–1；floor ≥ ceiling 時 `position = null`，UI 標「模型分歧」）。

**風險等級**（來自 `buy_score`）：`risk_score 0 → 低`、`1 → 中`、`≥2 → 高`（與既有「2 項以上下調評等」一致）。

**判定句**（`verdict`）：
| 條件 | verdict | 文案 |
|---|---|---|
| risk 高 | `avoid` | 偵測到 N 項排雷警示，先避開 |
| price < floor 且 risk 低/中 | `buy` | 現價低於安全邊際估值，具下檔保護 |
| position < 0.5 且 risk 低 | `accumulate` | 現價位於區間下半，可分批佈局 |
| position < 0.5 且 risk 中 | `watch` | 估值合理但有 1 項警示，列入觀察 |
| position ≥ 0.5 且 price ≤ ceiling | `hold` | 現價偏向成長估值，上檔有限 |
| price > ceiling | `wait` | 現價高於成長估值，等待回檔 |
| floor/ceiling 任一 null | `insufficient` | 估值資料不足 |

---

## Task List

### Phase 0：效能基準

#### Task 0.1 — 端點耗時紀錄 + 基準量測
**描述:** `api.py` 加 FastAPI middleware：每個 `/api/*` 回應加 `X-Elapsed-Ms` header，並以 `logging.info` 記錄 `path, elapsed_ms, cache_hit`（端點內以 `request.state.cache_hit = True` 標記命中；先只標 `buy_score`, `dcf`, `valuation_extra`, `forward_rolling_eps`, `pe_river`, `latest`）。`FinMindClient._get_dataset()` 加計數器（模組層 `FINMIND_CALL_COUNT`），`/api/health` 回傳目前計數。寫 `docs/perf-baseline.md`：以 2330、年數 3，分別記錄冷快取（清 `.cache/`）與熱快取下：23 個請求總完成時間、最慢端點、FinMind 呼叫數、首屏可見時間（DevTools Performance）。
**Acceptance criteria:**
- [ ] 每個 API 回應帶 `X-Elapsed-Ms`
- [ ] `/api/health` 回 `finmind_calls_since_start`
- [ ] `docs/perf-baseline.md` 有冷/熱兩組數字（Desktop Chrome；Android 若方便一併記）
**Verification:** `pytest -q`（既有測試不受 middleware 影響）；手動查詢並讀 log
**Files:** `api.py`, `datasource_finmind.py`, `docs/perf-baseline.md`
**Scope:** S

---

### Phase 1：快速分析後端

#### Task 1.1 — `quick_analysis.py` 純函式
**描述:** 新模組，無 I/O。`compute_summary(latest, dcf, valuation_extra, forward, pe_river) -> dict` 依「指標定義」算出 `price, floor, floor_components[], ceiling, ceiling_components[], downside_pct, upside_pct, gap_pct, position`。每個 component 為 `{"name", "value", "source"}`，UI 要列出用了哪些模型。`apply_risk(summary, buy_score) -> dict` 加入 `risk_level, risk_count, risk_names[], pass_rate, verdict, verdict_text`。任一輸入為 `None`（端點失敗）時對應候選跳過，函式不拋例外。
**Acceptance criteria:**
- [ ] 三個 floor 候選齊全時 floor = 中位數；只剩一個時 floor = 該值；零個時 floor = null、verdict = insufficient
- [ ] floor ≥ ceiling 時 `position = null`，verdict 退回只看 price vs floor/ceiling
- [ ] 判定表七種情境各有一個測試案例
**Verification:** `pytest tests/test_quick_analysis.py`（純 dict fixture，無 mock）
**Files:** `quick_analysis.py`, `tests/test_quick_analysis.py`
**Scope:** M

#### Task 1.2 — `/api/stocks/{id}/quick_summary` 端點
**描述:** 加在 `api.py` 檔尾。參數同其他端點（`years`, `token`, DCF 三參數帶預設）。用 `ThreadPoolExecutor(max_workers=5)` 並行呼叫 `latest()`, `dcf()`, `valuation_extra()`, `forward_rolling_eps()`, `pe_river()`；個別失敗（`HTTPException`/其他例外）記進 `warnings[]`、該輸入視為 `None`。結果經 `compute_summary()` 回傳；**不呼叫 `buy_score`**。回應不另外快取（成分端點各自已快取，熱查詢 < 100ms）。
**Acceptance criteria:**
- [ ] 五個成分端點 mock 後，回應含 floor/ceiling/position 與 components
- [ ] 其中一個成分拋 502 時仍回 200，`warnings` 含其名稱，其餘候選照常
- [ ] 熱快取下 `X-Elapsed-Ms` < 300
**Verification:** `pytest -k quick_summary`；手動 `curl` 2330 比對各成分端點數值
**Files:** `api.py`, `tests/test_api.py`
**Scope:** S · **Dependencies:** 1.1

#### Checkpoint A
- [ ] `pytest -q` 全通過
- [ ] 2330 / 2891（金融）/ 一檔虧損股 的 `quick_summary` 數值合理（floor < ceiling；虧損股 verdict = insufficient 或 avoid）
- [ ] **人工確認 → Phase 2**

---

### Phase 2：快速分析前端

#### Task 2.1 — 新分頁骨架 + 燈號卡搬移
**描述:** `index.html` `tab-nav` 最前加 `<button data-tab="tab-quick">快速分析</button>`，新增 `<div id="tab-quick" class="tab-panel">`，內含四個區塊佔位：`#quickVerdict`（判定句 + 風險徽章）、`#quickLadder`（階梯圖）、`#quickQuadrant`（象限圖）、既有 `#signalCard`（從 `tab-basic` 搬入）。`runQuery()` 成功啟動後 `switchTab("tab-quick")`；`loadQueryCache()` 恢復時亦切到該分頁。`renderSignalCard()` 不改邏輯。
**Acceptance criteria:**
- [ ] 查詢後自動停在快速分析分頁；其他六個分頁內容與現狀一致
- [ ] 燈號卡在新分頁正常顯示；`tab-basic` 不再有它
- [ ] 375px 寬無橫向捲動；底部導航（Android）多一個項目不破版
**Verification:** 瀏覽器 1280 / 768 / 375px 檢查；`switchTab` 既有返回鍵邏輯（`updateTabBackButtonState`）仍正確
**Files:** `static/index.html`, `static/app.js`, `static/styles.css`
**Scope:** S

#### Task 2.2 — 價格階梯圖（HTML/CSS）
**描述:** `app.js` 新增 `renderQuickLadder(summary)`：一條水平軌道，左端 floor、右端 ceiling、現價為軌道上的圓點與標籤；floor→price 區段紅、price→ceiling 區段綠；軌道下方兩個數字「下檔 −X%」「上檔 +Y%」與 `gap_pct`。軌道下列出 floor/ceiling 各用了哪些模型（components，小字、可點擊跳到估值分頁對應卡片）。`position = null` 顯示「模型分歧」提示；`insufficient` 顯示灰色空軌道與「估值資料不足」。風險尚未到達時軌道外框中性色；`apply_risk` 後依 `risk_level` 改外框（低=綠、中=黃、高=紅）並顯示雷項名稱 chips。`runQuery()` 新增 `pQuick = fetchJson(`${base}/quick_summary?...`)`，到達即渲染；`pBuyScore` 到達後呼叫 `applyRiskToQuick(buyScoreData)`（前端用與 `quick_analysis.apply_risk` 同一張判定表；表以常數寫在 `app.js`，註明與後端同步）。
**Acceptance criteria:**
- [ ] 估值到達即可見階梯圖（不等 buy_score）；buy_score 到達後外框與判定句更新，無閃爍重排
- [ ] 現價 < floor 時圓點在軌道左側外、顯示「低於安全邊際」；> ceiling 時在右側外
- [ ] 暗色模式配色正確（沿用 `styles.css` 既有 `--` 變數）
**Verification:** 瀏覽器用 2330（區間內）、一檔高估股（> ceiling）、一檔虧損股（insufficient）各看一次；DevTools 切 `prefers-color-scheme`
**Files:** `static/app.js`, `static/styles.css`, `static/index.html`
**Scope:** M · **Dependencies:** 1.2, 2.1

#### Task 2.3 — 風險/上檔象限圖（Plotly）
**描述:** `renderQuickQuadrant(summary, buyScore)`：x = 風險分數（`risk_score × 2 + 未通過指標數 / 4`，0–10 刻度）、y = `upside_pct`；單一標記點 + 四象限底色（左上「值得買進」、右上「觀察」、左下「觀望」、右下「避開」）、虛線分界 x=3、y=0。hover 顯示雷項與未通過指標名。`buy_score` 未到達前不畫（顯示 skeleton）。`rerenderDashboard()` 加入兩個 quick 渲染，讓主題切換與快取恢復時重繪。
**Acceptance criteria:**
- [ ] 四象限底色與標籤在亮/暗主題皆可讀
- [ ] 2330 落在左半；`risk_score ≥ 2` 的股票落在右半
- [ ] `localStorage` 恢復後象限圖與階梯圖重現，無需重查
**Verification:** 瀏覽器三檔股票；重新整理頁面驗證恢復
**Files:** `static/app.js`, `static/styles.css`
**Scope:** S · **Dependencies:** 2.2

#### Checkpoint B
- [ ] `pytest -q` 全通過；cache-buster `?v=32 → 33`
- [ ] 查詢 → 快速分析頁 → 點 component 跳到估值分頁對應卡片 → 返回鍵回快速分析
- [ ] 同步 `static/*`、`api.py`、`quick_analysis.py` 到 `embedded_deployment/` 與 `android_app/`（`android_sync.cmd` 清單加 `quick_analysis.py`）
- [ ] **人工確認 → Phase 3**

---

### Phase 3：效能優化

#### Task 3.1 — FinMind dataset 層共用快取
**描述:** `FinMindClient._get_dataset()` 在 `_ds_cache` 之後、HTTP 之前查 `CacheStore`：key = `finmind_ds|dataset|stock_id|start|end|asof=today`，命中直接回傳 list。寫入時只在非空結果寫。快取目錄沿用 `MICROECO_CACHE_DIR`（client 在 `__post_init__` 建一個 `CacheStore`，或由 `api.py` 注入）。環境變數 `FINMIND_DS_CACHE=0` 可關閉（測試與除錯）。
**Acceptance criteria:**
- [ ] 同一天第二個端點請求同一 dataset+range 不發 HTTP（測試：mock `requests` 計數為 1）
- [ ] 空結果不寫快取（避免 15:30 法人資料窗口被空值卡住，同 Task 1.4 教訓）
- [ ] 冷查詢 FinMind 呼叫數相對基準下降 ≥ 50%（寫回 `docs/perf-baseline.md`）
**Verification:** `pytest tests/test_finmind_ds_cache.py`；`/api/health` 計數器前後比較
**Files:** `datasource_finmind.py`, `api.py`, `tests/test_finmind_ds_cache.py`, `docs/perf-baseline.md`
**Scope:** M · **Dependencies:** 0.1

#### Task 3.2 — 前端分頁延遲繪圖
**描述:** `app.js` 建立 `pendingRenders: Map<tabId, Array<fn>>`。各 `plot*/render*` 呼叫改經 `scheduleRender(tabId, fn)`：若 `tabId` 為當前分頁立即執行，否則入隊；`switchTab()` 切換時 flush 該分頁隊列（`requestAnimationFrame` 分批，每幀最多 3 張）。快速分析分頁與 `signalCard` 永遠立即渲染。`rerenderDashboard()` 同樣走此機制。
**Acceptance criteria:**
- [ ] 查詢後僅快速分析分頁的圖被繪製（DevTools 檢查其他分頁 `.chart` 容器為空）
- [ ] 切到任一分頁 ≤ 1 幀內開始出圖，資料與現狀一致
- [ ] 查詢到首屏可見時間相對基準下降 ≥ 40%（Desktop）
**Verification:** DevTools Performance 錄製查詢流程；`docs/perf-baseline.md` 更新
**Files:** `static/app.js`, `docs/perf-baseline.md`
**Scope:** M · **Dependencies:** 2.3

#### Task 3.3 — 先顯示上次結果 + 請求優先序
**描述:** (1) `runQuery()` 若 `localStorage` 有同代號快取，先 `restoreFromState()` 顯示並標「更新中…」，新資料到達再逐卡替換（避免整頁 skeleton）。(2) 請求分兩批：第一批 `latest, quick_summary, buy_score` 立即發出；第二批其餘 19 個延後 `setTimeout(…, 0)` 於第一批任一回應後發出，確保快速分頁的請求不排在瀏覽器 6 連線限制之後。(3) `fetchJson` 加 `AbortController`，使用者連續查詢時取消前一批。
**Acceptance criteria:**
- [ ] 重查同代號時無全頁 skeleton，狀態列顯示「更新中」
- [ ] Network 面板中 `quick_summary` 的開始時間在前三名
- [ ] 快速連按兩次查詢不產生交錯渲染
**Verification:** 瀏覽器 Network/Performance 面板
**Files:** `static/app.js`
**Scope:** S · **Dependencies:** 2.2

#### Task 3.4 — 傳輸層：gzip + 靜態快取標頭
**描述:** `api.py` 加 `GZipMiddleware(minimum_size=1024)`；`StaticFiles` 掛載保留，另在 middleware 為 `/static/*` 回應加 `Cache-Control: public, max-age=31536000, immutable`（依賴既有 `?v=` cache-buster 失效）。`price_history`, `shareholding_spread` 等大回應受益最多。
**Acceptance criteria:**
- [ ] 回應 header 含 `content-encoding: gzip`（> 1KB 時）
- [ ] `/static/app.js` 回應含 `Cache-Control: immutable`；bump `?v=` 後瀏覽器重新下載
**Verification:** `curl -H "Accept-Encoding: gzip" -I`；瀏覽器二次載入 Network 顯示 `(memory cache)`
**Files:** `api.py`
**Scope:** XS

#### Checkpoint C
- [ ] `pytest -q` 全通過
- [ ] `docs/perf-baseline.md` 有「優化後」欄：冷查詢 FinMind 呼叫數、首屏時間、23 請求總時間，皆附改善百分比
- [ ] Android WebView 實測一次（`android_sync.cmd` + 重建 APK）
- [ ] 同步 `embedded_deployment/`、`android_app/`
- [ ] **人工確認 → Phase 4**

---

### Phase 4：v1.0 待辦收尾

#### Task 4.1 — `datasource_twse_open.py`
封裝 `openapi.twse.com.tw/v1/`：`fetch_stock_day_all(sid)`（`exchangeReport/STOCK_DAY_ALL`）、`fetch_institutional_t86(sid)`（`fund/T86`）、`fetch_margin_margn(sid)`（`exchangeReport/MI_MARGN`）。全市場 JSON 以模組層 TTL 快取 1 小時（照 `datasource_goodinfo._gc_get/_gc_set` 樣式），三方法共用。欄位對齊 FinMind：價格 `date/open/max/min/close/spread/Trading_Volume/Trading_money/Trading_turnover`；法人 `date/name/buy/sell/net`（name 映射 `Foreign_Investor/Investment_Trust/Dealer_self/Dealer_Hedging`）；融資券 `date/margin_balance/short_balance/short_margin_ratio`。**只涵蓋上市股**，上櫃靠 fallback。
- [ ] 查無該股 / HTTP 失敗回空 DataFrame 不拋例外；同小時不重發請求；帶 User-Agent
- **Verification:** `pytest tests/test_twse_open.py`（mock `requests.get`）· **Files:** `datasource_twse_open.py`, `tests/test_twse_open.py` · **Scope:** M

#### Task 4.2 — `/latest` TWSE 優先 + FinMind fallback
`api.py:288-437`：價格段先 `fetch_stock_day_all`，空則走現有 FinMind；法人段 FinMind 5 日窗口結果為 `unavailable` 時再試 `fetch_institutional_t86`。回應加 `price_source` / `institutional_source`。
- [ ] TWSE 有資料時不呼叫 `fetch_stock_price`（測試中 monkeypatch 為 `assert False`）；TWSE 空時既有 3 個 institutional 測試不改即通過
- **Verification:** `pytest -q` + `test_latest_prefers_twse`, `test_latest_falls_back_to_finmind` · **Files:** `api.py`, `tests/test_api.py` · **Scope:** S · **Deps:** 4.1

#### Task 4.3 — `/margin_trading` + 基本資訊卡片
新端點回 `fetch_margin_margn` 最新快照 `{date, margin_balance, short_balance, short_margin_ratio, available}`；無資料 `available:false`（HTTP 200）。前端法人卡片旁加「融資融券」卡片，`renderMarginTrading()`；`available:false` 顯示「TWSE OpenAPI 無此股融資券資料（上櫃股暫不支援）」。
- [ ] 375px 不破版；cache-buster +1
- **Verification:** `pytest -k margin_trading`；瀏覽器 2330 + 一檔上櫃股 · **Files:** `api.py`, `static/*`, `tests/test_api.py` · **Scope:** M · **Deps:** 4.1

#### Task 4.4 — R1 應收帳款異常膨脹
`fetch_inventory_and_revenue_growth()`（`datasource_finmind.py:534`）用既有 `_get_last_quarter_val` 多取應收序列（type 候選 `AccountsReceivableNet`, `AccountsReceivable`, `NotesAndAccountsReceivableNet`），回傳加 `ar_yoy`（可 None）。`api.py:3017` 存貨雷之後加：`ar_yoy > 20 and ar_yoy − rev_yoy > 20` → `{"category":"財務造假","name":"應收帳款異常膨脹",…}`。不增加 FinMind 呼叫。快取鍵升 `api_buy_score_v6`。
- [ ] 無應收欄位時不觸發不報錯；應收 60% / 營收 10% fixture 觸發
- **Verification:** `pytest tests/test_buy_score.py -k receivable` · **Files:** `datasource_finmind.py`, `api.py`, `tests/test_buy_score.py` · **Scope:** S

#### Task 4.5 — R2 營業現金流與獲利背離
用既有季淨利 `ni_q`（`api.py:2451`）與季 OCF（重用 `fetch_quarterly_fcf_data` `datasource_finmind.py:875` 的去累計邏輯，抽成 helper）。最近 2 季皆 `NI > 0` 且 `OCF/NI < 0.7` → `{"category":"財務造假","name":"營業現金流與獲利背離",…}`。< 2 季資料加入 `warnings`。前端 `catOrder`（`app.js:2461`）已含「財務造假」。
- [ ] 連續 2 季 0.5 觸發；僅 1 季不觸發；NI ≤ 0 季跳過；不增加 FinMind 呼叫
- **Verification:** `pytest tests/test_buy_score.py -k ocf` · **Files:** `api.py`, `datasource_finmind.py`, `tests/test_buy_score.py` · **Scope:** M · **Deps:** 4.4

#### Task 4.6 — `query_history.py`
`record(stock_id)` 原子寫入 `CACHE_DIR/query_history.json`（tmp + `os.replace`），裁掉 7 天前紀錄；`top_recent(limit=40, days=7)` 依次數降序、同次數依最近時間。`/latest` 成功前呼叫 `record(sid)`。檔案損毀視為空。
- **Verification:** `pytest tests/test_query_history.py` · **Files:** `query_history.py`, `api.py`, `tests/test_query_history.py` · **Scope:** S

#### Task 4.7 — `scheduler.py` 隔夜預算
`apscheduler>=3.10` 加入 `requirements.txt` 與 `embedded_deployment/requirements.txt`（不加 Android）。`BackgroundScheduler` 每日 **02:00**（`SCHED_HOUR = 2`）執行 `precompute_recent()`：`top_recent(40)` 逐檔進程內呼叫 `api.buy_score(sid, token=os.environ["FINMIND_API_KEY"])`，每檔間隔 5 秒，單檔例外記 log 續跑，結尾 log 成功/失敗數。`api.py` startup：`FINMIND_API_KEY` 有值才 `scheduler.start()`，否則 log `scheduler disabled`。CLI `python scheduler.py --now`。
- [ ] 無 `FINMIND_API_KEY` 時 app 正常啟動、既有測試不受影響；`--now` 後清單股票當天 `buy_score` 快取存在
- **Verification:** `pytest tests/test_scheduler.py`（monkeypatch `api.buy_score`、`top_recent`、`time.sleep`）· **Files:** `scheduler.py`, `api.py`, `requirements.txt`, `embedded_deployment/requirements.txt`, `tests/test_scheduler.py` · **Scope:** M · **Deps:** 4.6

#### Task 4.8 — `cache.py` TTL 清理 + tmpfs flush
`CacheStore.purge_expired(ttl_seconds)`（依 mtime）、`flush_to(dir)` / `restore_from(dir)`。環境變數 `MICROECO_CACHE_TTL_DAYS`（預設 7）、`MICROECO_CACHE_PERSIST_DIR`（預設空 = 不 flush）。startup 時 `restore_from`；排程批次尾端 `purge_expired` → `flush_to`。`README.md:135` 環境變數表加兩項 + tmpfs 範例（`MICROECO_CACHE_DIR=/dev/shm/stock-cache`）。
- **Verification:** `pytest tests/test_cache.py`（`os.utime` 製造舊檔）· **Files:** `cache.py`, `scheduler.py`, `api.py`, `README.md`, `tests/test_cache.py` · **Scope:** S · **Deps:** 4.7

#### Task 4.9 — `datasource_mops_news.py`（R10/R11 資料源）
抓 MOPS 個股重大訊息（`mops.twse.com.tw/mops/web/ajax_t05st01`，實作前以 DevTools 確認 POST 參數），近 12 個月標題 `list[{date,title}]`。`detect_auditor_change(titles)`（更換/變更簽證/解任/委任…會計師）、`detect_filing_delay(titles)`（延後公告財務報告/無法於期限內）。防擋沿用 Goodinfo 樣板，封鎖偵測重用 `datasource_mops.is_blocked_html`。失敗拋 `MopsNewsError`（不回空 list）。結果快取 7 天。
- **Verification:** `pytest tests/test_mops_news.py`（三組 HTML fixture）· **Files:** `datasource_mops_news.py`, `tests/test_mops_news.py` · **Scope:** M

#### Task 4.10 — R10/R11 接入 `buy_score` + UI
成功則依兩個 detect 各加一條 `{"category":"治理","name":"近一年更換會計師" / "財報延遲申報","value_label":公告日期,"description":標題}`；`MopsNewsError` → `warnings.append("mops_news: …")`，不加雷項。快取鍵升 `api_buy_score_v7`；`catOrder` 於「籌碼治理」後插入「治理」；cache-buster +1。
- **Verification:** `pytest tests/test_buy_score.py -k mops_news`；瀏覽器 2330 應無治理雷 · **Files:** `api.py`, `static/app.js`, `static/index.html`, `tests/test_buy_score.py` · **Scope:** S · **Deps:** 4.9

#### Checkpoint D（Final）
- [ ] `pytest -q` 全通過
- [ ] 2330 全流程：快速分析頁 → TWSE 來源 → 無誤報雷 → 隔日 `buy_score` 命中快取
- [ ] `android_sync.cmd` 清單含 `datasource_twse_open.py`, `query_history.py`, `datasource_mops_news.py`；三份複本 `diff` 一致
- [ ] **最終人工驗收**

---

## 並行策略

```
0.1 ──→ 3.1 ──────────────────────┐
1.1 → 1.2 → 2.1 → 2.2 → 2.3 → 3.2 → 3.3 → 3.4   ← 主線
4.1 → 4.2 → 4.3                    │ 可與主線並行（不碰 static/app.js 的 quick 區段）
4.4 → 4.5                          │ 可並行
4.6 → 4.7 → 4.8                    │ 可並行
4.9 → 4.10                         │ 可並行
```
`api.py` 為多任務共同檔案：並行時各開分支，合併前 rebase；新端點一律加在檔尾。

## Risks and Mitigations

| 風險 | 影響 | 緩解 |
|------|------|------|
| 估值模型彼此分歧（floor ≥ ceiling） | 階梯圖無意義 | `position = null` 顯示「模型分歧」，列出 components 讓使用者自行判讀 |
| 虧損股 / 無配息股多數候選為 null | 快速頁空白 | `insufficient` verdict 明示；component 清單顯示哪些缺 |
| 判定表前後端各一份 | 不同步 | 後端為權威；前端常數註明來源，`test_quick_analysis` 的七案例同時產生 JSON fixture 供前端對照 |
| dataset 層快取讓當日資料滯後 | 盤中價格不更新 | key 含 `asof=today`；`TaiwanStockPrice` 可設較短 TTL（1 小時） |
| 延遲繪圖造成切分頁時卡頓 | 體驗倒退 | 每幀最多 3 張，`requestAnimationFrame` 分批 |
| TWSE OpenAPI 無上櫃股 | 省不到 quota | FinMind fallback；TPEx OpenAPI 列後續 |
| MOPS 反爬 | R10/R11 失效 | 失敗進 `warnings` 標 N/A，不影響評分 |
| 排程需 `FINMIND_API_KEY` | Desktop 使用者未設則不跑 | 啟動 log 明示；README 說明；前端 token 不落盤 |

## Open Questions（已決議）

1. 階梯圖的模型組合維持固定，不開放勾選排除，收集回饋後再加。
2. `TaiwanStockPrice` 的 dataset 快取 TTL 為 1 小時。
3. 排程維持 02:00 / 40 檔，`/api/health` 的 `scheduler` 欄位顯示上次批次時間與成功數。

## Verification（全計畫）

1. `pytest -q` 全綠（既有 10 + 新增約 30）。
2. `run.cmd` 啟動，查 2330：首屏停在快速分析頁，階梯圖 ≤ 1.5s 可見，buy_score 到達後外框變色、象限圖出現；component 可點跳轉。
3. `docs/perf-baseline.md`：優化後冷查詢 FinMind 呼叫數 ↓ ≥ 50%、首屏時間 ↓ ≥ 40%。
4. 清 `.cache/` 後查 2330 兩次：第二次所有端點 `X-Elapsed-Ms` < 300。
5. `FINMIND_API_KEY` 設定後 `python scheduler.py --now`，重查 `buy_score` 命中快取。
6. 三份複本（root / `embedded_deployment/` / `android_app/`）同步一致，Android 實機走一次查詢。

---

## 實作紀錄（2026-11-01）

全部任務已實作。以下是實測後與本計畫不同之處，以此節為準。

**資料來源**
- T86 不在 TWSE OpenAPI 上（`fund/T86` 回 302），OpenAPI 的 `MI_MARGN` 沒有資料日期，兩者都改走 `www.twse.com.tw/rwd`（同樣全市場、免 token）。`STOCK_DAY_ALL` 維持 OpenAPI。
- `mops.twse.com.tw/mops/web/ajax_t05st01` 已被 WAF 擋下，改用 JSON API `mops/api/t05st01`。`month: "all"` 一次回傳整年，查近 12 個月最多 2 次請求。`code 406` 代表該期間沒有公告，不是錯誤。

**效能**
- Task 3.1 比計畫多三件事，否則達不到目標：(1) single-flight，因為冷查詢的請求是同時發出，單純快取會全部 miss；(2) 首次抓取放寬到 6 年，較短區間直接切片，因為各端點要的起訖日不同；(3) 快取鍵不含日期，改記 `asof`，一個 dataset 一個檔，磁碟用量隨股票數而非天數成長。
- 當日會更新的 dataset（股價、PER、法人）一小時過期。
- FinMind 拒絕網站 session JWT（`Token is illegal`），程式會改走匿名。現在會記住這類 token，省掉每次先失敗一輪。匿名配額由同一對外 IP 共用。
- Task 3.3 的第二批請求改為等 `quick_summary` 完成（最多 8 秒），不是第一批任一回應：這時快取已熱，deep-read 端點幾乎都能命中。
- 延遲繪圖不改呼叫點，而是在開機時包裝 19 個 `plot*` / `render*` 函式，同一張圖重複呼叫會合併成一次。
- Goodinfo 回 403 時不再重試，所有 client 冷卻 10 分鐘。403 是封鎖，舊版每次白等約 15 秒，是 `buy_score` 慢的主因。
- `/latest` 的股價快取加上 1 小時 TTL，否則早上查過，當天收盤價就看不到。
- Plotly 與 `app.js` 改為 `defer`，避免 3.5 MB 的 CDN 腳本擋住首次繪製。

**排雷**
- R2 計畫說重用 `fetch_quarterly_fcf_data` 的去累計邏輯，但該函式回傳的就是累計值。去累計是新寫的（`series_builder.decumulate_ytd`），跨缺季時捨棄該季。
- 金融保險業不適用 R1、R2 與既有的「自由現金流轉負」：放款與保單流量主導應收與現金流，只會產生雜訊。
- `pe_river` 的 `eps_ttm` 是既有 bug：把以元計的股本當成千元，2330 算出 0.01（實際 86.28）。前端沒用過這個欄位，直到快速分析才暴露。改為加總財報揭露的季 EPS，不再假設面額 10 元。mock 測試抓不到這類問題，新增欄位時要用真實資料驗收。
- R1 依使用者決定改為連續 2 季才觸發，比 Task 4.4 嚴格。單季多半是出貨時點或低基期：2330 本季應收 +86.7%／營收 +36.0% 達標，但上季只領先 12.9 個百分點，不觸發；2409 連兩季都達標，觸發。
- `buy_score` 的快取鍵一次升到 v7，cache-buster 一次升到 v33：中間版本沒有上線過。

**排程**
- 容器時區是 UTC，`date.today()` 要到台北 08:00 才換日，02:00 的預算會寫進「昨天」的鍵。`api.py` 現在把時區設為 `CST-8`（POSIX 字串，不需時區資料庫）。
- 排程在沒有 `FINMIND_API_KEY` 時仍會啟動，只跳過預算、照常清快取，否則沒設 key 的環境快取永遠不會清。批次遇到配額耗盡會提前停止。
- 正式機的 `FINMIND_API_KEY` 放在主機上的環境檔（權限 600），不進映像，`run_docker_service.sh` 也會讀取。
- `/latest` 現在也被 `/quick_summary` 呼叫，所以 `query_history.record` 對同一檔 60 秒內只記一次。

**部署**
- 正式機是共用主機（根目錄 92%，可用記憶體約 200 MB），且本機與正式機映像沒有共用 layer。完整流程會多寫約 680 MB，所以新增 `embedded_deployment/Dockerfile.update`，在現有映像上增量更新。流程見 `remote_deploy.md`。
- 正式機是 pandas 3.0.3，本機是 2.3.3。測試也要在正式機映像裡跑。
- 文件原本的健康檢查路徑 `/health` 是 404，已改為 `/api/health`。

**測試基準**：計畫寫的「既有 10 個測試」是算錯的，實際是 33 個。
