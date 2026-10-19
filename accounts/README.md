# accounts/ — 统一账号注册表

`accounts.yaml` 是所有 TG / WhatsApp 账号的**唯一建档位置**。

| 小节 | 用途 |
|---|---|
| `whatsapp` | `wa-worker` 启动时**直接读取**，改完重启容器生效 |
| `telegram` | 建档 + 代理预置。账号本体在 `tg-worker` 的 Web 控制台（:8000）导入 session/tdata；代理可用脚本预写入 |

```bash
# 校验注册表（重名/seed 重复/代理格式/必填项）
node scripts/accounts.mjs validate

# 把 telegram 一节的 proxy 写入 tg/tg_suite/accounts/<name>/proxy.txt（0600 权限）
# 适合在 tg-worker 控制台批量导入前先把代理配好
node scripts/accounts.mjs sync-tg
```

**纪律**（防封）：

- WhatsApp：1 号 1 静态住宅 IP，IP 国家与手机号归属一致；`fingerprintSeed` 永久固定；`wa-data` 命名卷永不删除
- Telegram：1 个 IP 最多绑 3 个号；新号先养 3~5 天再采集
- 主力承接号绝不参与采集和陌生私信（死小号不伤主号）
