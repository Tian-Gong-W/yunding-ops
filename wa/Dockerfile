FROM node:20-slim

# Chromium 运行依赖 + xvfb（有头模式反检测）+ 中文字体（二维码页/WhatsApp Web 显示）
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb ca-certificates wget fonts-noto-cjk \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 \
    libpango-1.0-0 libcairo2 libatspi2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY package.json ./
RUN npm install --omit=dev
COPY worker.js ./

ENV CHROME_PATH=/opt/fingerprint-chromium/chrome \
    ACCOUNTS_JSON=/config/accounts.json \
    DATA_DIR=/data \
    STATUS_PORT=8080

EXPOSE 8080
# xvfb-run 提供虚拟显示，浏览器以有头模式运行（反自动化检测效果最好）
CMD ["xvfb-run", "-a", "node", "worker.js"]
