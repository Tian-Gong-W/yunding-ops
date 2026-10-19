#!/bin/sh
# 下载 fingerprint-chromium Linux 版
# 用法: ./fetch-chromium.sh "<Release 页面里 Linux TAR.XZ 的直链>"
# 直链获取：打开 https://github.com/adryfish/fingerprint-chromium/releases
#          复制对应版本 Linux 一行的 TAR.XZ 链接，粘贴为参数
set -e
if [ -z "$1" ]; then
  echo "用法: $0 \"<Linux TAR.XZ 直链>\""
  echo "到 https://github.com/adryfish/fingerprint-chromium/releases 复制链接"
  exit 1
fi
mkdir -p chromium
cd chromium
echo "下载中…"
wget -O fp-chromium.tar.xz "$1"
echo "解压中…"
tar -xf fp-chromium.tar.xz --strip-components=1
chmod +x chrome
./chrome --version || true
echo "完成：./chromium/chrome"
