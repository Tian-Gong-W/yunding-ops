import re
import asyncio
import json
from datetime import datetime
from typing import List, Dict, Optional, Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from telethon import TelegramClient, events
from telethon.tl.types import User, Channel, Chat

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database

async def _send_bot_notification(text: str) -> None:
    """通过已配置的 Telegram Bot 目标发送通知，不阻塞监听事件循环。"""
    targets = [target for target in database.list_bot_targets() if target.get("enabled")]
    if not targets:
        return

    def send_one(target):
        token = target.get("bot_token", "")
        chat_id = target.get("chat_id", "")
        if not token or not chat_id:
            return
        payload = json.dumps({"chat_id": chat_id, "text": text, "disable_web_page_preview": True}).encode()
        request = Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=12) as response:
                response.read()
        except Exception as exc:
            print(f"[!] Bot 通知失败 [{target.get('name', 'unknown')}]: {exc}")

    await asyncio.gather(*(asyncio.to_thread(send_one, target) for target in targets))


class KeywordInterceptor:
    """全网群组关键词实时监听与商机拦截引擎"""

    def __init__(
        self,
        client: TelegramClient,
        keywords: List[str],
        exclude_words: Optional[List[str]] = None,
        forward_target_peer: Optional[str] = None,
        account_name: Optional[str] = None,
        archive_all: bool = True,
    ):
        """
        :param client: 用于监听的工作账号
        :param keywords: 监控的关键词列表，例如 ["求购", "多少钱", "买", "需要", "找"]
        :param exclude_words: 排除词列表（过滤无关杂音），例如 ["已出", "招聘", "广告"]
        :param forward_target_peer: 商机接收目标（可以是客服账号 @username、私聊 ID 或内部销售工作群）
        """
        self.client = client
        self.keywords = [k.strip() for k in keywords if k.strip()]
        self.exclude_words = [e.strip() for e in (exclude_words or []) if e.strip()]
        self.forward_target_peer = forward_target_peer
        self.account_name = account_name or ""
        self.archive_all = archive_all
        self.forward_entity = None
        self.monitored_chats: List[Any] = []
        self.lead_count = 0

    async def init_forward_target(self):
        """初始化商机接收目标 Entity"""
        if self.forward_target_peer:
            try:
                self.forward_entity = await self.client.get_entity(self.forward_target_peer)
                target_title = getattr(self.forward_entity, 'title', self.forward_target_peer)
                print(f"[+] 商机通知目标已锁定: {target_title}")
            except Exception as e:
                print(f"[!] 无法解析商机接收目标 [{self.forward_target_peer}]: {e}")

    def _match_keywords(self, text: str, sender_username: str = "") -> Optional[str]:
        """按持久化规则匹配消息，兼容旧版关键词列表。"""
        if not text:
            return None
        rules = database.list_keyword_rules(self.account_name)
        if not rules:
            rules = [
                {"keyword_value": kw, "match_mode": "contains", "exclude_words": ",".join(self.exclude_words),
                 "sender_filter": "", "action": "monitor", "case_sensitive": False, "enabled": True}
                for kw in self.keywords
            ]
        import fnmatch
        for rule in rules:
            if not rule.get("enabled", True) or rule.get("action") == "exclude":
                continue
            source = text if rule.get("case_sensitive") else text.lower()
            value = str(rule.get("keyword_value", ""))
            target = value if rule.get("case_sensitive") else value.lower()
            excludes = [x.strip() for x in str(rule.get("exclude_words", "")).replace("，", ",").split(",") if x.strip()]
            if any((x if rule.get("case_sensitive") else x.lower()) in source for x in excludes):
                continue
            sender_filter = str(rule.get("sender_filter", "")).strip()
            if sender_filter and sender_filter.lstrip("@").lower() not in sender_username.lower():
                continue
            mode = rule.get("match_mode", "contains")
            try:
                if mode == "exact": matched = source == target
                elif mode == "regex": matched = bool(re.search(value, text, 0 if rule.get("case_sensitive") else re.IGNORECASE))
                elif mode == "fuzzy": matched = all(part.strip().lower() in text.lower() for part in value.replace("?", "|").split("|") if part.strip())
                else: matched = target in source
            except re.error:
                matched = False
            if matched:
                return value
        return None

    async def start_listening(self, target_groups: Optional[List[str]] = None):
        """
        启动后台监听循环
        :param target_groups: 指定监听的目标群组链接/ID列表（若为空则监听该账号加入的所有群）
        """
        await self.init_forward_target()

        chat_entities = []
        if target_groups:
            print(f"[*] 正在载入 {len(target_groups)} 个指定监控群组...")
            for g in target_groups:
                try:
                    ent = await self.client.get_entity(g)
                    chat_entities.append(ent)
                    print(f"  ✓ 成功挂载监听: {getattr(ent, 'title', g)}")
                except Exception as err:
                    print(f"  ✗ 挂载群组失败 ({g}): {err}")
        else:
            print("[*] 正在监听当前账号已加入的全部群组...")

        chats_filter = chat_entities if chat_entities else None

        @self.client.on(events.NewMessage(chats=chats_filter))
        async def message_handler(event):
            # 过滤私聊消息，只监控群聊/频道
            if event.is_private:
                return

            text = event.raw_text
            sender = await event.get_sender()
            chat = await event.get_chat()
            matched_kw = self._match_keywords(text, getattr(sender, "username", "") or "")
            if not matched_kw and not self.archive_all:
                return

            # 过滤机器人发的广告
            if isinstance(sender, User) and sender.bot:
                return

            sender_name = f"{getattr(sender, 'first_name', '')} {getattr(sender, 'last_name', '')}".strip() or "匿名用户"
            username = getattr(sender, 'username', '')
            sender_id = sender.id if sender else 0
            group_title = getattr(chat, 'title', '未知群组')
            group_username = getattr(chat, 'username', '')
            
            # 构造消息直达链接
            msg_link = ""
            if group_username:
                msg_link = f"https://t.me/{group_username}/{event.id}"
            
            self.lead_count += 1
            now_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            database.save_archived_message({
                "account_name": self.account_name, "chat_id": getattr(chat, "id", 0),
                "chat_type": type(chat).__name__, "chat_title": group_title,
                "chat_username": group_username, "message_id": event.id,
                "sender_id": sender_id, "sender_name": sender_name,
                "sender_username": username, "text": text,
                "is_outgoing": int(bool(getattr(event, "out", False))),
                "matched_keyword": matched_kw, "message_date": str(getattr(event, "date", now_time)),
            })
            if not matched_kw:
                return

            print("\n" + "🔥" * 20)
            print(f"【⚡ 截获新商机 #{self.lead_count}】 时间: {now_time}")
            print(f"  📍 来源群组: {group_title}")
            print(f"  👤 客户名称: {sender_name} (@{username or '无用户名'})")
            print(f"  🔑 命中关键词: [{matched_kw}]")
            print(f"  💬 原始需求: \"{text[:120]}...\"")
            if msg_link:
                print(f"  🔗 消息链接: {msg_link}")
            print("🔥" * 20)

            # 1. 存入数据库
            lead_record = {
                "group_title": group_title,
                "group_link": f"https://t.me/{group_username}" if group_username else "",
                "sender_id": sender_id,
                "sender_name": sender_name,
                "sender_username": username,
                "matched_keyword": matched_kw,
                "message_text": text,
                "message_link": msg_link
            }
            database.save_intercepted_lead(lead_record)
            await _send_bot_notification(
                f"🎯 发现潜在客户商机\n\n命中关键词: {matched_kw}\n来源群组: {group_title}\n"
                f"客户: {sender_name} (@{username or '无用户名'})\n\n{text[:120]}"
                + (f"\n\n消息链接: {msg_link}" if msg_link else "")
            )

            # 2. 实时推送给监听账号的内部销售/工作群
            if self.forward_entity:
                alert_card = (
                    f"🎯 **发现潜在客户商机！**\n\n"
                    f"• **命中关键词**：`{matched_kw}`\n"
                    f"• **来源群组**：`{group_title}`\n"
                    f"• **客户信息**：{sender_name} (" + (f"@{username}" if username else f"ID: `{sender_id}`") + ")\n"
                    f"• **时间**：`{now_time}`\n\n"
                    f"💬 **客户需求原文**：\n> {text}\n\n"
                    + (f"👉 [点击跳转到原始消息]({msg_link})" if msg_link else "")
                )
                try:
                    await self.client.send_message(self.forward_entity, alert_card, link_preview=False)
                except Exception as send_err:
                    print(f"[!] 商机推送发送失败: {send_err}")

        print("\n[✓] 关键词实时监控已启动，正在全天候拦截商机 (按 Ctrl+C 可停止)...")
        await self.client.run_until_disconnected()
