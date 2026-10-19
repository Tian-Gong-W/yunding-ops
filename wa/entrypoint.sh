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

# vless 桥接：如有 vless:// 代理，生成 sing-box 配置并起 sing-box，
# 把账号配置改写为本地 socks5（输出 /tmp/accounts.bridge.json）
BRIDGE_LOG=$(node /app/vless-bridge.mjs 2>&1) || {
  echo "$BRIDGE_LOG"
  echo "vless 桥接失败，退出"
  exit 1
}
echo "$BRIDGE_LOG"
BRIDGE_ACCOUNTS=$(echo "$BRIDGE_LOG" | grep '^BRIDGE_ACCOUNTS=' | cut -d= -f2 | tail -1)
if [ -n "$BRIDGE_ACCOUNTS" ] && [ -f "$BRIDGE_ACCOUNTS" ]; then
  export ACCOUNTS_JSON="$BRIDGE_ACCOUNTS"
  unset ACCOUNTS_YAML
  echo "账号配置已切换为桥接后版本: $ACCOUNTS_JSON"
fi
if [ -f /tmp/sing-box.json ]; then
  /usr/local/bin/sing-box run -c /tmp/sing-box.json >>/tmp/sing-box.log 2>&1 &
  echo "sing-box 已启动 (pid $!)"
  sleep 2
  if ! kill -0 $! 2>/dev/null; then
    echo "sing-box 启动失败，日志："
    tail -20 /tmp/sing-box.log || true
    exit 1
  fi
fi

exec node worker.js
