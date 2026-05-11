@echo off
setlocal

set "ROOT=%~dp0"
set "ANDROID_DIR=%ROOT%android_app"

if not exist "%ANDROID_DIR%" (
  echo android_app directory was not found.
  exit /b 1
)

pushd "%ANDROID_DIR%"

if not exist "local.properties" (
  echo local.properties is missing. Run android_prepare.cmd first.
  popd
  exit /b 1
)

if exist "gradlew.bat" (
  call gradlew.bat assembleDebug
  set "RESULT=%ERRORLEVEL%"
  popd
  exit /b %RESULT%
)

where gradle >nul 2>nul
if %ERRORLEVEL%==0 (
  echo Gradle wrapper not found. Generating wrapper with installed Gradle...
  call gradle wrapper
  if errorlevel 1 (
    echo Failed to generate Gradle wrapper.
    popd
    exit /b 1
  )
  call gradlew.bat assembleDebug
  set "RESULT=%ERRORLEVEL%"
  popd
  exit /b %RESULT%
)

echo Neither gradlew.bat nor a system Gradle installation is available.
echo Open android_app in Android Studio and run Gradle Sync first, or install Gradle and rerun this script.
popd
exit /b 1