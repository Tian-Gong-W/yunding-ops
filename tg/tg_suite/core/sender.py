import re
import random
import asyncio
import time
from typing import List, Dict, Optional, Any, Callable
from telethon import TelegramClient, events
from telethon.tl.types import InputPeerUser, InputUser
from telethon.errors import (
    FloodWaitError,
    PeerFloodError,
    UserPrivacyRestrictedError,
    UserIsBlockedError,
    UsernameNotOccupiedError,
    UserDeactivatedError
)

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database

class SpintaxParser:
    """Spintax 智能话术随机混淆引擎"""

    EMOJI_LIST = ["👍", "🔥", "✨", "🚀", "💡", "🎯", "⭐", "🎉", "🌟", "📈", "🤝", "💎"]

    @classmethod
    def parse(cls, text: str, user_info: Optional[Dict[str, Any]] = None) -> str:
        """
        递归解析 Spintax 语法，例如: {您好|Hi|Hello}，{请问|想了解下}这个{项目|方案}吗？
        并替换动态变量: {first_name}, {username}, {random_emoji}, {random_num}
        """
        if not text:
            return ""

        # 1. 递归处理嵌套的大括号 Spintax
        pattern = re.compile(r"\{([^{}]+)\}")
        while True:
            match = pattern.search(text)
            if not match:
                break
            options = match.group(1).split("|")
            chosen = random.choice(options)
            text = text[:match.start()] + chosen + text[match.end():]

        # 2. 动态变量占位符替换
        if user_info:
            first_name = user_info.get("first_name") or "朋友"
            username = user_info.get("username") or ""
            text = text.replace("{first_name}", first_name)
            text = text.replace("{name}", first_name)
            text = text.replace("{username}", f"@{username}" if username else first_name)
        
        # 3. 随机扰动变量
        text = text.replace("{random_emoji}", random.choice(cls.EMOJI_LIST))
        text = text.replace("{random_num}", str(random.randint(100, 999)))

        return text.strip()


class TelegramMassSender:
    """多账号轮播 Spintax 私信群发引擎"""

    def __init__(
        self,
        clients: List[TelegramClient],
        min_delay: int = 20,
        max_delay: int = 45,
        max_per_account: int = 25
    ):
        self.clients = clients
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.max_per_account = max_per_account

        self.account_stats: Dict[int, Dict[str, Any]] = {}
        for c in self.clients:
            self.account_stats[id(c)] = {
                "cooldown_until": 0.0,
                "sent_count": 0,
                "is_banned": False
            }

    def _get_next_available_client(self) -> Optional[TelegramClient]:
        now = time.time()
        for client in self.clients:
            cid = id(client)
            stat = self.account_stats[cid]
            if stat["is_banned"]:
                continue
            if stat["sent_count"] >= self.max_per_account:
                continue
            if now < stat["cooldown_until"]:
                continue
            return client
        return None

    async def send_mass_dm(
        self,
        user_list: List[Dict[str, Any]],
        spintax_template: str,
        media_file_path: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> Dict[str, int]:
        """
        向目标受众列表批量发送私信
        :param user_list: 目标用户列表
        :param spintax_template: 带 Spintax 语法的模板
        :param media_file_path: 可选图片/文件附件路径
        """
        stats = {
            "success": 0,
            "blocked": 0,
            "privacy": 0,
            "failed": 0
        }

        total = len(user_list)
        print(f"[*] 启动多账号私信群发任务: 共 {len(self.clients)} 个工作号，目标受众 {total} 人")

        for idx, u in enumerate(user_list, start=1):
            client = self._get_next_available_client()
            if not client:
                print("\n[!] 所有账号均已达到单日发送上限或处于冷却期，群发任务安全暂停。")
                break

            cid = id(client)
            user_id = u["user_id"]
            access_hash = u.get("access_hash")
            username = u.get("username")
            display_name = f"@{username}" if username else f"ID:{user_id}"

            # 动态生成该用户专属的差异化文案
            personalized_msg = SpintaxParser.parse(spintax_template, u)

            try:
                # 获取目标 Entity
                if access_hash:
                    input_user = InputUser(user_id=user_id, access_hash=int(access_hash))
                elif username:
                    input_user = await client.get_input_entity(username)
                else:
                    input_user = await client.get_input_entity(user_id)

                # 发送消息 (带图片或纯文本)
                if media_file_path and os.path.exists(media_file_path):
                    await client.send_file(input_user, media_file_path, caption=personalized_msg)
                else:
                    await client.send_message(input_user, personalized_msg)

                stats["success"] += 1
                self.account_stats[cid]["sent_count"] += 1
                curr_sent = self.account_stats[cid]["sent_count"]
                print(f"  [{idx}/{total}] 📨 成功私信: {display_name} (当前账号已发: {curr_sent}/{self.max_per_account})")
                print(f"      文案预览: \"{personalized_msg[:45]}...\"")

            except UserPrivacyRestrictedError:
                stats["privacy"] += 1
                print(f"  [{idx}/{total}] 🔒 用户隐私设置拒绝陌生人私信: {display_name}")

            except (UserIsBlockedError, UserDeactivatedError):
                stats["blocked"] += 1
                print(f"  [{idx}/{total}] 🚫 用户已注销或已拉黑: {display_name}")

            except FloodWaitError as e:
                wait_sec = e.seconds
                print(f"  [!] 当前账号触发 FloodWait，休眠 {wait_sec} 秒，自动换号...")
                self.account_stats[cid]["cooldown_until"] = time.time() + wait_sec + 10
                stats["failed"] += 1

            except PeerFloodError:
                print(f"  [!] 当前账号触发 PeerFlood (限制向陌生人发信)，自动置为封存...")
                self.account_stats[cid]["is_banned"] = True
                stats["failed"] += 1

            except Exception as err:
                stats["failed"] += 1
                print(f"  [{idx}/{total}] ❌ 发送失败 ({display_name}): {err}")

            if progress_callback:
                progress_callback(idx, total, f"成功:{stats['success']} 失败:{stats['failed']}")

            # 随机休眠
            delay = random.uniform(self.min_delay, self.max_delay)
            print(f"  ⏳ 随机休眠 {delay:.1f} 秒...")
            await asyncio.sleep(delay)

        print("\n" + "=" * 45)
        print("  🎉 私信群发任务总结报告")
        print(f"  • 成功触达: {stats['success']} 人")
        print(f"  • 隐私拦截: {stats['privacy']} 人")
        print(f"  • 被拉黑/注销: {stats['blocked']} 人")
        print(f"  • 其他失败: {stats['failed']} 人")
        print("=" * 45)
        return stats


class TelegramAutoResponder:
    """关键词自动回复监听器"""

    def __init__(self, client: TelegramClient, rules: Dict[str, str], ai_callback: Optional[Callable[[str, Dict[str, Any]], Any]] = None):
        """
        :param rules: 规则字典，例如 {"价格": "我们的报价单是...", "怎么买": "请联系客服微信..."}
        """
        self.client = client
        self.rules = rules
        self.ai_callback = ai_callback

    def start(self):
        @self.client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
        async def handler(event):
            text = event.raw_text.strip()
            sender = await event.get_sender()

            if self.ai_callback:
                user_info = {
                    "first_name": getattr(sender, "first_name", ""),
                    "username": getattr(sender, "username", ""),
                }
                reply_msg = await self.ai_callback(text, user_info)
                await event.reply(reply_msg)
                print(f"[🤖 AI 自动应答] 已回复用户 @{getattr(sender, 'username', '') or getattr(sender, 'id', '')}")
                return
            
            for keyword, reply_template in self.rules.items():
                if keyword in text:
                    user_info = {"first_name": getattr(sender, 'first_name', ''), "username": getattr(sender, 'username', '')}
                    reply_msg = await self.ai_callback(text, user_info) if self.ai_callback else SpintaxParser.parse(reply_template, user_info)
                    await event.reply(reply_msg)
                    print(f"[🤖 自动应答] 捕获关键词 [{keyword}]，已回复用户 @{sender.username or sender.id}")
                    break
