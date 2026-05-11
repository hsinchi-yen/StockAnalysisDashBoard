@echo off
setlocal

set "ROOT=%~dp0"
set "ANDROID_DIR=%ROOT%android_app"
set "LOCAL_PROPERTIES=%ANDROID_DIR%\local.properties"
set "EXAMPLE_PROPERTIES=%ANDROID_DIR%\local.properties.example"
set "PYTHON_DST=%ANDROID_DIR%\app\src\main\python"
set "STATIC_DST=%ANDROID_DIR%\app\src\main\assets\static"

if not exist "%ANDROID_DIR%" (
  echo android_app directory was not found.
  exit /b 1
)

echo Syncing Python source files to android_app...
for %%f in (api.py cache.py datasource_finmind.py datasource_goodinfo.py datasource_mops.py series_builder.py charts.py android_launcher.py) do (
  if exist "%ROOT%%%f" (
    copy /Y "%ROOT%%%f" "%PYTHON_DST%\%%f" >nul
    echo   Copied %%f
  )
)

echo Syncing static assets to android_app...
for %%f in (app.js index.html styles.css) do (
  if exist "%ROOT%static\%%f" (
    copy /Y "%ROOT%static\%%f" "%STATIC_DST%\%%f" >nul
    echo   Copied static\%%f
  )
)

if exist "%LOCAL_PROPERTIES%" (
  echo local.properties already exists.
) else (
  if defined ANDROID_SDK_ROOT (
    > "%LOCAL_PROPERTIES%" echo sdk.dir=%ANDROID_SDK_ROOT:\=\\%
    echo Created local.properties from ANDROID_SDK_ROOT.
  ) else if defined ANDROID_HOME (
    > "%LOCAL_PROPERTIES%" echo sdk.dir=%ANDROID_HOME:\=\\%
    echo Created local.properties from ANDROID_HOME.
  ) else (
    copy /Y "%EXAMPLE_PROPERTIES%" "%LOCAL_PROPERTIES%" >nul
    echo Created local.properties from example. Please edit the SDK path before building.
  )
)

echo.
echo Next:
echo 1. Open android_app in Android Studio
echo 2. Verify local.properties SDK path
echo 3. Run Gradle Sync
echo 4. Build debug APK

endlocal
