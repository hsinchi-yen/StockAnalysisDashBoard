# Enhancement Plan: 台股分析 Dashboard — 選股強化與排雷深化

**版本:** 1.0
**日期:** 2026-05-22
**平台:** PC only（Web / FastAPI Host）
**前置文件:** `tasks/plan.md` v1.0、`DCF_GUIDE.md`

---

## Overview

在現有 FastAPI + Plotly + JSON 快取架構上，分四條主軸強化「選股精準度」與「排雷能力」，同時嚴格控制 FinMind API 用量並降低爬蟲被擋風險：

1. **資料源分層重構** — 把當日類指標從 FinMind 搬到 TWSE/TPEx OpenAPI（無 token、無 quota），FinMind 只保留歷史時序與財報
2. **排雷指標深化** — 從現有 5 項擴充到三大類雷（財務造假、財務惡化、籌碼治理），含趨勢惡化偵測
3. **產業感知評分** — 24 指標維持一刀切，但標出產業別，並讓不適用指標自動排除（如金融股負債比）
4. **隔夜排程預算** — APScheduler 每晚批次預算近期查過的股票，嵌入式 eMMC 友善快取設計

**核心約束:** 不引入完整資料庫；沿用 `cache.py` JSON 快取；不破壞現有測試；新端點追加於 `api.py` 檔尾。

---

## Architecture — 依賴圖

```
┌─────────────── 資料源分層 ───────────────┐
│                                          │
│  Layer 1: TWSE/TPEx OpenAPI（無 token）   │ ← 當日快照：股價、法人、融資券
│  Layer 2: FinMind API（600 次/日）        │ ← 歷史時序、財報、EPS、PER
│  Layer 3: Goodinfo（30 天長效快取）       │ ← 補漏：董監質押、外資月度
│  Layer 4: MOPS 重訊（第二階段爬蟲）       │ ← 會計師變更、財報延遲
│                                          │
└──────────────────┬───────────────────────┘
                    │
        datasource_*.py（新增 datasource_twse_open.py）
                    │
              api.py（FastAPI）← 新增端點 + 排雷邏輯擴充
                    │
        ┌───────────┴───────────┐
        │                       │
  scheduler.py（APScheduler）  static/（UI）
        │
  query_history.json（7 天滾動）→ 隔夜批次輸入清單
        │
  cache（tmpfs + 定時 flush，TTL 7 天）
```

**實作順序:** 資料源遷移先 → 排雷指標 → 產業評分 → 排程；後端先、UI 後。

---

## Architecture Decisions

- **當日 / 歷史分流（Q-A）** — TWSE OpenAPI 為全市場當日快照，僅承接「當日類」指標；個股歷史時序仍走 FinMind。不強行把歷史搬到 TWSE `rwd` 端點，避免逐股請求反而被當爬蟲。
- **排雷分階段（Q-B）** — 先做「財報 / 公開資料算得出來」的雷（FinMind + TWSE 可覆蓋約 8 項）；需爬 MOPS 重訊的雷（會計師變更、財報延遲）列為 Phase 4 第二階段。
- **趨勢惡化融入排雷（Q-D）** — 不另開區塊，連續 N 季惡化直接作為一條 `risk_criteria`，維持單一排雷面板。
- **產業一刀切 + 標註（Q-C）** — 評分門檻不依產業調整；新增 `industry` 欄位顯示；不適用指標標 `pass: null` + `not_applicable: true`，不計入 `eligible_count`。
- **eMMC 寫入優化（Q4）** — 快取目錄掛 `tmpfs`（RAM），APScheduler 定時 flush 落盤；TTL 7 天自動清理；合併寫入避免頻繁小檔，降低 NAND 磨損。
- **不破壞零 DB 原則** — `query_history.json` 與快取均為扁平 JSON，無 SQLite/Postgres。

---

## 資料源遷移對照表

| 指標 | 現行來源 | 新來源 | 動機 |
|------|---------|--------|------|
| 最新股價 | FinMind TaiwanStockPrice | TWSE OpenAPI `STOCK_DAY_ALL` | 省 quota，官方當日快照 |
| 法人買賣超 | FinMind InstitutionalInvestors | TWSE OpenAPI `T86` | 省 quota |
| 融資融券餘額（新） | — | TWSE OpenAPI `MI_MARGN` | 新增籌碼面 |
| 月營收 / EPS / 財報 | FinMind | FinMind（不變） | 歷史時序，OpenAPI 無 |
| PER / PBR / 殖利率 | FinMind TaiwanStockPER | FinMind（不變） | 歷史時序 |
| 董監持股 / 質押 | Goodinfo + MOPS | Goodinfo（加 30 天快取）| OpenAPI 無質押明細 |

**預期效果:** 單次 `runQuery()` 對 FinMind 的呼叫從 ~19 降至 ~13，省下約 30% 配額。

---

## 新增排雷指標總覽

| # | 雷別 | 指標 | 資料來源 | 判定邏輯 | 階段 |
|---|------|------|---------|---------|------|
| R1 | 財務造假 | 應收/存貨成長 >> 營收成長 | FinMind BS+IS | 已部分有，補應收 | P2 |
| R2 | 財務造假 | 營收創高但 OCF 背離 | FinMind | OCF/NI 連續 2 季 < 0.7 | P2 |
| R3 | 財務造假 | Sloan Ratio 極端 | FinMind（已有）| 強化門檻 | P2 |
| R4 | 財務惡化 | 連續虧損 | FinMind IS | 近 N 季 EPS < 0 | P2 |
| R5 | 財務惡化 | 淨值低於票面 10 元 | FinMind BS | BVPS < 10 | P2 |
| R6 | 財務惡化 | 利息保障倍數不足 | FinMind IS | 營業利益/利息費用 < 2 | P2 |
| R7 | 籌碼治理 | 董監質押比過高 | Goodinfo | 質押/持股 > 50% | P2 |
| R8 | 籌碼治理 | 趨勢惡化（毛利/ROE/負債）| FinMind | 連續 3 季單向惡化 | P2 |
| R9 | 配息陷阱 | 配發率 > 100% | FinMind（已有）| 維持 | P2 |
| R10 | 治理 | 更換會計師 | MOPS 重訊爬蟲 | 重訊偵測 | P4 第二階段 |
| R11 | 治理 | 財報延遲申報 | MOPS 重訊爬蟲 | 重訊偵測 | P4 第二階段 |

---

## Task List

### Phase 1: 資料源分層重構（Foundation）

#### Task 1.1 — TWSE OpenAPI 資料源模組
**描述:** 新增 `datasource_twse_open.py`，封裝 TWSE OpenAPI（`openapi.twse.com.tw/v1/`）。實作 `fetch_stock_day_all()`、`fetch_institutional_t86()`、`fetch_margin_margn()`。OpenAPI 為全市場 JSON 快照，模組內依 `stock_id` 過濾單檔。

**Acceptance criteria:**
- [ ] 三個方法各回傳標準化 DataFrame，欄位命名與既有 FinMind 對齊
- [ ] OpenAPI 無資料或網路失敗時回傳空 DataFrame，不拋例外
- [ ] 內建 1 小時快取（當日快照變動慢）
- [ ] 無需 token；請求帶合理 User-Agent

**Verification:** `pytest -k twse_open` 通過（Mock）；`curl` 驗證 2330 當日股價與公開資料吻合
**Files:** `datasource_twse_open.py`, `tests/test_twse_open.py`
**Scope:** M

#### Task 1.2 — 現有端點切換至 TWSE 來源
**描述:** 將 `/api/stocks/{id}/latest` 的股價與法人改用 `datasource_twse_open`；FinMind 保留為 fallback（TWSE 失敗時）。新增 `/api/stocks/{id}/margin_trading` 顯示融資融券。

**Acceptance criteria:**
- [ ] `/latest` 優先 TWSE，失敗自動 fallback FinMind，行為對前端透明
- [ ] `/margin_trading` 回傳近期融資餘額、融券餘額、券資比
- [ ] 既有 `test_api.py` 不需大改即通過（fallback 路徑可被 mock）

**Verification:** `pytest -q` 全通過；斷開 TWSE 驗證 fallback
**Files:** `api.py`, `tests/test_api.py`
**Scope:** M

#### Task 1.3 — Goodinfo 30 天長效快取 + 防擋強化
**描述:** `datasource_goodinfo.py` 加入：(1) 月更指標 30 天 TTL 快取；(2) User-Agent 池輪替；(3) 失敗指數退避（1s→2s→4s）；(4) throttle 間隔加大並隨機化。

**Acceptance criteria:**
- [ ] 同股票 30 天內第二次呼叫不發 HTTP 請求
- [ ] 連續失敗時退避時間遞增，最多重試 3 次
- [ ] User-Agent 每次請求從池中隨機選取

**Verification:** 單元測試模擬連續失敗驗證退避；快取命中測試
**Files:** `datasource_goodinfo.py`, `tests/test_goodinfo.py`
**Scope:** S

#### Checkpoint A
- [ ] `pytest -q` 全通過
- [ ] FinMind 單次查詢呼叫數實測下降
- [ ] **人工確認 → Phase 2**

---

### Phase 2: 排雷指標深化

#### Task 2.1 — 財務造假類排雷（R1–R3）
**描述:** 擴充 `buy_score` 的 `risk_criteria`：補強應收帳款成長偵測（R1）、營收創高 vs OCF 背離（R2）、強化 Sloan 極端門檻（R3）。資料沿用 `fetch_inventory_and_revenue_growth` 並新增應收欄位。

**Acceptance criteria:**
- [ ] R1 偵測應收 YoY 與存貨 YoY 同時 >> 營收 YoY
- [ ] R2：OCF/NI 連續 2 季 < 0.7 時觸發
- [ ] 每條雷含 `category`, `name`, `status`, `value_label`, `description`

**Verification:** `pytest -k risk` 以造假型 fixture 驗證觸發
**Files:** `api.py`, `datasource_finmind.py`, `tests/test_buy_score.py`
**Scope:** M

#### Task 2.2 — 財務惡化類排雷（R4–R6）
**描述:** 新增連續虧損（R4）、淨值低於票面（R5）、利息保障倍數不足（R6）。R6 需從 IS 取營業利益與利息費用。

**Acceptance criteria:**
- [ ] R4：近 4 季 EPS 任一為負或連 2 季為負時觸發
- [ ] R5：最新季 BVPS < 10 時觸發
- [ ] R6：營業利益 / 利息費用 < 2 時觸發；利息費用缺失則該雷標 N/A

**Verification:** `pytest -k risk` 以惡化型 fixture 驗證
**Files:** `api.py`, `datasource_finmind.py`, `tests/test_buy_score.py`
**Scope:** M

#### Task 2.3 — 籌碼治理排雷 + 趨勢惡化偵測（R7–R8）
**描述:** 新增董監質押比過高（R7，用 Goodinfo `total_dir_pledged`/`total_dir_shares`）。新增趨勢惡化偵測（R8）：對毛利率、ROE、負債比做斜率判斷，連續 3 季單向惡化即列一條雷。

**Acceptance criteria:**
- [ ] R7：質押比 > 50% 觸發
- [ ] R8：以線性斜率或連續差分判定，連 3 季惡化觸發
- [ ] R8 為純算法，不新增 API 呼叫

**Verification:** `pytest -k risk` 以遞減序列 fixture 驗證 R8
**Files:** `api.py`, `series_builder.py`, `tests/test_buy_score.py`
**Scope:** M

#### Task 2.4 — 排雷面板 UI 強化
**描述:** `static/app.js` 的 `renderBuyScore` 風險區依 `category` 分組顯示（財務造假 / 財務惡化 / 籌碼治理 / 配息）。每組顯示觸發數，沿用「2 項以上下調評等」邏輯。

**Acceptance criteria:**
- [ ] 風險項目按類別分組，組標題清楚
- [ ] 趨勢惡化雷顯示惡化的季數與指標名
- [ ] 無風險時整區隱藏（維持現狀）

**Verification:** 瀏覽器查詢已知地雷股，分組正確顯示
**Files:** `static/app.js`, `static/index.html`, `static/styles.css`
**Scope:** S

#### Task 2.5 — 修正多維估值比較的 PEG / EPS CAGR 計算錯誤
**描述:** 核對現有 `valuation_extra` 端點後發現 PEG Ratio 計算有兩處缺陷需修正。Graham Number 與 Graham MOS 計算正確（MOS 負值代表現價高於保守估值，屬正常設計，不需修正），問題僅在 PEG。

**問題與修正:**

- **Bug A — CAGR 期數與文件不一致:** `valuation_extra` 取 `full_years.iloc[-3]` 到 `iloc[-1]`（2 年跨距）卻搭配 `pow(ratio, 1/2)`，實際算的是 2 年 CAGR；但 `DCF_GUIDE.md` 第十三節明定為「EPS 3 年 CAGR」、公式 `^(1/3)`。需統一為 3 年：取 `iloc[-4]` 到 `iloc[-1]`，指數改 `1/3`，或將年期參數化。
- **Bug B — 負值 / 負 EPS 未防護:** 目前 `peg` 僅在 `eps_cagr > 0` 時計算，但 (1) 負的 `eps_cagr` 本身仍被回傳，前端 detail 行會顯示「EPS CAGR -8.5%」之類負值；(2) 若起始年 EPS 為負（`oldest < 0`），對負數開根號會產生**複數**，`eps_cagr > 0` 判斷對複數失效，`round()` 可能拋例外。需在計算前檢查 `oldest > 0 and newest > 0`，任一為負直接令 `eps_cagr = None`。

**Acceptance criteria:**
- [ ] EPS CAGR 採 3 年期，與 `DCF_GUIDE.md` 公式一致（或年期參數化並同步更新文件）
- [ ] `oldest` 或 `newest` 任一 ≤ 0 時 `eps_cagr` 與 `peg` 皆回傳 `null`，不產生複數、不拋例外
- [ ] 前端 `renderValuationExtra`：`eps_cagr` 為 null 或負值時，detail 行顯示「EPS 衰退，PEG 無參考意義」而非負數字
- [ ] Graham Number / Graham MOS 邏輯不動（MOS 負值維持，代表高估）

**Verification:** `pytest -k valuation_extra` 新增三案例：EPS 衰退股、起始年虧損股、正常成長股；對標 Yahoo Finance / 財報狗 PEG 數值
**Files:** `datasource_finmind.py`（`valuation_extra` EPS CAGR 段）, `api.py`, `static/app.js`, `tests/test_api.py`
**Scope:** S

#### Checkpoint B
- [ ] 8 項可算排雷指標全部運作
- [ ] PEG / EPS CAGR 修正完成，無負值誤顯示、無複數例外
- [ ] `pytest -q` 全通過
- [ ] **人工確認 → Phase 3**

---

### Phase 3: 產業感知評分

#### Task 3.1 — 產業分類資料
**描述:** 從 FinMind `TaiwanStockInfo`（含 `industry_category`）或 TWSE 取得產業別，加入 `/latest` 回傳並快取。建立「指標 ↔ 不適用產業」對照表（金融保險→負債比/流動比；營建→存貨天數等）。

**Acceptance criteria:**
- [ ] 回傳含 `industry` 欄位
- [ ] 對照表以常數定義於 `api.py`，易於擴充

**Verification:** `pytest -k industry`；驗證 2330 為「半導體」、2891 為「金融保險」
**Files:** `api.py`, `datasource_finmind.py`, `tests/test_api.py`
**Scope:** S

#### Task 3.2 — 評分套用產業排除
**描述:** `buy_score` 依產業將不適用指標標 `pass: null` + `not_applicable: true`，排除於 `eligible_count`，`pass_rate` 不受影響。分數門檻維持一刀切。

**Acceptance criteria:**
- [ ] 金融股負債比指標標 `not_applicable`，不計入分母
- [ ] `pass_rate` = 通過數 / 可評分數（排除 not_applicable）
- [ ] 一般股票行為不變（回歸測試）

**Verification:** `pytest -k buy_score` 含金融股 fixture
**Files:** `api.py`, `tests/test_buy_score.py`
**Scope:** M

#### Task 3.3 — UI 顯示產業與排除標示
**描述:** 買入評分 Tab 標頭顯示產業別；`not_applicable` 指標以灰階「產業不適用」標示，與「資料不足」區分。

**Acceptance criteria:**
- [ ] 標頭顯示產業徽章
- [ ] not_applicable 與 unknown 視覺可區分
- [ ] hover 說明為何排除

**Verification:** 瀏覽器查詢金融股，負債比顯示「產業不適用」
**Files:** `static/app.js`, `static/index.html`, `static/styles.css`
**Scope:** S

#### Checkpoint C
- [ ] 金融 / 營建 / 一般股評分行為符合預期
- [ ] **人工確認 → Phase 4**

---

### Phase 4: 隔夜排程 + 第二階段爬蟲

#### Task 4.1 — 查詢紀錄 + APScheduler 批次
**描述:** 新增 `query_history.json`（記代號 + 時間戳，7 天滾動）；每次查詢追加。新增 `scheduler.py` 用 APScheduler 每晚預算清單內股票的 `buy_score` 寫入快取。

**Acceptance criteria:**
- [ ] 每次查詢更新 `query_history.json`，自動裁剪 7 天前紀錄
- [ ] 排程每晚執行，預算結果進快取，隔日查詢直接命中
- [ ] 批次間有 throttle，避免集中打爆 FinMind

**Verification:** 手動觸發排程，確認快取生成；查詢命中無 API 呼叫
**Files:** `scheduler.py`, `api.py`, `tests/test_scheduler.py`
**Scope:** M

#### Task 4.2 — eMMC 友善快取
**描述:** 快取目錄改可指向 `tmpfs`（`MICROECO_CACHE_DIR` 指向 RAM 掛載點）；`cache.py` 新增 TTL 清理（預設 7 天）與啟動 / 排程時的落盤 flush。

**Acceptance criteria:**
- [ ] 過期快取自動清除，僅留 7 天
- [ ] 支援 tmpfs 路徑；flush 將 RAM 快取落盤
- [ ] 合併寫入，減少小檔 I/O

**Verification:** 設短 TTL 驗證清理；驗證 flush 落盤
**Files:** `cache.py`, `scheduler.py`, `README.md`
**Scope:** S

#### Task 4.3 — MOPS 重訊排雷（第二階段爬蟲，R10–R11）
**描述:** 新增 `datasource_mops_news.py` 爬公開資訊觀測站重大訊息，偵測「更換會計師」「財報延遲申報」關鍵字，作為 R10/R11 排雷。沿用 Goodinfo 防擋策略（UA 輪替、退避、長效快取）。

**Acceptance criteria:**
- [ ] 偵測重訊關鍵字並產生對應排雷項
- [ ] 爬取失敗時該雷標 N/A，不影響其他排雷
- [ ] 重訊結果快取 7 天

**Verification:** `pytest -k mops_news` Mock 重訊 HTML 驗證
**Files:** `datasource_mops_news.py`, `api.py`, `tests/test_mops_news.py`
**Scope:** M

#### Checkpoint D（Final）
- [ ] 排程批次穩定運作，快取命中率提升
- [ ] eMMC 寫入量實測下降
- [ ] R10–R11 重訊排雷運作
- [ ] `pytest -q` 全通過
- [ ] **最終人工驗收**

---

## API 用量預算試算

| 情境 | 改善前 | 改善後 |
|------|--------|--------|
| 單次 `runQuery()` FinMind 呼叫 | ~19 | ~13（股價/法人移至 TWSE）|
| 排程命中後查詢 | ~13 | ~0–3（buy_score 已預算）|
| 每日 600 次可支撐查詢數 | ~31 檔 | ~46 檔（未命中）/ 更多（命中）|

---

## Risks and Mitigations

| 風險 | 影響 | 緩解 |
|------|------|------|
| TWSE OpenAPI 格式/ 端點變動 | 當日指標取不到 | 保留 FinMind fallback |
| Goodinfo 質押資料被擋 | R7 失效 | 30 天快取 + 退避；失敗標 N/A |
| MOPS 重訊頁面結構複雜 | R10/R11 解析困難 | 列第二階段；先求關鍵字命中 |
| APScheduler 在嵌入式資源吃緊 | 排程拖慢 host | 批次限流、限預算清單長度 |
| tmpfs 容量有限 | 快取塞爆 RAM | TTL 7 天 + 容量上限清理 |
| 趨勢斜率對少樣本誤判 | R8 假警報 | 要求最少季數，連續單向才觸發 |

---

## Open Questions

1. **排程時間點** — 每晚幾點執行？建議盤後財報更新後（如 02:00），需確認 host 不休眠。
2. **預算清單上限** — `query_history.json` 若累積過多代號，單晚批次可能超過配額；建議設上限（如最多 40 檔），超出依查詢頻率排序取捨。
3. **利息保障倍數欄位** — FinMind 利息費用 type 名稱需實測幾檔確認 fallback 清單。
4. **Yahoo Finance 對標** — 驗證階段用 Yahoo 作第三方交叉比對，是否需寫成自動化測試，或人工抽查即可？
5. **EPS CAGR 年期定案（Task 2.5）** — 修正後採固定 3 年（與 `DCF_GUIDE.md` 一致）或改為可調年期參數？若參數化，需同步更新 `DCF_GUIDE.md` 第十三節公式說明。
