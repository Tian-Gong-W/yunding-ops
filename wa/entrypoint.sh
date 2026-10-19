#!/bin/sh
# entrypoint: 不用 xvfb-run（它靠 SIGUSR1 等 Xvfb 就绪，在容器 PID 1 下会卡死），
# 改为直接起 Xvfb + 轮询 socket，确定性更高。
set -e
Xvfb :99 -screen 0 1280x1024x24 -nolisten tcp &
export DISPLAY=:99
for i in $(seq 1 40); do
  if [ -S /tmp/.X11-unix/X99 ]; then
    echo "Xvfb ready (DISPLAY=:99)"
    break
  fi
  sleep 0.5
done
if [ ! -S /tmp/.X11-unix/X99 ]; then
  echo "Xvfb 未能在 20 秒内就绪，退出"
  exit 1
fi
exec node worker.js
