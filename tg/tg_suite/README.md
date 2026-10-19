# Telegram Suite

基于 Python、FastAPI、Telethon 的 Telegram 管理与自动化控制台。

## 工程结构

```text
tg_suite/
├── server.py              # FastAPI Web API / 控制台服务
├── database.py            # SQLite 数据层
├── core/
│   ├── config.py          # API 凭据读取、Session 名称校验与路径保护
│   ├── scraper.py         # 数据采集模块
│   ├── inviter.py         # 群组成员操作模块
│   ├── sender.py          # 消息模板与发送模块
│   ├── cloner.py          # 内容复制模块
│   ├── interceptor.py     # 关键词事件监听模块
│   ├── account_actions.py # 账号维护模块
│   ├── broadcaster.py     # 定时发布模块
│   └── contacts_stories.py# 联系人与 Stories 模块
├── static/index.html      # Web 控制台
├── tests/                 # 不依赖 Telegram 登录态的测试
└── run_*.py               # 四个交互式入口
```

## 配置

复制 `.env.example` 到部署环境并设置：

```bash
TG_ENV=production
TG_ADMIN_API_KEY=<随机长密钥>
TG_OPERATOR_API_KEY=<随机长密钥>
TG_API_ID=<Telegram API ID>
TG_API_HASH=<Telegram API Hash>
```

CLI 入口现在统一从环境变量读取 Telegram API 凭据，不再在源代码中保存固定值。Session 名称限制为单段安全标识，避免直接拼接用户输入形成越界路径。

仅在隔离的本机开发环境中使用认证旁路；生产环境应保持认证开启。

## 启动 Web 服务

```bash
python server.py
```

默认绑定 `127.0.0.1:8000`。生产部署时请放在受信任的反向代理或私有网络之后，并使用 Secret 管理 API Key、Telegram 凭据和代理凭据。

## CLI

```bash
python run_scraper.py
python run_marketing.py
python run_pro_tools.py
python run_operations.py
```

这些入口主要用于人工管理和受控自动化。涉及消息、邀请、采集或转载的操作应当基于合法授权，并遵守 Telegram 平台规则及适用法律。

## 测试与静态检查

```bash
python -m compileall -q .
PYTHONPATH=. python -m unittest discover -s tests -v
```

仓库包含 GitHub Actions 工作流，用于自动执行 Python 语法编译与轻量单元测试。

## 敏感资产

以下内容不要提交到 Git：

- Telegram TData / Telethon Session
- API Key / API Hash / 代理账号密码
- SQLite 数据库
- 上传文件和导出 CSV

`.gitignore` 已经覆盖常见运行时目录和文件类型，但已有历史提交中的敏感资产仍需单独检查与清理。
