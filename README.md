# yunding-ops — TG / WhatsApp 账号统一作战仓库

把原来散在三个地方的东西收到一个仓库里，一套 `docker-compose` 全拉起：

| 服务 | 来源 | 角色 | 端口 |
|---|---|---|---|
| `yunding-bot` | Koishi 官方镜像（配置沿用 YundingTiangong） | 官方 Bot + 主力承接号（只做被动承接） | 5140 |
| `tg-worker` | `tg/`（submodule → TG-JIQIREN，TG 分支） | TG 个人号矩阵：养号 / 采集 / 引流 | 8000 |
| `wa-worker` | `wa/`（自研隔离浏览器模块） | WA 个人号矩阵：扫码登录 / 长期保活 | 8080 |

账号统一建档在 [`accounts/accounts.yaml`](accounts/accounts.yaml)，说明见 [`accounts/README.md`](accounts/README.md)。

## 目录结构

```
yunding-ops/
├── docker-compose.yml      # 三服务编排
├── .env                    # tg-worker 凭据（cp .env.example .env 后填写，不提交）
├── accounts/
│   ├── accounts.yaml       # 统一账号注册表（WA + TG）
│   └── README.md
├── tg/                     # git submodule → Tian-Gong-W/TG-JIQIREN（TG 分支）
├── wa/                     # WhatsApp 隔离 worker（fingerprint-chromium + rebrowser-puppeteer）
├── koishi/                 # 中控说明（数据卷由 compose 管理）
├── chromium/               # fingerprint-chromium 解压目录（wa/fetch-chromium.sh 下载，不提交）
└── scripts/
    └── accounts.mjs        # 注册表校验 / TG 代理同步
```

## 快速开始

```bash
# 1. 拉代码（含 submodule）
git clone --recurse-submodules <本仓库地址>
cd yunding-ops

# 2. 凭据
cp .env.example .env && vim .env   # 填 TG_API_ID / TG_API_HASH / API Keys

# 3. 账号
vim accounts/accounts.yaml        # 填 WA/TG 账号、代理、指纹种子
node scripts/accounts.mjs validate

# 4. 指纹浏览器（wa-worker 用）
cd wa && ./fetch-chromium.sh "<Release 页 Linux TAR.XZ 直链>" && cd ..

# 5. 启动
docker compose up -d --build
docker compose logs -f wa-worker   # 打开 http://服务器IP:8080 扫码
```

打开 `http://服务器IP:8000` 进 TG 控制台（用 `.env` 里的 API Key 登录），
`http://服务器IP:8080` 给 WA 账号扫码，`http://服务器IP:5140` 是 Koishi 中控。

## 角色隔离纪律

- **主力承接号**（Koishi 侧）绝不参与外部群采集和陌生私信
- **WA 采集/引流号**：1 号 1 静态住宅 IP（国家与手机号一致），前 5~7 天只保活
- **TG 号**：1 IP 最多绑 3 个号，新号养 3~5 天再采集
- `wa-data` / `tg-data` 命名卷是核心资产，不要删
