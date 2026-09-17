#!/usr/bin/env bash
# stock-god Web 一键启动（生产模式：FastAPI 托管前端构建产物）
# 用法: webapp/start.sh [端口]   默认 8000
set -euo pipefail

WEBAPP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${1:-8000}"

BACKEND="$WEBAPP/backend"
FRONTEND="$WEBAPP/frontend"
DIST="$WEBAPP/frontend-dist"
VENV="$BACKEND/.venv"

# 1. 检查后端虚拟环境
if [ ! -x "$VENV/bin/python" ]; then
  echo "错误: 未找到后端虚拟环境 $VENV" >&2
  echo "请先创建并安装依赖:" >&2
  echo "  python3 -m venv $VENV && $VENV/bin/pip install -r $BACKEND/requirements.txt" >&2
  exit 1
fi

# 2. 前端产物不存在则构建
if [ ! -f "$DIST/index.html" ]; then
  echo "前端产物不存在，开始构建..."
  if [ ! -d "$FRONTEND/node_modules" ]; then
    (cd "$FRONTEND" && npm install)
  fi
  (cd "$FRONTEND" && npm run build)
fi

# 3. 启动服务（Ctrl+C 停止）
echo "stock-god Web 启动: http://127.0.0.1:$PORT"
cd "$BACKEND"
exec "$VENV/bin/python" -m uvicorn main:app --host 127.0.0.1 --port "$PORT"
