@echo off
chcp 65001 >nul
rem Crea el APK de Android: dist\PC-Remote.apk
rem Necesita: Node.js, JDK 21 y el SDK de Android (viene con Android Studio)
cd /d "%~dp0"
if not exist node_modules call npm install || goto :err
call node scripts\sync-pad.js || goto :err
if not defined ANDROID_HOME set ANDROID_HOME=%LOCALAPPDATA%\Android\Sdk
for /d %%D in ("%ProgramFiles%\Microsoft\jdk-21*" "%ProgramFiles%\Eclipse Adoptium\jdk-21*" "%ProgramFiles%\Java\jdk-21*") do set JAVA_HOME=%%~D
echo sdk.dir=%ANDROID_HOME:\=/%>android\local.properties
call npx cap sync android || goto :err
pushd android
call gradlew.bat assembleDebug || (popd & goto :err)
popd
if not exist dist mkdir dist
copy /y android\app\build\outputs\apk\debug\app-debug.apk dist\PC-Remote.apk >nul
echo.
echo LISTO. APK: dist\PC-Remote.apk  (copialo al movil e instalalo)
pause
exit /b 0
:err
echo ERROR al crear el APK.
pause
exit /b 1
