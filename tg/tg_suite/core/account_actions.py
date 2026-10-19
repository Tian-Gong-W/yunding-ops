import re
import asyncio
import random
from typing import List, Dict, Optional, Any
from telethon import TelegramClient
from telethon.tl.functions.channels import LeaveChannelRequest
from telethon.tl.functions.messages import DeleteChatUserRequest
from telethon.tl.types import Channel, Chat
from telethon.errors import FloodWaitError

class AccountActionManager:
    """账号日常状态维护与批处理引擎"""

    def __init__(self, client: TelegramClient):
        self.client = client

    # ==========================================
    # 1. 批量退群 / 退订频道
    # ==========================================
    async def batch_leave_chats(
        self,
        leave_channels: bool = True,
        leave_groups: bool = True,
        exclude_created: bool = True,
        min_delay: float = 3.0,
        max_delay: float = 8.0
    ) -> Dict[str, int]:
        """
        批量退出已加入的无用群组和频道（释放 500 个加入上限配额）
        :param leave_channels: 是否退出广播频道
        :param leave_groups: 是否退出超级群/普通群
        :param exclude_created: 是否保留自己创建的群/频道（不退出自己的群）
        """
        print("[*] 正在检索当前账号加入的全部对话与群组...")
        dialogs = await self.client.get_dialogs()
        
        targets = []
        for d in dialogs:
            if not d.is_channel and not d.is_group:
                continue

            entity = d.entity
            # 过滤自己创建的群组/频道
            if exclude_created and getattr(entity, 'creator', False):
                continue

            is_broadcast_channel = getattr(entity, 'broadcast', False)
            if is_broadcast_channel and leave_channels:
                targets.append((d.title, entity))
            elif not is_broadcast_channel and leave_groups:
                targets.append((d.title, entity))

        total = len(targets)
        print(f"[+] 找到待退出目标共 {total} 个，开始执行批量退订...")

        stats = {"success": 0, "failed": 0}

        for idx, (title, entity) in enumerate(targets, start=1):
            try:
                if isinstance(entity, Channel):
                    await self.client(LeaveChannelRequest(entity))
                elif isinstance(entity, Chat):
                    await self.client(DeleteChatUserRequest(entity.id, 'me'))

                stats["success"] += 1
                print(f"  [{idx}/{total}] 🚪 成功退出: {title}")

            except FloodWaitError as e:
                print(f"  [!] 触发限流，休眠 {e.seconds} 秒...")
                await asyncio.sleep(e.seconds + 2)
                stats["failed"] += 1
            except Exception as err:
                print(f"  [{idx}/{total}] ❌ 退出失败 ({title}): {err}")
                stats["failed"] += 1

            delay = random.uniform(min_delay, max_delay)
            await asyncio.sleep(delay)

        print(f"\n[✓] 批量退群完成: 成功 {stats['success']} 个, 失败 {stats['failed']} 个。")
        return stats

    # ==========================================
    # 2. @SpamBot 账号风控与解封时间检测
    # ==========================================
    async def check_spambot_status(self) -> Dict[str, Any]:
        """
        向官方 @SpamBot 发送查询，获取账号当前的限制状态与解封时间
        """
        print("[*] 正在向官方 @SpamBot 发送风控检测请求...")
        spambot_peer = "SpamBot"

        try:
            # 启动对话发送 /start
            await self.client.send_message(spambot_peer, "/start")
            await asyncio.sleep(2.5)  # 等待机器人回复

            # 获取 SpamBot 最新回复
            messages = await self.client.get_messages(spambot_peer, limit=2)
            if not messages:
                return {"status": "unknown", "detail": "未收到 SpamBot 回复"}

            reply_text = messages[0].text
            print(f"\n🤖 SpamBot 官方回复原文:\n{reply_text}\n")

            # 智能解析回复判定状态
            if "Good news" in reply_text or "no limits" in reply_text or "自由" in reply_text or "没有任何限制" in reply_text:
                return {
                    "status": "clean",
                    "is_limited": False,
                    "detail": "账号完全正常，无任何限制 (Good news, no limits)"
                }
            elif "unfortunately" in reply_text or "limited" in reply_text or "限制" in reply_text:
                # 尝试正则提取解封时间（如 25 Aug 2026 或 UTC 时间）
                date_match = re.search(r"(\d{1,2}\s+[A-Za-z]{3}\s+\d{4},\s+\d{2}:\d{2}\s+UTC)", reply_text)
                unban_time = date_match.group(1) if date_match else "未明确具体时间"
                return {
                    "status": "limited",
                    "is_limited": True,
                    "unban_date": unban_time,
                    "detail": f"账号处于限制状态 (Spamblock)，预计解封时间: {unban_time}"
                }
            else:
                return {
                    "status": "custom",
                    "is_limited": True,
                    "detail": reply_text
                }

        except Exception as e:
            return {"status": "error", "is_limited": False, "detail": f"检测失败: {str(e)}"}
