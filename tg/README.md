# TG Telegram Suite

这是一个基于 Python、FastAPI 与 Telethon 的 Telegram 管理控制台，主要由 `tg_suite/` 提供 Web API、账号资产管理、本地数据库和交互式 CLI。

## 目录

```text
tg_suite/
├── server.py              # FastAPI 控制台服务
├── database.py            # SQLite 数据层
├── core/                  # Telegram 业务模块与运行时配置
├── static/                # Web 控制台
├── tests/                 # 轻量级单元测试
├── run_scraper.py         # 数据采集入口
├── run_marketing.py       # 工作流入口
├── run_pro_tools.py       # PRO 工具入口
└── run_operations.py      # 运维入口
```

## 快速开始

1. 进入项目目录：

```bash
cd tg_suite
```

2. 建立虚拟环境并安装依赖：

```bash
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell
# .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

3. 根据 `.env.example` 设置 `TG_API_ID`、`TG_API_HASH` 以及控制台 API Key。

4. 启动 Web 控制台：

```bash
python server.py
```

默认监听 `127.0.0.1:8000`。

## 配置原则

Telegram API 凭据不再写入任何 CLI 源文件，统一从环境变量读取。Session 名称也会在进入文件系统之前进行格式校验，避免把用户输入直接当作路径使用。

运行时账号目录、Session、上传文件、数据库和导出结果均应视为敏感资产，项目 `.gitignore` 已针对这些目录和文件类型做了默认排除。

生产环境必须显式配置 `TG_ENV=production` 与管理员 API Key；不要使用开发环境的认证旁路。

## 验证

项目提供不依赖 Telegram 登录态的轻量测试：

```bash
cd tg_suite
PYTHONPATH=. python -m unittest discover -s tests -v
python -m compileall -q .
```

GitHub Actions 同时对 `tg_suite` 做语法编译和单元测试检查。

## 安全提示

TData、Telethon Session、代理凭据以及数据库中的用户数据都可能包含高敏感信息。不要提交到 Git，也不要把管理 API Key 暴露给不受信任的网络环境。

对于任何自动化消息、邀请、采集或内容转载功能，请遵守 Telegram 平台规则、适用法律以及目标用户的授权和隐私要求。
