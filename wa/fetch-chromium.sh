#!/bin/sh
# 下载 fingerprint-chromium Linux 版（给 wa-worker 的 Docker 挂载用）
# 默认下载最新稳定版；如需指定版本，把 Release 页 Linux TAR.XZ 的直链作为参数传入
#   ./fetch-chromium.sh
#   ./fetch-chromium.sh "https://github.com/adryfish/fingerprint-chromium/releases/download/<tag>/<asset>.tar.xz"
set -e
URL="${1:-https://github.com/adryfish/fingerprint-chromium/releases/download/150.0.7871.186/ungoogled-chromium-150.0.7871.186-1-x86_64_linux.tar.xz}"

mkdir -p chromium
cd chromium
if [ ! -f fp-chromium.tar.xz ]; then
  echo "下载中（约 139MB）…"
  wget -O fp-chromium.tar.xz "$URL"
fi
echo "解压中…"
tar -xf fp-chromium.tar.xz

# 兼容不同的打包目录结构：找到 chrome 二进制后，在 ./chromium/chrome 建软链
# （compose 把 ./chromium 只读挂载到 /opt/fingerprint-chromium，CHROME_PATH 指向其下的 chrome）
BIN=$(find . -maxdepth 3 -type f -name chrome | head -n 1)
if [ -z "$BIN" ]; then
  echo "解压后找不到 chrome 二进制，请检查包结构"
  exit 1
fi
chmod +x "$BIN"
case "$BIN" in
  ./chrome) ;;
  *) ln -sfn "${BIN#./}" ./chrome && echo "已创建软链 ./chrome -> ${BIN#./}" ;;
esac
./chrome --version
echo "完成：./chromium/chrome（compose 会挂载到容器的 /opt/fingerprint-chromium）"
