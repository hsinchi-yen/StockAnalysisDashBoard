# Task List — 快速分析頁、效能優化與待辦收尾

> 詳細說明見 [plan.md](plan.md)（= `enhancement_plan.md` v2.0）
> 更新日期: 2026-11-01
> 前版清單: [todo-2026-04-25.md](todo-2026-04-25.md)

---

## Phase 0: 效能基準

- [x] **Task 0.1** — 端點耗時 middleware（`X-Elapsed-Ms`）+ FinMind 呼叫計數 + `docs/perf-baseline.md` 冷/熱基準

---

## Phase 1: 快速分析後端

- [x] **Task 1.1** — `quick_analysis.py` 純函式：`compute_summary()`（floor/ceiling/position）+ `apply_risk()`（verdict）+ 七情境測試
- [x] **Task 1.2** — `/api/stocks/{id}/quick_summary` 端點：並行呼叫 latest/dcf/valuation_extra/forward_rolling_eps/pe_river ← 依賴 1.1

### ✅ Checkpoint A
- [x] `pytest -q` 全通過
- [x] 2330 / 2891 / 一檔虧損股 `quick_summary` 數值合理
- [ ] **人工確認 → Phase 2**

---

## Phase 2: 快速分析前端

- [x] **Task 2.1** — 新增「快速分析」分頁（第一個）、查詢後預設切入、燈號卡搬入
- [x] **Task 2.2** — 價格階梯圖（HTML/CSS）：floor ← 現價 → ceiling、下檔/上檔 %、components 可點跳轉、風險到達後變色 ← 依賴 1.2, 2.1
- [x] **Task 2.3** — 風險/上檔象限圖（Plotly）+ `rerenderDashboard` 整合 ← 依賴 2.2

### ✅ Checkpoint B
- [x] `pytest -q` 全通過；cache-buster `?v=32 → 33`
- [x] 查詢 → 快速分析頁 → 點 component 跳估值分頁 → 返回鍵回快速分析
- [x] 同步 `embedded_deployment/`、`android_app/`（`android_sync.cmd` 加 `quick_analysis.py`）
- [ ] **人工確認 → Phase 3**

---

## Phase 3: 效能優化

- [x] **Task 3.1** — FinMind dataset 層共用 `CacheStore` 快取（空結果不寫；`FINMIND_DS_CACHE=0` 可關）← 依賴 0.1
- [x] **Task 3.2** — 前端分頁延遲繪圖 `scheduleRender()`（非當前分頁入隊，切換時 rAF 分批）← 依賴 2.3
- [x] **Task 3.3** — 先顯示上次結果 + 請求分兩批（quick 優先）+ `AbortController` ← 依賴 2.2
- [x] **Task 3.4** — `GZipMiddleware` + `/static/*` `Cache-Control: immutable`

### ✅ Checkpoint C
- [x] `pytest -q` 全通過
- [x] `docs/perf-baseline.md` 優化後欄：FinMind 呼叫 ↓ 71%；首屏熱查詢 ↓ 75%，冷查詢持平（見文件）
- [ ] Android WebView 實測（程式已同步，尚未重建 APK）
- [x] 同步 `embedded_deployment/`、`android_app/`
- [ ] **人工確認 → Phase 4**

---

## Phase 4: v1.0 待辦收尾

- [x] **Task 4.1** — `datasource_twse_open.py`（STOCK_DAY_ALL / T86 / MI_MARGN，1 小時快取）
- [x] **Task 4.2** — `/latest` TWSE 優先、FinMind fallback、`*_source` 欄位 ← 依賴 4.1
- [x] **Task 4.3** — `/margin_trading` 端點 + 基本資訊「融資融券」卡片 ← 依賴 4.1
- [x] **Task 4.4** — R1 應收帳款異常膨脹（連續 2 季 `ar_yoy`；buy_score 快取 v8）
- [x] **Task 4.5** — R2 營業現金流與獲利背離（季 OCF/NI 連 2 季 < 0.7）← 依賴 4.4
- [x] **Task 4.6** — `query_history.py`（7 天滾動、`top_recent`）
- [x] **Task 4.7** — `scheduler.py` APScheduler 02:00、40 檔、需 `FINMIND_API_KEY`、`--now` CLI ← 依賴 4.6
- [x] **Task 4.8** — `cache.py` `purge_expired` / `flush_to` / `restore_from` + README 環境變數 ← 依賴 4.7
- [x] **Task 4.9** — `datasource_mops_news.py`（重訊關鍵字偵測，7 天快取，失敗拋 `MopsNewsError`）
- [x] **Task 4.10** — R10/R11 接入 buy_score + `catOrder` 加「治理」← 依賴 4.9

### ✅ Checkpoint D (Final)
- [x] `pytest -q` 全通過
- [x] 2330 全流程：快速分析頁 → TWSE 來源 → 無誤報雷 → 隔日 buy_score 命中快取
- [x] `android_sync.cmd` 清單含三個新資料源模組；三份複本 `diff` 一致
- [ ] **最終人工驗收**

---

## 並行策略

```
0.1 ──→ 3.1
1.1 → 1.2 → 2.1 → 2.2 → 2.3 → 3.2 → 3.3 → 3.4   ← 主線
4.1 → 4.2 → 4.3      ┐
4.4 → 4.5            ├ 可與主線並行（api.py 各開分支，合併前 rebase）
4.6 → 4.7 → 4.8      │
4.9 → 4.10           ┘
```
