import os
import re
import asyncio
import random
from typing import List, Dict, Optional, Any, Callable
from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument, MessageMediaPoll
from telethon.errors import FloodWaitError

class ChannelCloner:
    """频道/群组内容一键克隆与搬运引擎"""

    def __init__(
        self,
        client: TelegramClient,
        min_delay: float = 3.0,
        max_delay: float = 8.0,
        temp_download_dir: str = "./temp_downloads"
    ):
        """
        :param client: 用于读取和发送消息的工作账号
        :param min_delay: 每条消息发布最小间隔（秒）
        :param max_delay: 每条消息发布最大间隔（秒）
        :param temp_download_dir: 媒体文件临时中转目录
        """
        self.client = client
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.temp_download_dir = temp_download_dir
        os.makedirs(self.temp_download_dir, exist_ok=True)

    def _clean_text(self, text: str, replace_rules: Optional[Dict[str, str]] = None) -> str:
        """根据替换规则清洗与重构消息文案（去水印/替换推广链接）"""
        if not text:
            return ""

        result = text
        if replace_rules:
            for old_pattern, new_replacement in replace_rules.items():
                result = re.sub(old_pattern, new_replacement, result)

        return result.strip()

    async def clone_channel_content(
        self,
        source_channel_link: str,
        target_channel_link: str,
        limit: int = 500,
        start_from_msg_id: int = 0,
        replace_rules: Optional[Dict[str, str]] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> Dict[str, int]:
        """
        执行正序完整克隆搬运流程
        :param source_channel_link: 源频道链接或 @username
        :param target_channel_link: 目标接收频道链接或 @username (当前账号须具备管理员发帖权限)
        :param limit: 最大搬运消息条数
        :param start_from_msg_id: 起始消息 ID (支持断点续传)
        :param replace_rules: 文本替换字典，如 {"@old_channel": "@my_channel", "https://t.me/old": "https://t.me/new"}
        """
        src_entity = await self.client.get_entity(source_channel_link)
        dst_entity = await self.client.get_entity(target_channel_link)

        src_title = getattr(src_entity, "title", source_channel_link)
        dst_title = getattr(dst_entity, "title", target_channel_link)

        print(f"[*] 开始克隆频道内容:")
        print(f"  源频道: {src_title} ➔ 目标频道: {dst_title}")
        print(f"  搬运上限: {limit} 条 | 起始消息 ID: {start_from_msg_id}")

        # 1. 获取源频道的历史消息 (由于 iter_messages 默认从新到旧，需要反转为从旧到新的时间顺序)
        print("[*] 正在拉取历史消息索引...")
        messages = []
        async for msg in self.client.iter_messages(src_entity, limit=limit, min_id=start_from_msg_id):
            messages.append(msg)

        # 反转为正序（从旧到新）
        messages.reverse()
        total_msgs = len(messages)
        print(f"[+] 索引完毕，共检索到 {total_msgs} 条历史消息，开始逐条搬运...")

        stats = {
            "success": 0,
            "skipped": 0,
            "failed": 0
        }

        for idx, msg in enumerate(messages, start=1):
            # 跳过服务通知类消息（如置顶、改名等无实际内容的消息）
            if not msg.text and not msg.media:
                stats["skipped"] += 1
                continue

            cleaned_text = self._clean_text(msg.text or "", replace_rules)

            try:
                # 情况 1: 纯文本消息
                if not msg.media:
                    await self.client.send_message(dst_entity, cleaned_text)

                # 情况 2: 图文/视频/文件等富媒体消息 (下载后重新发布，去除“转发自”痕迹)
                elif msg.media:
                    # 如果是投票 (Poll)，Telethon 无法直接下载，可作为特殊处理或直接跳过
                    if isinstance(msg.media, MessageMediaPoll):
                        print(f"  [{idx}/{total_msgs}] ℹ️ 跳过投票类型消息 (ID: {msg.id})")
                        stats["skipped"] += 1
                        continue

                    # 下载媒体到本地中转目录
                    media_path = await self.client.download_media(msg, file=self.temp_download_dir)
                    if media_path and os.path.exists(media_path):
                        await self.client.send_file(
                            dst_entity,
                            file=media_path,
                            caption=cleaned_text,
                            force_document=False
                        )
                        # 发送完毕后清理临时文件
                        try:
                            os.remove(media_path)
                        except OSError:
                            pass
                    else:
                        # 无法下载时尝试发送文本
                        if cleaned_text:
                            await self.client.send_message(dst_entity, cleaned_text)

                stats["success"] += 1
                print(f"  [{idx}/{total_msgs}] ✅ 成功搬运消息 (原ID: {msg.id})")

            except FloodWaitError as e:
                wait_sec = e.seconds
                print(f"  [!] 触发频率限制，休眠 {wait_sec} 秒后重试...")
                await asyncio.sleep(wait_sec + 2)
                stats["failed"] += 1

            except Exception as err:
                stats["failed"] += 1
                print(f"  [{idx}/{total_msgs}] ❌ 搬运失败 (原ID: {msg.id}): {err}")

            if progress_callback:
                progress_callback(idx, total_msgs, f"已搬运:{stats['success']} 失败:{stats['failed']}")

            # 随机休眠
            delay = random.uniform(self.min_delay, self.max_delay)
            await asyncio.sleep(delay)

        print("\n" + "=" * 45)
        print("  🎉 频道克隆任务完成！")
        print(f"  • 成功搬运: {stats['success']} 条")
        print(f"  • 跳过空消息: {stats['skipped']} 条")
        print(f"  • 失败: {stats['failed']} 条")
        print("=" * 45)
        return stats
