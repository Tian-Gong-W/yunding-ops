import asyncio
import random
import time
from typing import List, Dict, Optional, Any, Callable
from telethon import TelegramClient
from telethon.tl.functions.channels import InviteToChannelRequest
from telethon.tl.functions.messages import AddChatUserRequest
from telethon.tl.types import InputPeerUser, InputUser
from telethon.errors import (
    FloodWaitError,
    UserPrivacyRestrictedError,
    UserNotMutualContactError,
    UserChannelsTooMuchError,
    UserAlreadyParticipantError,
    PeerFloodError,
    UserKickedError,
    UserBannedInChannelError
)

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database

class TelegramInviter:
    """多账号矩阵轮换拉群引擎"""

    def __init__(
        self,
        clients: List[TelegramClient],
        min_delay: int = 25,
        max_delay: int = 55,
        max_per_account: int = 15
    ):
        """
        :param clients: 用于拉人的账号客户端列表 (已连接并登录)
        :param min_delay: 单次拉人最小随机延迟（秒）
        :param max_delay: 单次拉人最大随机延迟（秒）
        :param max_per_account: 单号单轮最大拉人数（达到后自动休眠换号）
        """
        self.clients = clients
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.max_per_account = max_per_account
        
        # 账号状态追踪: {client_id: {"cooldown_until": float, "invited_count": int, "is_banned": bool}}
        self.account_stats: Dict[int, Dict[str, Any]] = {}
        for c in self.clients:
            self.account_stats[id(c)] = {
                "cooldown_until": 0.0,
                "invited_count": 0,
                "is_banned": False
            }

    def _get_next_available_client(self) -> Optional[TelegramClient]:
        """轮询获取下一个可用且未超限的账号"""
        now = time.time()
        for client in self.clients:
            cid = id(client)
            stat = self.account_stats[cid]
            if stat["is_banned"]:
                continue
            if stat["invited_count"] >= self.max_per_account:
                continue
            if now < stat["cooldown_until"]:
                continue
            return client
        return None

    async def execute_batch_invite(
        self,
        target_group_link: str,
        user_list: List[Dict[str, Any]],
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> Dict[str, int]:
        """
        执行多账号轮换拉人主流程
        :param target_group_link: 目标群链接或 @username
        :param user_list: 待拉入群的用户字典列表
        """
        clean_group = target_group_link.strip().replace("https://t.me/", "").replace("t.me/", "")
        
        # 首先用第一个有效账号解析目标群 Entity
        first_client = self.clients[0]
        group_entity = await first_client.get_entity(clean_group)
        group_title = getattr(group_entity, "title", clean_group)

        print(f"[*] 启动多账号拉群任务 ➔ 目标群: {group_title}")
        print(f"[*] 参与作业账号: {len(self.clients)} 个 | 待拉用户总数: {len(user_list)}")

        stats = {
            "success": 0,
            "privacy_restricted": 0,
            "already_in_group": 0,
            "channels_too_much": 0,
            "failed": 0
        }

        total = len(user_list)

        for idx, u in enumerate(user_list, start=1):
            client = self._get_next_available_client()
            if not client:
                print("\n[!] 所有账号均已达到单日拉人上限或处于冷却期，拉群任务自动安全暂停。")
                break

            cid = id(client)
            user_id = u["user_id"]
            access_hash = u.get("access_hash")
            username = u.get("username")
            display_name = f"@{username}" if username else f"ID:{user_id}"

            try:
                # 构造目标 InputUser
                if access_hash:
                    input_user = InputUser(user_id=user_id, access_hash=int(access_hash))
                elif username:
                    input_user = await client.get_input_entity(username)
                else:
                    input_user = await client.get_input_entity(user_id)

                # 刷新群实体并在当前账号下发起邀请
                current_group = await client.get_input_entity(clean_group)
                await client(InviteToChannelRequest(channel=current_group, users=[input_user]))

                stats["success"] += 1
                self.account_stats[cid]["invited_count"] += 1
                curr_acc_count = self.account_stats[cid]["invited_count"]
                print(f"  [{idx}/{total}] ✅ 成功拉入: {display_name} (当前账号累计: {curr_acc_count}/{self.max_per_account})")

            except UserAlreadyParticipantError:
                stats["already_in_group"] += 1
                print(f"  [{idx}/{total}] ℹ️ 用户已在群内: {display_name}")

            except UserPrivacyRestrictedError:
                stats["privacy_restricted"] += 1
                print(f"  [{idx}/{total}] 🔒 用户开启了隐私限制(拒绝拉群): {display_name}")

            except UserChannelsTooMuchError:
                stats["channels_too_much"] += 1
                print(f"  [{idx}/{total}] ⚠️ 用户加入群组数量已达上限: {display_name}")

            except FloodWaitError as e:
                wait_sec = e.seconds
                print(f"  [!] 当前账号触发 FloodWait，自动休眠 {wait_sec} 秒，切换下一账号...")
                self.account_stats[cid]["cooldown_until"] = time.time() + wait_sec + 10
                stats["failed"] += 1

            except PeerFloodError:
                print(f"  [!] 当前账号触发 PeerFlood (拉人受限)，自动置为封存状态...")
                self.account_stats[cid]["is_banned"] = True
                stats["failed"] += 1

            except Exception as err:
                stats["failed"] += 1
                print(f"  [{idx}/{total}] ❌ 拉入失败 ({display_name}): {err}")

            if progress_callback:
                progress_callback(idx, total, f"成功:{stats['success']} 隐私限制:{stats['privacy_restricted']}")

            # 随机休眠防风控
            delay = random.uniform(self.min_delay, self.max_delay)
            print(f"  ⏳ 随机休眠 {delay:.1f} 秒后处理下一个目标...")
            await asyncio.sleep(delay)

        print("\n" + "=" * 45)
        print("  🎉 拉群任务总结报告")
        print(f"  • 成功拉入: {stats['success']} 人")
        print(f"  • 隐私拦截: {stats['privacy_restricted']} 人")
        print(f"  • 已在群内: {stats['already_in_group']} 人")
        print(f"  • 满群拦截: {stats['channels_too_much']} 人")
        print(f"  • 其他失败: {stats['failed']} 人")
        print("=" * 45)
        return stats
