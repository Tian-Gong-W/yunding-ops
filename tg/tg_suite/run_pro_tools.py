import os
import asyncio
from telethon import TelegramClient

import database
from core.cloner import ChannelCloner
from core.config import get_api_credentials, get_session_path
from core.interceptor import KeywordInterceptor


async def main():
    print("=" * 60)
    print("  Telegram PRO 高级模块工具集")
    print("=" * 60)

    api_id, api_hash = get_api_credentials()
    session_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")
    os.makedirs(session_dir, exist_ok=True)
    session_name = input("请输入执行 PRO 任务的 Session 账号名称 (默认: pro_worker): ").strip() or "pro_worker"

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

        while True:
            print("\n" + "-" * 40)
            print("请选择 PRO 任务:")
            print(" [1] 频道/群组内容克隆")
            print(" [2] 关键词实时监听")
            print(" [3] 查看历史截获线索记录")
            print(" [0] 退出")
            print("-" * 40)

            choice = input("请输入选项编号 (0-3): ").strip()

            if choice == "0":
                break

            if choice == "1":
                src = input("请输入源频道链接或 @username: ").strip()
                dst = input("请输入目标频道链接或 @username: ").strip()
                if not src or not dst:
                    continue

                try:
                    limit = int(input("搬运历史消息上限条数 (默认 200): ").strip() or 200)
                    start_id = int(input("起始消息 ID (默认 0): ").strip() or 0)
                    if not 1 <= limit <= 50000 or start_id < 0:
                        raise ValueError
                except ValueError:
                    print("❌ 消息条数或起始 ID 无效。")
                    continue

                replace_old = input("需要替换的旧文本 (选填): ").strip()
                replace_new = input("替换为的新文本 (选填): ").strip() if replace_old else ""
                rules = {replace_old: replace_new} if replace_old else None

                cloner = ChannelCloner(client, min_delay=3.0, max_delay=7.0)
                await cloner.clone_channel_content(
                    source_channel_link=src,
                    target_channel_link=dst,
                    limit=limit,
                    start_from_msg_id=start_id,
                    replace_rules=rules,
                )

            elif choice == "2":
                kw_input = input("请输入监控关键词（多个用逗号隔开）: ").strip()
                if not kw_input:
                    print("❌ 关键词不能为空。")
                    continue
                keywords = [k.strip() for k in kw_input.split(",") if k.strip()]
                exclude_input = input("请输入排除词（选填，多个用逗号隔开）: ").strip()
                exclude_words = [e.strip() for e in exclude_input.split(",") if e.strip()]
                forward_target = input("请输入事件接收目标: ").strip()
                group_input = input("请输入指定监控群组（多个逗号隔开，留空则使用默认范围）: ").strip()
                monitored_groups = [g.strip() for g in group_input.split(",") if g.strip()] or None

                interceptor = KeywordInterceptor(
                    client=client,
                    keywords=keywords,
                    exclude_words=exclude_words,
                    forward_target_peer=forward_target or None,
                )
                try:
                    await interceptor.start_listening(target_groups=monitored_groups)
                except (KeyboardInterrupt, SystemExit):
                    print("\n[!] 用户手动停止监听。")

            elif choice == "3":
                conn = database.get_db_connection()
                try:
                    cursor = conn.cursor()
                    cursor.execute("SELECT * FROM intercepted_leads ORDER BY created_at DESC LIMIT ?;", (20,))
                    leads = cursor.fetchall()
                finally:
                    conn.close()

                if not leads:
                    print("\n暂无截获线索记录。")
                else:
                    print(f"\n📋 最近截获的 {len(leads)} 条线索:")
                    for lead in leads:
                        message_text = (lead["message_text"] or "")[:60]
                        print(
                            f"  • [{lead['created_at']}] [{lead['matched_keyword']}] "
                            f"{lead['sender_name']} (@{lead['sender_username'] or '无'}): {message_text}..."
                        )

    finally:
        await client.disconnect()
        print("\n已退出 PRO 工具终端。")


if __name__ == "__main__":
    asyncio.run(main())
