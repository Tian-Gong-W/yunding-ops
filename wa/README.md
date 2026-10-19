# WhatsApp 多账号隔离 Worker

每个 WA 账号 = 独立浏览器进程 + 独立 `user-data-dir` + 固定指纹种子 + 独立代理 IP。
扫码一次，长期保活，**永不清理 profile 目录**。

## 快速开始（在 yunding-ops 根目录操作）

```bash
# 1. 下载指纹浏览器（默认最新稳定版，开箱即用；解压到 ./chromium/）
chmod +x wa/fetch-chromium.sh
./wa/fetch-chromium.sh

# 2. 账号配置（统一注册表）
vim accounts/accounts.yaml   # 填每个号的 proxy / fingerprintSeed / timezone
node scripts/accounts.mjs validate

# 3. 启动
docker compose up -d --build
docker compose logs -f wa-worker

# 4. 打开 http://服务器IP:8080，逐个扫码登录
```

# 4. 打开 http://服务器IP:8080，逐个扫码登录
```

## accounts.json 字段说明

| 字段 | 说明 |
|---|---|
| `name` | 账号标识，字母数字下划线，用于目录名 |
| `phone` | 手机号，仅展示用 |
| `proxy` | 该号独享代理，如 `http://ip:port` / `socks5://ip:port`，可带 `user:pass@`（见下） |
| `fingerprintSeed` | 32 位整数，**每个号唯一且永久固定**，指纹就靠它 |
| `platform` | `windows` / `linux` / `macos`，建议 windows（最常见） |
| `timezone` / `lang` | 与手机号归属地一致，如 `Asia/Hong_Kong` + `zh-HK` |

**代理鉴权**（fingerprint-chromium 的 `--proxy-server` 不支持密码，已自动处理）：
- `http://ip:port` / `socks5://ip:port`（无密码）→ 直接透传
- `http://user:pass@ip:port` → 自动走 `page.authenticate()`
- `socks5://user:pass@ip:port` → 自动起**本地 127.0.0.1 中转**（`proxy-relay.js`，同容器回环不出网），浏览器走无密码中转，上游 socks5 做用户名/密码认证。住宅代理基本都是这种形态，直接填就行

## 接码流程（新号）

1. 接码平台收短信验证码，在手机 WA App（或备用机）完成注册
2. 本 worker 启动后，到 8080 页面扫二维码把号挂上 Web 端
3. 前 5~7 天只保活：每天打开看看、和老号互聊几句，**不进陌生群、不群发**
4. 稳定后再做采集/承接（主力承接号与采集号分开）

## 稳号纪律

- 1 号 1 静态住宅 IP，IP 国家与手机号归属一致，中途不换 IP
- fingerprintSeed 写死不许改，改了等于换设备
- `wa-data/profiles/` 永不删除；重装/迁移时整个目录搬走
- 异常掉线只 reload 页面，**严禁频繁退出重扫**
- 账号间启动错峰 20 秒（代码里已做）

## 目录结构

```
wa-data/
  profiles/<name>/   # 每个号的浏览器数据，核心资产
  qr/<name>.png      # 待扫码二维码（登录后自动删除）
```
