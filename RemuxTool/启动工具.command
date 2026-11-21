#!/bin/bash
# 视频批量重封装工具 - macOS 双击启动脚本
# 自动探测带 tkinter 的 Python，无需网络

cd "$(dirname "$0")" || exit 1

PY=""
# 候选 Python 列表（按优先级）
CANDIDATES=(
  "$(command -v python3 2>/dev/null)"
  "/opt/homebrew/bin/python3"
  "/usr/local/bin/python3"
  "$HOME/.workbuddy/binaries/python/versions/3.13.12/bin/python3"
)
# 追加 WorkBuddy 其它版本
for p in "$HOME"/.workbuddy/binaries/python/versions/*/bin/python3; do
  [ -x "$p" ] && CANDIDATES+=("$p")
done

for p in "${CANDIDATES[@]}"; do
  [ -n "$p" ] || continue
  [ -x "$p" ] || continue
  if "$p" -c "import tkinter" >/dev/null 2>&1; then
    PY="$p"
    break
  fi
done

if [ -z "$PY" ]; then
  osascript -e 'display dialog "未找到带图形界面支持(tkinter)的 Python。\n\n请安装 Python 3（建议从 python.org 安装，自带 tkinter），或在本机安装 Homebrew 后运行 brew install python-tk。" buttons {"确定"} with icon stop' >/dev/null 2>&1
  echo "错误：未找到带 tkinter 的 Python，退出。"
  exit 1
fi

echo "使用 Python: $PY"
exec "$PY" "$(dirname "$0")/remux_gui.py"
