# Android App

這個資料夾是 Android 封裝專案，採用以下架構：

- WebView 顯示現有前端
- Chaquopy 在 App 內啟動 Python
- Python 內啟動 FastAPI / Uvicorn 本機服務
- Android App 連到 `http://127.0.0.1:8000`

## 需求

- Android Studio
- JDK 17
- Android SDK Platform 36

## 開啟方式

1. 在 repo 根目錄先執行 `android_prepare.cmd`
2. 用 Android Studio 開啟 `android_app/`
3. 等待 Gradle Sync 完成
4. 執行 `app` 組態到模擬器或實機

## Windows 輔助腳本

- `../android_prepare.cmd`：建立或補齊 `local.properties`
- `../android_build_debug.cmd`：在有 Gradle wrapper 或系統 Gradle 時嘗試 build debug APK

## 目前設計

- Python 原始碼由 Gradle 從專案根目錄同步到 build 目錄後再打包
- 前端靜態檔由 Gradle 從 `../static/` 同步到 Android assets，再由 App 啟動時複製到可讀寫目錄
- FastAPI 透過環境變數 `MICROECO_STATIC_DIR` 和 `MICROECO_CACHE_DIR` 指向 Android 執行時路徑

## 注意

- `requirements-android.txt` 故意不帶 `streamlit`、`pytest` 與 `uvicorn[standard]`
- Goodinfo 備援在 Android 上可能因 `lxml` 不可用而停用；目前以 FinMind 主流程優先
