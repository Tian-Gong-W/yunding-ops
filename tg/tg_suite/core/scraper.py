import os
import asyncio
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Any, Callable
from telethon import TelegramClient
from telethon.tl.types import (
    User,
    UserStatusOnline,
    UserStatusRecently,
    UserStatusLastWeek,
    UserStatusLastMonth,
    UserStatusEmpty,
    Channel,
    Chat
)
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.errors import FloodWaitError, ChannelPrivateError

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database

class TelegramScraper:
    """高精度受众采集引擎"""

    def __init__(self, client: TelegramClient):
        self.client = client

    @staticmethod
    def _parse_user_status(status) -> str:
        """解析 Telegram 用户最近在线状态"""
        if isinstance(status, UserStatusOnline):
            return "Online"
        elif isinstance(status, UserStatusRecently):
            return "Recently"
        elif isinstance(status, UserStatusLastWeek):
            return "LastWeek"
        elif isinstance(status, UserStatusLastMonth):
            return "LastMonth"
        else:
            return "LongTimeAgo"

    async def get_group_entity(self, group_link: str):
        """解析群组实体（支持 @username, t.me/joinchat/+xxxx 等格式）"""
        clean = group_link.strip()
        if "t.me/" in clean:
            clean = clean.split("t.me/")[-1]
        
        try:
            entity = await self.client.get_entity(clean)
            return entity
        except Exception as e:
            raise ValueError(f"无法解析目标群组 [{group_link}]: {str(e)}")

    # ==========================================
    # 1. 全量群成员采集
    # ==========================================
    async def scrape_group_members(
        self,
        group_link: str,
        limit: int = 5000,
        filter_bots: bool = True,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> List[Dict[str, Any]]:
        """
        采集指定群组的所有成员
        :param group_link: 群链接或 @username
        :param limit: 最大采集人数
        :param filter_bots: 是否过滤机器人
        :param progress_callback: 进度回调函数 (current, total)
        """
        entity = await self.get_group_entity(group_link)
        group_title = getattr(entity, 'title', str(group_link))
        print(f"[*] 开始采集群成员: {group_title} (目标上限: {limit})")

        collected = []
        count = 0

        async for user in self.client.iter_participants(entity, limit=limit):
            if not isinstance(user, User):
                continue
            if filter_bots and (user.bot or user.deleted):
                continue

            status_str = self._parse_user_status(user.status)
            user_data = {
                "user_id": user.id,
                "access_hash": user.access_hash,
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "phone": user.phone,
                "source_group": group_title,
                "is_bot": user.bot,
                "is_premium": getattr(user, 'premium', False),
                "last_seen_type": status_str,
                "last_active_at": None,
                "messages_count": 0,
                "last_message_text": None,
                "tags": "member"
            }
            collected.append(user_data)
            count += 1

            if progress_callback and count % 50 == 0:
                progress_callback(count, limit)

        # 存入数据库
        res = database.save_collected_users(collected)
        print(f"[+] 采集完成! 提取 {len(collected)} 人 (新入库: {res['added']}, 更新: {res['updated']})")
        return collected

    # ==========================================
    # 2. 真实活跃发言者采集（极高价值线索）
    # ==========================================
    async def scrape_active_chatters(
        self,
        group_link: str,
        days_lookback: int = 7,
        max_messages: int = 3000,
        progress_callback: Optional[Callable[[int], None]] = None
    ) -> List[Dict[str, Any]]:
        """
        遍历群聊历史，抓取近期实际发过言的真实活跃用户
        :param group_link: 群链接
        :param days_lookback: 抓取过去几天的发言（默认 7 天）
        :param max_messages: 最大检索消息条数
        """
        entity = await self.get_group_entity(group_link)
        group_title = getattr(entity, 'title', str(group_link))
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_lookback)

        print(f"[*] 开始采集活跃发言者: {group_title} (回溯 {days_lookback} 天, 上限 {max_messages} 条消息)")

        user_activity_map: Dict[int, Dict[str, Any]] = {}
        processed_msgs = 0

        async for msg in self.client.iter_messages(entity, limit=max_messages):
            processed_msgs += 1
            if msg.date < cutoff_date:
                break  # 超出指定天数范围

            if not msg.sender or not isinstance(msg.sender, User):
                continue
            if msg.sender.bot or msg.sender.deleted:
                continue

            u = msg.sender
            u_id = u.id

            if u_id not in user_activity_map:
                status_str = self._parse_user_status(u.status)
                user_activity_map[u_id] = {
                    "user_id": u.id,
                    "access_hash": u.access_hash,
                    "username": u.username,
                    "first_name": u.first_name,
                    "last_name": u.last_name,
                    "phone": u.phone,
                    "source_group": group_title,
                    "is_bot": False,
                    "is_premium": getattr(u, 'premium', False),
                    "last_seen_type": status_str,
                    "last_active_at": str(msg.date),
                    "messages_count": 1,
                    "last_message_text": (msg.text or "")[:80],
                    "tags": "active_chatter"
                }
            else:
                user_activity_map[u_id]["messages_count"] += 1

            if progress_callback and processed_msgs % 200 == 0:
                progress_callback(processed_msgs)

        chatters_list = list(user_activity_map.values())
        # 存入数据库
        res = database.save_collected_users(chatters_list)
        print(f"[+] 活跃发言者采集完毕! 提取活跃用户 {len(chatters_list)} 人 (新入库: {res['added']}, 更新: {res['updated']})")
        return chatters_list

    # ==========================================
    # 3. 频道评论区互动者采集
    # ==========================================
    async def scrape_channel_commenters(
        self,
        channel_link: str,
        recent_posts_count: int = 30
    ) -> List[Dict[str, Any]]:
        """
        采集频道下方绑定的讨论组评论区互动者
        """
        entity = await self.get_group_entity(channel_link)
        full_channel = await self.client(GetFullChannelRequest(entity))
        
        # 检查是否有关联的讨论组 linked_chat_id
        linked_chat_id = full_channel.full_chat.linked_chat_id
        if not linked_chat_id:
            print(f"[-] 频道 [{channel_link}] 未绑定公开讨论组或未开启评论功能。")
            return []

        discussion_group = await self.client.get_entity(linked_chat_id)
        print(f"[+] 发现关联讨论组: {getattr(discussion_group, 'title', linked_chat_id)}")

        # 抓取讨论组内的活跃发言者
        return await self.scrape_active_chatters(
            group_link=str(linked_chat_id),
            days_lookback=30,
            max_messages=recent_posts_count * 50
        )
