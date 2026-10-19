import os
import asyncio
from datetime import datetime
from telethon import TelegramClient

import database
from core.config import get_api_credentials, get_session_path
from core.scraper import TelegramScraper


async def main():
    print("=" * 60)
    print("  Telegram 智能受众采集与数据筛选终端")
    print("=" * 60)

    api_id, api_hash = get_api_credentials()
    session_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")
    os.makedirs(session_dir, exist_ok=True)

    session_name = input("请输入用于采集的 Session 账号名称 (默认: default_scraper): ").strip() or "default_scraper"
    try:
        session_path = get_session_path(session_dir, session_name)
    except ValueError as exc:
        print(f"❌ Session 名称无效: {exc}")
        return

    client = TelegramClient(str(session_path), api_id, api_hash)
    try:
        await client.start()
        me = await client.get_me()
        print(f"\n[+] 账号连接成功! 当前登录账号: {me.first_name} (@{me.username or '无用户名'}, ID: {me.id})")

        scraper = TelegramScraper(client)

        while True:
            print("\n" + "-" * 40)
            print("请选择采集模式:")
            print(" [1] 采集全量群成员 (可过滤机器人)")
            print(" [2] 采集近期发言活跃者")
            print(" [3] 采集频道评论区互动者")
            print(" [4] 查看当前数据库统计与受众池")
            print(" [5] 导出当前受众数据为 CSV 文件")
            print(" [0] 退出")
            print("-" * 40)

            choice = input("请输入选项编号 (0-5): ").strip()

            if choice == "0":
                break

            if choice == "1":
                target = input("请输入目标群组链接或 @username: ").strip()
                if not target:
                    continue
                try:
                    limit = int(input("请输入最大采集人数 (默认 1000): ").strip() or 1000)
                    if not 1 <= limit <= 50000:
                        raise ValueError
                except ValueError:
                    print("❌ 最大采集人数必须是 1-50000 的整数。")
                    continue
                try:
                    users = await scraper.scrape_group_members(target, limit=limit)
                    print(f"✅ 采集完成，成功获取 {len(users)} 名群成员数据并写入本地数据库。")
                except Exception as exc:
                    print(f"❌ 采集出错: {exc}")

            elif choice == "2":
                target = input("请输入目标群组链接或 @username: ").strip()
                if not target:
                    continue
                try:
                    days = int(input("请输入回溯天数 (默认 7 天): ").strip() or 7)
                    if not 1 <= days <= 365:
                        raise ValueError
                except ValueError:
                    print("❌ 回溯天数必须是 1-365 的整数。")
                    continue
                try:
                    chatters = await scraper.scrape_active_chatters(target, days_lookback=days)
                    print(f"✅ 采集完成，成功获取 {len(chatters)} 位过去 {days} 天内真实发言的活跃用户！")
                except Exception as exc:
                    print(f"❌ 采集出错: {exc}")

            elif choice == "3":
                target = input("请输入目标公开频道链接或 @username: ").strip()
                if not target:
                    continue
                try:
                    commenters = await scraper.scrape_channel_commenters(target)
                    print(f"✅ 采集完成，成功从讨论组获取 {len(commenters)} 位互动评论用户。")
                except Exception as exc:
                    print(f"❌ 采集出错: {exc}")

            elif choice == "4":
                stats = database.get_database_stats()
                print("\n📊 数据库受众池统计:")
                print(f"  • 总用户数: {stats['total_users']}")
                print(f"  • 拥有 @username: {stats['with_username']}")
                print(f"  • 活跃发言者: {stats['active_chatters']}")
                print(f"  • Telegram Premium 会员: {stats['premium_users']}")
                print(f"  • 覆盖群组/频道来源: {stats['sources_count']}")

            elif choice == "5":
                exports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exports")
                os.makedirs(exports_dir, exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = os.path.join(exports_dir, f"telegram_leads_{timestamp}.csv")
                database.export_users_to_csv(filename)
                print(f"✅ 导出成功！文件已保存至: {filename}")

    finally:
        await client.disconnect()
        print("\n已安全退出。")


if __name__ == "__main__":
    asyncio.run(main())
