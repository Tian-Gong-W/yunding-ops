import os
import asyncio
from typing import List
from telethon import TelegramClient

import database
from core.config import get_api_credentials, get_session_path
from core.inviter import TelegramInviter
from core.sender import TelegramMassSender, SpintaxParser


async def init_client_pool(session_names: List[str]) -> List[TelegramClient]:
    """初始化并连接账号池。"""
    api_id, api_hash = get_api_credentials()
    session_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")
    os.makedirs(session_dir, exist_ok=True)

    clients: List[TelegramClient] = []
    try:
        for name in session_names:
            session_path = get_session_path(session_dir, name)
            client = TelegramClient(str(session_path), api_id, api_hash)
            await client.start()
            me = await client.get_me()
            print(f"  [+] 账号 [{name}] 连接成功: {me.first_name} (@{me.username or '无用户名'})")
            clients.append(client)
    except Exception:
        for client in clients:
            await client.disconnect()
        raise
    return clients


async def main():
    print("=" * 60)
    print("  Telegram 营销矩阵执行终端")
    print("=" * 60)

    try:
        acc_input = input("请输入参与作业的 Session 账号名称（多个用逗号隔开，默认 default_worker）: ").strip() or "default_worker"
        session_names = [a.strip() for a in acc_input.split(",") if a.strip()]
        if not session_names:
            print("❌ 至少需要一个 Session 名称。")
            return

        print(f"\n[*] 正在登录 {len(session_names)} 个工作账号...")
        clients = await init_client_pool(session_names)

        while True:
            print("\n" + "-" * 40)
            print("请选择营销任务:")
            print(" [1] 批量拉入已授权目标群组")
            print(" [2] 发送已获许可的模板消息")
            print(" [3] 测试 Spintax 话术生成效果")
            print(" [4] 查看当前本地受众池数据")
            print(" [0] 退出")
            print("-" * 40)

            choice = input("请输入选项编号 (0-4): ").strip()

            if choice == "0":
                break

            if choice == "1":
                target_group = input("请输入目标群组链接: ").strip()
                if not target_group:
                    continue
                try:
                    tag_filter = input("按标签筛选受众: ").strip()
                    max_limit = int(input("本次最大目标数 (默认 100): ").strip() or 100)
                    max_per_acc = int(input("每个账号单轮最大数 (默认 15): ").strip() or 15)
                    if not 1 <= max_limit <= 50000 or not 1 <= max_per_acc <= 500:
                        raise ValueError
                except ValueError:
                    print("❌ 参数必须是有效范围内的整数。")
                    continue

                users = database.query_collected_users(
                    tag=tag_filter or None,
                    only_active=True,
                    limit=max_limit,
                )
                if not users:
                    print("❌ 数据库中没有符合条件的受众数据。")
                    continue

                inviter = TelegramInviter(
                    clients,
                    min_delay=20,
                    max_delay=45,
                    max_per_account=max_per_acc,
                )
                await inviter.execute_batch_invite(target_group, users)

            elif choice == "2":
                template = input("请输入已获许可的消息模板: ").strip()
                if not template:
                    print("消息模板不能为空！")
                    continue
                try:
                    tag_filter = input("按标签筛选受众: ").strip()
                    max_limit = int(input("本次最大目标数 (默认 50): ").strip() or 50)
                    max_per_acc = int(input("每个账号单轮最大消息数 (默认 20): ").strip() or 20)
                    if not 1 <= max_limit <= 50000 or not 1 <= max_per_acc <= 500:
                        raise ValueError
                except ValueError:
                    print("❌ 参数必须是有效范围内的整数。")
                    continue

                users = database.query_collected_users(
                    tag=tag_filter or None,
                    limit=max_limit,
                )
                if not users:
                    print("❌ 受众数据池为空。")
                    continue

                sender = TelegramMassSender(
                    clients,
                    min_delay=20,
                    max_delay=40,
                    max_per_account=max_per_acc,
                )
                await sender.send_mass_dm(users, template)

            elif choice == "3":
                sample_template = input(
                    "输入测试模板 (默认: {您好|Hi|Hello} {name}，对{项目|业务}感兴趣吗？{random_emoji}): "
                ).strip() or "{您好|Hi|Hello} {name}，对{项目|业务}感兴趣吗？{random_emoji}"
                mock_user = {"first_name": "张总", "username": "zhang_vip"}
                print("\n🎲 随机生成 5 组不同变体展示:")
                for i in range(5):
                    out = SpintaxParser.parse(sample_template, mock_user)
                    print(f"  变体 {i + 1}: {out}")

            elif choice == "4":
                stats = database.get_database_stats()
                print("\n📊 数据库受众统计:")
                for key, value in stats.items():
                    print(f"  • {key}: {value}")

    finally:
        for client in locals().get("clients", []):
            await client.disconnect()
        print("\n已退出营销终端。")


if __name__ == "__main__":
    asyncio.run(main())
