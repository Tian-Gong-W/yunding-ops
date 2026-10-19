import os
import asyncio
from telethon import TelegramClient

from core.account_actions import AccountActionManager
from core.broadcaster import GroupAutoPoster, ChannelAutoCommenter
from core.config import get_api_credentials, get_session_path
from core.contacts_stories import ContactsAndStoriesManager


async def main():
    print("=" * 60)
    print("  Telegram 运维增强终端")
    print("=" * 60)

    api_id, api_hash = get_api_credentials()
    session_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")
    os.makedirs(session_dir, exist_ok=True)
    session_name = input("请输入执行操作的 Session 账号名称 (默认: op_worker): ").strip() or "op_worker"

    try:
        session_path = get_session_path(session_dir, session_name)
    except ValueError as exc:
        print(f"❌ Session 名称无效: {exc}")
        return

    client = TelegramClient(str(session_path), api_id, api_hash)
    try:
        await client.start()
        me = await client.get_me()
        print(f"\n[+] 账号连接成功: {me.first_name} (@{me.username or '无用户名'}, ID: {me.id})")

        action_mgr = AccountActionManager(client)
        contact_story_mgr = ContactsAndStoriesManager(client)

        while True:
            print("\n" + "-" * 40)
            print("请选择运维任务:")
            print(" [1] 退出无用群组与频道")
            print(" [2] @SpamBot 风控状态检测")
            print(" [3] 群聊定时广播")
            print(" [4] 频道新帖自动评论")
            print(" [5] 清空当前账号联系人")
            print(" [6] 发布 Stories")
            print(" [0] 退出")
            print("-" * 40)

            choice = input("请输入选项编号 (0-6): ").strip()

            if choice == "0":
                break

            if choice == "1":
                confirm = input("⚠️ 确认退出当前账号加入的非自建群组/频道？(y/n): ").strip().lower()
                if confirm == "y":
                    await action_mgr.batch_leave_chats()

            elif choice == "2":
                res = await action_mgr.check_spambot_status()
                print(f"📊 检测结果: {res.get('detail', '未知')}")

            elif choice == "3":
                groups_input = input("请输入待广播的群组（多个逗号隔开）: ").strip()
                template = input("请输入广播文案: ").strip()
                if not groups_input or not template:
                    continue
                try:
                    interval = int(input("循环广播间隔时间（分钟，默认 60）: ").strip() or 60)
                    if not 1 <= interval <= 10080:
                        raise ValueError
                except ValueError:
                    print("❌ 间隔必须是 1-10080 的整数分钟。")
                    continue
                groups = [g.strip() for g in groups_input.split(",") if g.strip()]
                media = input("可选图片/海报文件路径（选填）: ").strip()

                poster = GroupAutoPoster(
                    client=client,
                    target_groups=groups,
                    spintax_template=template,
                    interval_minutes=interval,
                    media_file=media or None,
                )
                try:
                    await poster.start_broadcasting()
                except (KeyboardInterrupt, SystemExit):
                    print("\n[!] 广播已手动停止。")

            elif choice == "4":
                channels_input = input("请输入监控频道（多个逗号隔开）: ").strip()
                comment_text = input("请输入自动评论文案: ").strip()
                if not channels_input or not comment_text:
                    continue
                channels = [c.strip() for c in channels_input.split(",") if c.strip()]
                commenter = ChannelAutoCommenter(client, channels, comment_text)
                try:
                    await commenter.start_monitoring()
                except (KeyboardInterrupt, SystemExit):
                    print("\n[!] 评论监听已手动停止。")

            elif choice == "5":
                confirm = input("⚠️ 确认清空当前账号的全部联系人？(y/n): ").strip().lower()
                if confirm == "y":
                    await contact_story_mgr.clear_all_contacts()

            elif choice == "6":
                media_path = input("请输入要发布为故事的图片/视频路径: ").strip()
                if not media_path:
                    continue
                caption = input("故事文案/描述（选填）: ").strip()
                await contact_story_mgr.publish_story(media_path, caption=caption)

    finally:
        await client.disconnect()
        print("\n已退出运维终端。")


if __name__ == "__main__":
    asyncio.run(main())
