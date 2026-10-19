#!/bin/sh
# 代理实测（在服务器 ~/yunding-ops 下运行）：
#   sh scripts/check-proxies-docker.sh
# 经 wa-worker 容器，用每个账号自己的代理请求 ip-api.com，
# 校验出口 IP/国家是否与手机号区号一致。占位代理会自动跳过。
set -e
docker exec wa-worker mkdir -p /tmp/ck/scripts /tmp/ck/accounts
docker cp scripts/check-proxies.mjs wa-worker:/tmp/ck/scripts/check-proxies.mjs
docker exec wa-worker bash -c 'ln -sfn /app/node_modules /tmp/ck/node_modules && ln -sfn /config/accounts.yaml /tmp/ck/accounts/accounts.yaml && cd /tmp/ck/scripts && node check-proxies.mjs'
