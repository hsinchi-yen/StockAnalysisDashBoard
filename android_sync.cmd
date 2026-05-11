@echo off
setlocal

set "ROOT=%~dp0"
set "ANDROID_DIR=%ROOT%android_app"
set "PYTHON_DST=%ANDROID_DIR%\app\src\main\python"
set "STATIC_DST=%ANDROID_DIR%\app\src\main\assets\static"

if not exist "%ANDROID_DIR%" (
  echo ERROR: android_app directory not found.
  exit /b 1
)

echo Syncing Python source files...
for %%f in (api.py cache.py datasource_finmind.py datasource_goodinfo.py datasource_mops.py series_builder.py charts.py android_launcher.py) do (
  if exist "%ROOT%%%f" (
    copy /Y "%ROOT%%%f" "%PYTHON_DST%\%%f" >nul
    echo   [OK] %%f
  )
)

echo Syncing static assets...
for %%f in (index.html app.js styles.css) do (
  if exist "%ROOT%static\%%f" (
    copy /Y "%ROOT%static\%%f" "%STATIC_DST%\%%f" >nul
    echo   [OK] static\%%f
  )
)

echo.
echo Sync complete. Rebuild APK in Android Studio or run build_apk.cmd.
endlocal
