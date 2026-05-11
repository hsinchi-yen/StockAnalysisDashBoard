# Task List — 台股分析 Dashboard 強化

> 詳細說明見 [plan.md](plan.md)  
> 更新日期: 2026-04-25

---

## Phase 1: 後端新財務指標

- [ ] **Task 1.1** — 利潤率三兄弟 API (`/api/margins`) ← 毛利率/營業利益率/淨利率
- [ ] **Task 1.2** — EPS 季度趨勢 + YoY 成長率 API (`/api/eps_trend`) + 月營收 YoY 欄位
- [ ] **Task 1.3** — 流動比率/速動比率/BVPS API (`/api/liquidity`)
- [ ] **Task 1.4** — 外資持股比例趨勢 API (`/api/foreign_holding`)
- [ ] **Task 1.5** — PEG 比率 + Graham Number 端點 (`/api/valuation_extra`) ← 依賴 1.2, 1.3

### ✅ Checkpoint A
- [ ] `pytest -q` 全部通過
- [ ] 5 個新端點手動測試 OK
- [ ] **人工確認 → 進入 Phase 2**

---

## Phase 2: Web UI — 新指標顯示

- [ ] **Task 2.1** — 利潤率三兄弟折線圖 + 表格 ← 依賴 1.1
- [ ] **Task 2.2** — EPS 趨勢圖 + 月營收 YoY 柱狀圖 ← 依賴 1.2
- [ ] **Task 2.3** — 流動/速動比率 + BVPS 圖 ← 依賴 1.3
- [ ] **Task 2.4** — 外資持股比例趨勢圖 ← 依賴 1.4
- [ ] **Task 2.5** — Graham Number + PEG 估值面板 ← 依賴 1.5
- [ ] **Task 2.6** — 投資策略燈號摘要卡 ← 依賴 2.1–2.5

### ✅ Checkpoint B
- [ ] 6 個新功能 PC 瀏覽器正確顯示
- [ ] 1280px / 768px / 375px 視窗不破版
- [ ] **人工確認 → 進入 Phase 3**

---

## Phase 3: Web UI 體驗優化

- [ ] **Task 3.1** — 分頁導航 Tab (5 個分頁) ← 依賴 2.1–2.6
- [ ] **Task 3.2** — 骨架屏 Skeleton Loading ← 依賴 3.1
- [ ] **Task 3.3** — 全局讀取進度條 + 錯誤重試 ← 依賴 3.2

### ✅ Checkpoint C
- [ ] 完整查詢流程 PC 端跑通
- [ ] 斷網 + 重試流程正常
- [ ] **人工確認 → 進入 Phase 4**

---

## Phase 4: Android UI 優化 + 修復

- [ ] **Task 4.1** — 響應式 CSS 行動版 + 底部導航列 ← 依賴 3.1
- [ ] **Task 4.2** — Plotly 圖表觸控 + resize 優化 ← 依賴 4.1
- [ ] **Task 4.3** — Android API 超時修復 + `android_sync.cmd` 腳本 ← 依賴 3.3, 4.2

### ✅ Checkpoint D (Final)
- [ ] Android WebView 主要功能正常
- [ ] PC + Android 功能一致
- [ ] `pytest -q` 全部通過
- [ ] **最終人工驗收**

---

## 並行策略（同時可開發）

```
[Task 1.1] ──→ [Task 2.1]
[Task 1.2] ──→ [Task 2.2]  ← 四組可並行
[Task 1.3] ──→ [Task 2.3]
[Task 1.4] ──→ [Task 2.4]
[Task 1.2 + 1.3] ──→ [Task 1.5] ──→ [Task 2.5]
[Task 2.1~2.5] ──→ [Task 2.6]
[Task 3.1] → [Task 3.2 + Task 4.1]  ← 可並行
```
