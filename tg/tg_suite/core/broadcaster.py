import asyncio
import random
import time
import re
from collections import defaultdict, deque
from typing import List, Dict, Optional, Any, Callable, Awaitable
from telethon import TelegramClient, events
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import GetDiscussionMessageRequest
from telethon.errors import FloodWaitError, ChatWriteForbiddenError

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.sender import SpintaxParser

class GroupAutoPoster:
    """群聊自动定时广播与推流引擎"""

    def __init__(
        self,
        client: TelegramClient,
        target_groups: List[str],
        spintax_template: str,
        interval_minutes: int = 60,
        min_group_delay: float = 15.0,
        max_group_delay: float = 35.0,
        media_file: Optional[str] = None
    ):
        self.client = client
        self.target_groups = target_groups
        self.spintax_template = spintax_template
        self.interval_minutes = interval_minutes
        self.min_group_delay = min_group_delay
        self.max_group_delay = max_group_delay
        self.media_file = media_file
        self.is_running = False

    async def start_broadcasting(self):
        """启动定时循环广播任务"""
        self.is_running = True
        round_count = 0

        print(f"[*] 启动群聊自动定时推流任务: 目标群组 {len(self.target_groups)} 个 | 循环间隔: {self.interval_minutes} 分钟")

        while self.is_running:
            round_count += 1
            print(f"\n📢 开始第 {round_count} 轮广播推流...")

            for idx, g in enumerate(self.target_groups, start=1):
                if not self.is_running:
                    break

                try:
                    entity = await self.client.get_entity(g)
                    group_title = getattr(entity, 'title', g)
                    
                    # 动态生成 Spintax 消息
                    msg_text = SpintaxParser.parse(self.spintax_template)

                    if self.media_file and os.path.exists(self.media_file):
                        await self.client.send_file(entity, self.media_file, caption=msg_text)
                    else:
                        await self.client.send_message(entity, msg_text)

                    print(f"  [{idx}/{len(self.target_groups)}] ✅ 成功发送至群: {group_title}")

                except ChatWriteForbiddenError:
                    print(f"  [{idx}/{len(self.target_groups)}] 🚫 群组被全员禁言或仅管理员可发帖: {g}")
                except FloodWaitError as e:
                    print(f"  [!] 触发限流，休眠 {e.seconds} 秒...")
                    await asyncio.sleep(e.seconds + 2)
                except Exception as err:
                    print(f"  [{idx}/{len(self.target_groups)}] ❌ 发送失败 ({g}): {err}")

                delay = random.uniform(self.min_group_delay, self.max_group_delay)
                await asyncio.sleep(delay)

            print(f"\n💤 第 {round_count} 轮推流完成，等待 {self.interval_minutes} 分钟后开始下一轮...")
            await asyncio.sleep(self.interval_minutes * 60)


class ChannelAutoCommenter:
    """频道新帖自动抢评与引流评论引擎"""

    def __init__(
        self,
        client: TelegramClient,
        monitored_channels: List[str],
        comment_template: str,
        delay_seconds: float = 3.0,
        ai_callback: Optional[Callable[[str], Awaitable[str]]] = None,
        keywords: Optional[List[str]] = None,
        cooldown_seconds: float = 60.0,
        max_comments_per_hour: int = 12,
        max_retries: int = 3,
        blocked_terms: Optional[List[str]] = None
    ):
        self.client = client
        self.monitored_channels = monitored_channels
        self.comment_template = comment_template
        self.delay_seconds = delay_seconds
        self.ai_callback = ai_callback
        self.keywords = [k.strip().casefold() for k in (keywords or []) if k.strip()]
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self.max_comments_per_hour = max(1, int(max_comments_per_hour))
        self.max_retries = max(1, min(int(max_retries), 5))
        self.blocked_terms = [k.strip().casefold() for k in (blocked_terms or ["违法", "诈骗", "赌博", "色情", "暴力"]) if k.strip()]
        self._last_comment_at = {}
        self._hourly_comments = defaultdict(deque)
        self._seen_posts = set()

    def _channel_key(self, event):
        return str(getattr(event, "chat_id", None) or getattr(event, "peer_id", "unknown"))

    def _allowed_by_policy(self, event, text: str) -> tuple[bool, str]:
        key = self._channel_key(event)
        now = time.monotonic()
        if getattr(event, "id", None) in self._seen_posts:
            return False, "重复帖子"
        self._seen_posts.add(getattr(event, "id", None))
        if len(self._seen_posts) > 5000:
            self._seen_posts = set(list(self._seen_posts)[-2500:])
        if self.keywords and not any(k in text.casefold() for k in self.keywords):
            return False, "未命中关键词"
        if now - self._last_comment_at.get(key, 0) < self.cooldown_seconds:
            return False, "频道冷却中"
        bucket = self._hourly_comments[key]
        while bucket and now - bucket[0] >= 3600:
            bucket.popleft()
        if len(bucket) >= self.max_comments_per_hour:
            return False, "已达到频道每小时评论上限"
        return True, ""

    def _audit_comment(self, text: str) -> tuple[bool, str]:
        clean = re.sub(r"\s+", " ", (text or "")).strip()
        if not clean: return False, "评论为空"
        if len(clean) < 2 or len(clean) > 500: return False, "评论长度不合规"
        lowered = clean.casefold()
        if any(term in lowered for term in self.blocked_terms): return False, "命中敏感词"
        if clean.count("http://") + clean.count("https://") > 1: return False, "链接过多"
        if len(set(clean)) <= 3: return False, "内容重复度过高"
        return True, clean

    async def _retry(self, operation, label: str):
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try: return await operation()
            except Exception as exc:
                last_error = exc
                if attempt < self.max_retries:
                    wait = min(2 ** (attempt - 1), 8)
                    print(f"  ↻ {label}失败，第 {attempt} 次重试，等待 {wait}s: {exc}")
                    await asyncio.sleep(wait)
        raise last_error

    async def start_monitoring(self):
        """挂机监听频道新消息并自动在讨论组评论"""
        channel_entities = []
        for ch in self.monitored_channels:
            try:
                ent = await self.client.get_entity(ch)
                channel_entities.append(ent)
                print(f"  ✓ 成功挂载频道新帖监听: {getattr(ent, 'title', ch)}")
            except Exception as e:
                print(f"  ✗ 无法解析频道 ({ch}): {e}")

        if not channel_entities:
            print("❌ 没有有效的监听频道。")
            return

        @self.client.on(events.NewMessage(chats=channel_entities))
        async def new_post_handler(event):
            # 只处理频道广播消息
            if not event.is_channel or event.is_group:
                return

            allowed, reason = self._allowed_by_policy(event, event.raw_text or "")
            if not allowed:
                print(f"  ⏭️ 跳过帖子 {event.id}: {reason}")
                return
            print(f"\n🔔 捕获到符合条件的新帖 (ID: {event.id})，准备自动抢评...")
            if self.delay_seconds > 0:
                await asyncio.sleep(self.delay_seconds)

            try:
                async def generate_comment():
                    return await self.ai_callback(event.raw_text) if self.ai_callback else SpintaxParser.parse(self.comment_template)
                comment_text = await self._retry(generate_comment, "AI 评论生成")
                ok, audited = self._audit_comment(comment_text)
                if not ok:
                    print(f"  ⛔ 评论审核拦截: {audited}")
                    return
                discussion = await self._retry(lambda: self.client(GetDiscussionMessageRequest(peer=event.peer_id, msg_id=event.id)), "获取讨论消息")
                async def publish():
                    return await self.client.send_message(entity=discussion.messages[0].peer_id, message=audited, reply_to=discussion.messages[0].id)
                await self._retry(publish, "评论发布")
                key = self._channel_key(event); now = time.monotonic()
                self._last_comment_at[key] = now; self._hourly_comments[key].append(now)
                print(f"  ✅ 成功在评论区发布审核通过的评论: \"{audited[:50]}...\"")
            except Exception as err:
                print(f"  ❌ 评论最终失败（已重试 {self.max_retries} 次）: {err}")

        print("\n[✓] 频道新帖自动抢评监听已启动 (按 Ctrl+C 停止)...")
        await self.client.run_until_disconnected()
