@echo off
setlocal
REM ============================================================
REM  process_designer - corporate-net venv setup + check + run
REM  (ASCII only: avoids Korean-encoding batch parse errors)
REM  local C: venv -> deps(native-tls) -> check_env.py -> run app
REM ============================================================
set "APP=process_designer"
title %APP%
REM venv at %LOCALAPPDATA% (user-writable, local C: disk) - no admin needed on shared PC
set "VENV=%LOCALAPPDATA%\venvs\%APP%"
set "PORT=8540"
cd /d "%~dp0"

REM ---- network (proxy on/off) is auto-detected below, after the python guard ----

REM ---- use Windows cert store (fixes SSL UnknownIssuer behind corp SSL inspection) ----
set "UV_NATIVE_TLS=true"

REM ---- corp proxy drops large parallel downloads: serialize + longer timeout (resumable) ----
set "UV_CONCURRENT_DOWNLOADS=1"
set "UV_HTTP_TIMEOUT=300"

REM ---- force 64-bit Python: shared PC may have 32-bit (Python312-32) -> no C-ext wheel -> MSVC build fail ----
set "UV_PYTHON_PREFERENCE=only-system"
REM ---- pin interpreter for ALL uv ops incl build isolation (avoid 32-bit in temp build venv) ----
set "UV_PYTHON=cpython-3.12-windows-x86_64"

echo ============================================================
echo   %APP%    venv: %VENV%    port: %PORT%
echo ============================================================

where uv >nul 2>nul
if errorlevel 1 (
  echo [ERROR] uv not installed. Install from https://docs.astral.sh/uv/ then retry.
  goto :end
)

set "UV_PROJECT_ENVIRONMENT=%VENV%"

echo.
echo [1/3] install deps (UV_NATIVE_TLS=true : use Windows cert store)
REM --- require 64-bit Python 3.12 (32-bit Python312-32 auto-excluded by x86_64) ---
uv python find cpython-3.12-windows-x86_64 >nul 2>nul
if errorlevel 1 (
  echo [ERROR] 64-bit Python 3.12 not found.
  echo         Install 64-bit Python 3.12 from https://www.python.org/downloads/windows/
  echo         32-bit ^(Python312-32^) is unusable: pandas/pyarrow have no 32-bit wheels.
  goto :end
)

REM --- network: pypi needs the proxy on the corporate net, and must NOT use it outside.
REM     Detected with path_config.is_internal() (= NAS_BASE_PATH reachable?), the same rule
REM     the app itself uses. It imports stdlib only, so it runs before deps are installed.
REM     Do NOT ask the user: an unanswered prompt silently picked the wrong network before.
REM     Override when detection is wrong (e.g. corporate net with NAS unmounted):
REM       set PD_NET=corporate     (or  set PD_NET=external)  before running this file.
set "NETMODE="
if defined PD_NET set "NETMODE=%PD_NET%"
if defined NETMODE goto :netdone
set "SYSPY="
for /f "delims=" %%P in ('uv python find cpython-3.12-windows-x86_64 2^>nul') do set "SYSPY=%%P"
if not defined SYSPY goto :netext
"%SYSPY%" -c "import path_config,sys; sys.exit(0 if path_config.is_internal() else 1)" >nul 2>nul
if errorlevel 1 goto :netext
set "NETMODE=corporate"
goto :netdone
:netext
set "NETMODE=external"
:netdone
if /I "%NETMODE%"=="corporate" (
  set "HTTP_PROXY=http://60.200.254.1:9090"
  set "HTTPS_PROXY=http://60.200.254.1:9090"
  echo   [net] corporate ^(NAS reachable^) : proxy ON
) else (
  echo   [net] external ^(NAS not reachable^) : proxy OFF   [override: set PD_NET=corporate]
)

REM --- --locked: honor uv.lock as-is. Without it uv silently re-resolves to the newest
REM     releases when the lock drifts - that is exactly how this app ended up needing a
REM     fresh pyarrow download and failing on the corporate net. Fail loudly instead.
if exist "pyproject.toml" (
  uv sync --locked --python cpython-3.12-windows-x86_64
) else (
  echo [ERROR] no pyproject.toml found.
  goto :end
)
if errorlevel 1 (
  echo.
  echo [FAIL] dependency install failed. Read the actual uv error above first.
  echo        These are only hints - not detections:
  echo   - Connect / timeout    : on the corporate net? rerun and answer I at the Network prompt
  echo   - SSL UnknownIssuer    : UV_NATIVE_TLS=true is already set.
  echo                            else  set SSL_CERT_FILE=C:\path\corp-ca.pem
  echo   - lock is out of date  : uv lock   ^(then rerun; --locked refuses a drifted lock^)
  echo   - last resort: uv sync --native-tls --allow-insecure-host pypi.org --allow-insecure-host files.pythonhosted.org
  goto :end
)

echo.
echo [2/3] check_env.py (paths/deps/frontend assets/data folder)
"%VENV%\Scripts\python.exe" check_env.py
if errorlevel 1 echo [WARN] check_env reported FAIL items. See [FAIL] lines above.

echo.
choice /C YN /M "Run the app now (Y=run, N=quit)"
if errorlevel 2 goto :end

echo.
echo [3/3] run app - http://localhost:%PORT%   (stop: Ctrl+C in this window)
"%VENV%\Scripts\python.exe" -m streamlit run app.py --server.port %PORT%

:end
endlocal
pause
