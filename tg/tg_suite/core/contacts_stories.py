import asyncio
import os
from typing import List, Dict, Optional, Any
from telethon import TelegramClient
from telethon.tl.functions.contacts import ImportContactsRequest, DeleteContactsRequest, GetContactsRequest
from telethon.tl.types import InputPhoneContact, InputPrivacyValueAllowAll
from telethon.tl.functions.stories import SendStoryRequest
from telethon.errors import FloodWaitError

class ContactsAndStoriesManager:
    """通讯录批量管理与 Stories 动态故事引擎"""

    def __init__(self, client: TelegramClient):
        self.client = client

    # ==========================================
    # 1. 通讯录批量导入
    # ==========================================
    async def import_phone_contacts(
        self,
        phone_list: List[Dict[str, str]],
        batch_size: int = 50
    ) -> Dict[str, int]:
        """
        批量导入手机号到 Telegram 联系人
        :param phone_list: 手机号字典列表，如 [{'phone': '+8613800000000', 'first_name': '张三'}]
        """
        print(f"[*] 开始批量导入联系人: 共 {len(phone_list)} 个目标号码...")
        stats = {"imported": 0, "failed": 0}

        for i in range(0, len(phone_list), batch_size):
            chunk = phone_list[i : i + batch_size]
            input_contacts = []
            
            for c_idx, item in enumerate(chunk):
                phone = item.get("phone", "").strip()
                fname = item.get("first_name", f"User_{i+c_idx}")
                lname = item.get("last_name", "")
                if phone:
                    input_contacts.append(InputPhoneContact(
                        client_id=i + c_idx,
                        phone=phone,
                        first_name=fname,
                        last_name=lname
                    ))

            if not input_contacts:
                continue

            try:
                res = await self.client(ImportContactsRequest(contacts=input_contacts))
                imported_users = res.imported
                stats["imported"] += len(imported_users)
                print(f"  [+] 成功导入批次: {len(imported_users)} / {len(input_contacts)} 人")
            except FloodWaitError as e:
                print(f"  [!] 导入触发限流，休眠 {e.seconds} 秒...")
                await asyncio.sleep(e.seconds + 2)
            except Exception as err:
                print(f"  [!] 导入批次异常: {err}")
                stats["failed"] += len(input_contacts)

            await asyncio.sleep(3.0)

        print(f"[✓] 联系人导入完成: 成功 {stats['imported']} 人, 失败 {stats['failed']} 人。")
        return stats

    # ==========================================
    # 2. 批量清空通讯录
    # ==========================================
    async def clear_all_contacts(self) -> int:
        """一键清空账号当前的全部联系人"""
        print("[*] 正在读取当前通讯录列表...")
        contacts_res = await self.client(GetContactsRequest(hash=0))
        users = contacts_res.users
        
        if not users:
            print("[i] 通讯录当前为空。")
            return 0

        user_ids = [u.id for u in users]
        print(f"[*] 找到 {len(user_ids)} 个联系人，正在批量删除...")

        try:
            await self.client(DeleteContactsRequest(id=user_ids))
            print(f"[✓] 成功清空 {len(user_ids)} 个联系人！")
            return len(user_ids)
        except Exception as e:
            print(f"[❌] 清空联系人失败: {e}")
            return 0

    # ==========================================
    # 3. 动态故事发布 (Stories)
    # ==========================================
    async def publish_story(
        self,
        media_path: str,
        caption: str = "",
        period_hours: int = 24
    ) -> bool:
        """
        为账号发布 24 小时动态故事 (Stories)
        :param media_path: 本地图片或视频文件路径
        :param caption: 故事附带文本/链接文案
        :param period_hours: 故事有效时长（默认 24 小时，可选 6, 12, 24, 48）
        """
        if not os.path.exists(media_path):
            raise FileNotFoundError(f"媒体文件不存在: {media_path}")

        print(f"[*] 正在上传媒体并发布动态故事: {media_path}...")
        try:
            # 1. 上传媒体文件
            uploaded_media = await self.client.upload_file(media_path)
            
            # 2. 发布公开 Stories
            period_seconds = period_hours * 3600
            await self.client(SendStoryRequest(
                peer="me",
                media=uploaded_media,
                caption=caption,
                privacy_rules=[InputPrivacyValueAllowAll()],
                period=period_seconds
            ))
            print(f"✅ 动态故事发布成功！(有效期: {period_hours} 小时)")
            return True
        except Exception as err:
            print(f"❌ 故事发布失败: {err}")
            return False
