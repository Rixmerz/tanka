@echo off
rem Windows twin of chromium-headless: headless Chromium announces itself as "HeadlessChrome", and WhatsApp Web
rem refuses it. This runs the same browser with the user agent a normal window sends. Rastro starts it through
rem RASTRO_CHROMIUM; TANKA_BROWSER (or TANKA_WHATSAPP_BROWSER) picks the real executable.
setlocal EnableDelayedExpansion
set "browser="
for %%b in ("%TANKA_BROWSER%" "%TANKA_WHATSAPP_BROWSER%" "%ProgramFiles%\Google\Chrome\Application\chrome.exe" "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" "%LocalAppData%\Google\Chrome\Application\chrome.exe" "%ProgramFiles%\Chromium\Application\chrome.exe" "%LocalAppData%\Chromium\Application\chrome.exe" "%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe" "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe") do (
  if not defined browser if not "%%~b"=="" if exist "%%~b" set "browser=%%~b"
)
if not defined browser (
  echo chromium-headless: no Chromium, Chrome, Brave or Edge found; set TANKA_BROWSER 1>&2
  exit /b 1
)
rem chrome.exe --version prints nothing on Windows: the version is the name of the folder beside it.
set "ver=124.0.0.0"
for %%f in ("!browser!") do set "dir=%%~dpf"
for /d %%v in ("!dir!*.*.*.*") do set "ver=%%~nxv"
"!browser!" %* "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/!ver! Safari/537.36"
