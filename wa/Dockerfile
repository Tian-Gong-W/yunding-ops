FROM node:20-slim

# Chromium 运行依赖 + xvfb（有头模式反检测）+ 中文字体（二维码页/WhatsApp Web 显示）
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb xauth ca-certificates wget fonts-noto-cjk \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 \
    libpango-1.0-0 libcairo2 libatspi2.0-0 \
 && rm -rf /var/lib/apt/lists/*

# sing-box：把 vless:// 代理转成 127.0.0.1 socks5，供 Chromium 使用
# （vless-bridge.mjs 在容器启动时按账号生成配置并起 sing-box）
RUN SINGBOX_VER=$(wget -qO- https://api.github.com/repos/SagerNet/sing-box/releases/latest \
      | grep -o '"tag_name": *"[^"]*"' | head -1 | cut -d'"' -f4) \
 && echo "sing-box ${SINGBOX_VER}" \
 && wget -qO /tmp/sing-box.tar.gz \
      "https://github.com/SagerNet/sing-box/releases/download/${SINGBOX_VER}/sing-box-${SINGBOX_VER#v}-linux-amd64.tar.gz" \
 && tar -xzf /tmp/sing-box.tar.gz -C /tmp \
 && mv /tmp/sing-box-*/sing-box /usr/local/bin/sing-box \
 && chmod +x /usr/local/bin/sing-box \
 && rm -rf /tmp/sing-box* \
 && sing-box version

WORKDIR /app
COPY package.json ./
RUN npm install --omit=dev
COPY worker.js ./
COPY proxy-relay.js ./
COPY vless-bridge.mjs ./
COPY assets/ ./assets/
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

ENV CHROME_PATH=/opt/fingerprint-chromium/chrome \
    ACCOUNTS_JSON=/config/accounts.json \
    DATA_DIR=/data \
    STATUS_PORT=8080

EXPOSE 8080
# entrypoint.sh：直接起 Xvfb（轮询 socket 等就绪）再 exec node，
# 不用 xvfb-run（它在容器 PID 1 下等 Xvfb 的 SIGUSR1 会卡死）
CMD ["./entrypoint.sh"]
