@echo off
setlocal EnableExtensions EnableDelayedExpansion

title Build Android APK - One Click

set "ROOT=%~dp0"
set "ANDROID_DIR=%ROOT%android_app"
set "APK_REL=app\build\outputs\apk\debug\app-debug.apk"
set "APK_ABS=%ANDROID_DIR%\%APK_REL%"

echo ============================================================
echo   StockAnalysisDashBoard - Android APK One-Click Builder
echo ============================================================
echo.

if not exist "%ANDROID_DIR%" (
  echo [ERROR] android_app folder not found.
  echo         Expected: "%ANDROID_DIR%"
  echo.
  pause
  exit /b 1
)

echo [1/3] Syncing Python source and static assets...
call "%ROOT%android_prepare.cmd"
if errorlevel 1 (
  echo.
  echo [ERROR] android_prepare.cmd failed.
  echo.
  pause
  exit /b 1
)

echo.
echo [2/3] Building debug APK...
call "%ROOT%android_build_debug.cmd"
if errorlevel 1 (
  echo.
  echo [ERROR] APK build failed.
  echo.
  pause
  exit /b 1
)

echo.
echo [3/3] Verifying output...
if exist "%APK_ABS%" (
  echo [OK] Build successful.
  echo.
  echo APK:
  echo   "%APK_ABS%"
  echo.
  echo Opening APK folder...
  start "" "%ANDROID_DIR%\app\build\outputs\apk\debug"
) else (
  echo [WARN] Build command succeeded, but APK not found at expected path:
  echo   "%APK_ABS%"
)

echo.
echo Done.
pause
endlocal
