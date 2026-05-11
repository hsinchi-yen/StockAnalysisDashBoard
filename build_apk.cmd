@echo off
setlocal EnableDelayedExpansion

echo ============================================================
echo  MicroEconomicDashBoard -- Android Debug APK Builder
echo ============================================================
echo.

:: Change to android_app directory
cd /d "%~dp0android_app"
if errorlevel 1 (
    echo ERROR: Cannot find android_app directory.
    pause & exit /b 1
)

set GRADLE_CMD=
if exist "gradlew.bat" (
    set GRADLE_CMD=gradlew.bat
) else (
    where gradle >nul 2>nul
    if !errorlevel! equ 0 (
        set GRADLE_CMD=gradle
    ) else (
        echo ERROR: Neither gradlew.bat nor global Gradle was found.
        echo.
        echo Fix options:
        echo   1. Open android_app\ in Android Studio and run Gradle Sync once
        echo      ^(it will generate wrapper files if configured^).
        echo   2. Or install Gradle and add it to PATH.
        echo   3. Or run in android_app\: gradle wrapper  ^(if gradle is available^).
        echo.
        pause & exit /b 1
    )
)

:: Check JAVA_HOME (optional - Gradle may find it on its own)
if "%JAVA_HOME%"=="" (
    echo [WARN] JAVA_HOME not set -- relying on system PATH for Java.
    echo        If build fails, install JDK 17 and set JAVA_HOME.
    echo.
)

echo [1/3] Syncing Python sources and web assets...
echo       (This happens automatically via Gradle preBuild task)
echo.

echo [2/3] Running assembleDebug...
echo.

call !GRADLE_CMD! assembleDebug --stacktrace 2>&1
set BUILD_RESULT=!errorlevel!

echo.
if !BUILD_RESULT! neq 0 (
    echo ============================================================
    echo  BUILD FAILED  (exit code !BUILD_RESULT!)
    echo ============================================================
    echo.
    echo Common causes:
    echo   - Android SDK not installed or ANDROID_HOME not set
    echo   - JDK 17 required (check JAVA_HOME)
    echo   - Missing SDK Platform / Build-Tools (open SDK Manager)
    echo   - Chaquopy license issue (free for personal use)
    echo.
    echo Tip: Open android_app\ in Android Studio, let Gradle sync,
    echo      then retry: Build ^> Build Bundle^(s^) / APK^(s^) ^> Build APK^(s^)
    echo.
    pause & exit /b 1
)

echo ============================================================
echo  BUILD SUCCESSFUL
echo ============================================================
echo.

set APK_PATH=app\build\outputs\apk\debug\app-debug.apk

if exist "%APK_PATH%" (
    echo APK location:
    echo   %~dp0android_app\%APK_PATH%
    echo.
    echo [3/3] Installation options:
    echo.
    echo   Option A -- ADB (USB cable or Wi-Fi ADB):
    echo     adb install "%~dp0android_app\%APK_PATH%"
    echo.
    echo   Option B -- File transfer to phone:
    echo     Copy the APK to your phone via USB / cloud storage,
    echo     then open it on the phone.
    echo     (Enable: Settings ^> Security ^> Install unknown apps)
    echo.
    echo   Option C -- QR / local HTTP server:
    echo     python -m http.server 8080 --directory "%~dp0android_app\app\build\outputs\apk\debug"
    echo     Then open http://<your-pc-ip>:8080/app-debug.apk on the phone.
    echo.
) else (
    echo WARNING: APK file not found at expected path.
    echo          Check android_app\app\build\outputs\apk\debug\
)

pause
