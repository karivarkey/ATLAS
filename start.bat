@echo off
REM Start Atlas backend (Windows). Frontend is served from frontend/dist.
cd /d "%~dp0"
if not exist frontend\dist (
  echo Building frontend...
  pushd frontend && bun install && bunx vite build && popd
)
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --app-dir backend
