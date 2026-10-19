import sqlite3
import os
import csv
from datetime import datetime
from typing import List, Dict, Optional, Any

DB_PATH = os.environ.get("TG_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "tg_data.db"))

def get_db_connection():
    """获取 SQLite 数据库连接"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """初始化数据库表结构"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. 账号表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        phone TEXT,
        dc_id INTEGER DEFAULT 2,
        session_file TEXT NOT NULL,
        proxy TEXT,
        status TEXT DEFAULT 'active',
        flood_until TIMESTAMP,
        daily_invites_count INTEGER DEFAULT 0,
        daily_messages_count INTEGER DEFAULT 0,
        last_used_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    ''')

    # 2. 目标群组/频道表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS target_groups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        link TEXT UNIQUE NOT NULL,
        title TEXT,
        group_id INTEGER,
        access_hash INTEGER,
        member_count INTEGER DEFAULT 0,
        status TEXT DEFAULT 'pending',
        last_scraped_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    ''')

    # 3. 采集到的受众用户池
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS collected_users (
        user_id INTEGER PRIMARY KEY,
        access_hash INTEGER,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        phone TEXT,
        source_group TEXT,
        is_bot BOOLEAN DEFAULT 0,
        is_premium BOOLEAN DEFAULT 0,
        last_seen_type TEXT,
        last_active_at TIMESTAMP,
        messages_count INTEGER DEFAULT 0,
        last_message_text TEXT,
        tags TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    ''')

    # 4. 任务与操作日志表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS task_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_type TEXT NOT NULL,
        account_name TEXT,
        target TEXT,
        status TEXT,
        detail TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    ''')

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_username ON collected_users(username);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_tags ON collected_users(tags);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_source ON collected_users(source_group);")
    
    conn.commit()
    conn.close()

# ================================
# 数据采集与存储 CRUD 接口
# ================================

def save_collected_users(users: List[Dict[str, Any]]) -> Dict[str, int]:
    """批量保存采集的用户（自动去重与更新）"""
    if not users:
        return {"added": 0, "updated": 0}

    conn = get_db_connection()
    cursor = conn.cursor()
    
    added_count = 0
    updated_count = 0

    for u in users:
        user_id = u.get("user_id")
        if not user_id:
            continue

        cursor.execute("SELECT user_id, tags, messages_count FROM collected_users WHERE user_id = ?", (user_id,))
        existing = cursor.fetchone()

        if existing:
            new_msgs = existing["messages_count"] + u.get("messages_count", 0)
            existing_tags = set(existing["tags"].split(",")) if existing["tags"] else set()
            if u.get("tags"):
                existing_tags.update(u["tags"].split(","))
            merged_tags = ",".join(filter(None, existing_tags))

            cursor.execute('''
            UPDATE collected_users SET
                access_hash = COALESCE(?, access_hash),
                username = COALESCE(?, username),
                first_name = COALESCE(?, first_name),
                last_name = COALESCE(?, last_name),
                phone = COALESCE(?, phone),
                is_premium = COALESCE(?, is_premium),
                last_seen_type = COALESCE(?, last_seen_type),
                last_active_at = COALESCE(?, last_active_at),
                messages_count = ?,
                last_message_text = COALESCE(?, last_message_text),
                tags = ?
            WHERE user_id = ?
            ''', (
                u.get("access_hash"),
                u.get("username"),
                u.get("first_name"),
                u.get("last_name"),
                u.get("phone"),
                u.get("is_premium"),
                u.get("last_seen_type"),
                u.get("last_active_at"),
                new_msgs,
                u.get("last_message_text"),
                merged_tags,
                user_id
            ))
            updated_count += 1
        else:
            cursor.execute('''
            INSERT INTO collected_users (
                user_id, access_hash, username, first_name, last_name, phone,
                source_group, is_bot, is_premium, last_seen_type, last_active_at,
                messages_count, last_message_text, tags
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                u.get("user_id"),
                u.get("access_hash"),
                u.get("username"),
                u.get("first_name"),
                u.get("last_name"),
                u.get("phone"),
                u.get("source_group"),
                1 if u.get("is_bot") else 0,
                1 if u.get("is_premium") else 0,
                u.get("last_seen_type", "Unknown"),
                u.get("last_active_at"),
                u.get("messages_count", 0),
                u.get("last_message_text"),
                u.get("tags", "member")
            ))
            added_count += 1

    conn.commit()
    conn.close()
    return {"added": added_count, "updated": updated_count}

def query_collected_users(
    tag: Optional[str] = None,
    source_group: Optional[str] = None,
    only_active: bool = False,
    limit: int = 1000
) -> List[Dict[str, Any]]:
    """按条件筛选采集到的用户数据"""
    conn = get_db_connection()
    cursor = conn.cursor()

    query = "SELECT * FROM collected_users WHERE is_bot = 0"
    params = []

    if tag:
        query += " AND tags LIKE ?"
        params.append(f"%{tag}%")
    if source_group:
        query += " AND source_group = ?"
        params.append(source_group)
    if only_active:
        query += " AND (last_seen_type IN ('Online', 'Recently', 'LastWeek') OR messages_count > 0)"

    query += f" ORDER BY messages_count DESC, last_active_at DESC LIMIT {limit}"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    results = [dict(r) for r in rows]
    conn.close()
    return results

def get_database_stats() -> Dict[str, Any]:
    """获取用户数据库的统计指标"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM collected_users;")
    total_users = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM collected_users WHERE username IS NOT NULL AND username != '';")
    with_username = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM collected_users WHERE messages_count > 0;")
    active_chatters = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM collected_users WHERE is_premium = 1;")
    premium_users = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT source_group) FROM collected_users;")
    sources_count = cursor.fetchone()[0]

    conn.close()
    return {
        "total_users": total_users,
        "with_username": with_username,
        "active_chatters": active_chatters,
        "premium_users": premium_users,
        "sources_count": sources_count
    }

def export_users_to_csv(filepath: str, users: Optional[List[Dict[str, Any]]] = None) -> str:
    """将采集的用户名单导出为 CSV 文件"""
    if users is None:
        users = query_collected_users(limit=50000)

    keys = [
        "user_id", "access_hash", "username", "first_name", "last_name", 
        "phone", "source_group", "is_premium", "last_seen_type", 
        "messages_count", "last_message_text", "tags"
    ]

    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(users)

    return filepath

# ================================
# 5. 商机线索拦截表与 CRUD
# ================================
def init_leads_table():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS intercepted_leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_title TEXT,
        group_link TEXT,
        sender_id INTEGER,
        sender_name TEXT,
        sender_username TEXT,
        matched_keyword TEXT,
        message_text TEXT,
        message_link TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    ''')
    conn.commit()
    conn.close()

def save_intercepted_lead(lead_data: Dict[str, Any]):
    """保存截获的商机线索"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    INSERT INTO intercepted_leads (
        group_title, group_link, sender_id, sender_name,
        sender_username, matched_keyword, message_text, message_link
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        lead_data.get("group_title"),
        lead_data.get("group_link"),
        lead_data.get("sender_id"),
        lead_data.get("sender_name"),
        lead_data.get("sender_username"),
        lead_data.get("matched_keyword"),
        lead_data.get("message_text"),
        lead_data.get("message_link")
    ))
    conn.commit()
    conn.close()


def get_ai_settings() -> dict:
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS ai_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    rows = cur.execute("SELECT key, value FROM ai_settings").fetchall(); conn.commit(); conn.close()
    return {row["key"]: row["value"] for row in rows}

def save_ai_settings(values: dict) -> None:
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS ai_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    for key, value in values.items():
        if value is not None and str(value).strip():
            cur.execute("INSERT INTO ai_settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value).strip()))
    conn.commit(); conn.close()

init_db()
init_leads_table()


# ================================
# 监控产品化：规则、消息归档、Bot 目标
# ================================
def init_monitor_tables():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.executescript('''
    CREATE TABLE IF NOT EXISTS keyword_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_name TEXT,
        rule_name TEXT NOT NULL,
        keyword_value TEXT NOT NULL,
        match_mode TEXT NOT NULL DEFAULT 'contains',
        exclude_words TEXT DEFAULT '',
        sender_filter TEXT DEFAULT '',
        action TEXT NOT NULL DEFAULT 'monitor',
        case_sensitive INTEGER NOT NULL DEFAULT 0,
        enabled INTEGER NOT NULL DEFAULT 1,
        priority INTEGER NOT NULL DEFAULT 0,
        remark TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_keyword_rules_enabled ON keyword_rules(enabled, priority);
    CREATE TABLE IF NOT EXISTS archived_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_name TEXT,
        chat_id INTEGER,
        chat_type TEXT,
        chat_title TEXT,
        chat_username TEXT,
        message_id INTEGER,
        sender_id INTEGER,
        sender_name TEXT,
        sender_username TEXT,
        text TEXT,
        media_type TEXT,
        is_outgoing INTEGER DEFAULT 0,
        is_edited INTEGER DEFAULT 0,
        is_channel_post INTEGER DEFAULT 0,
        matched_rule_id INTEGER,
        matched_keyword TEXT,
        message_date TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(account_name, chat_id, message_id)
    );
    CREATE INDEX IF NOT EXISTS idx_archived_messages_created ON archived_messages(created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_archived_messages_chat ON archived_messages(chat_id, message_date DESC);
    CREATE TABLE IF NOT EXISTS bot_notify_targets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        bot_token TEXT NOT NULL,
        chat_id TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    ''')
    conn.commit()
    conn.close()


def list_keyword_rules(account_name: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    query = "SELECT * FROM keyword_rules"
    params: List[Any] = []
    if account_name:
        query += " WHERE account_name = ? OR account_name IS NULL"
        params.append(account_name)
    query += " ORDER BY priority DESC, id DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def save_keyword_rule(rule: Dict[str, Any]) -> int:
    conn = get_db_connection()
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    cursor = conn.execute('''
        INSERT INTO keyword_rules
        (account_name, rule_name, keyword_value, match_mode, exclude_words,
         sender_filter, action, case_sensitive, enabled, priority, remark, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        rule.get("account_name"), rule.get("rule_name") or "未命名规则",
        rule.get("keyword_value", ""), rule.get("match_mode", "contains"),
        rule.get("exclude_words", ""), rule.get("sender_filter", ""),
        rule.get("action", "monitor"), 1 if rule.get("case_sensitive") else 0,
        1 if rule.get("enabled", True) else 0, int(rule.get("priority", 0)),
        rule.get("remark", ""), now
    ))
    conn.commit()
    rule_id = cursor.lastrowid
    conn.close()
    return int(rule_id)


def update_keyword_rule(rule_id: int, rule: Dict[str, Any]) -> bool:
    conn = get_db_connection()
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    cursor = conn.execute('''
        UPDATE keyword_rules SET account_name=?, rule_name=?, keyword_value=?,
        match_mode=?, exclude_words=?, sender_filter=?, action=?, case_sensitive=?,
        enabled=?, priority=?, remark=?, updated_at=? WHERE id=?
    ''', (
        rule.get("account_name"), rule.get("rule_name") or "未命名规则",
        rule.get("keyword_value", ""), rule.get("match_mode", "contains"),
        rule.get("exclude_words", ""), rule.get("sender_filter", ""),
        rule.get("action", "monitor"), 1 if rule.get("case_sensitive") else 0,
        1 if rule.get("enabled", True) else 0, int(rule.get("priority", 0)),
        rule.get("remark", ""), now, rule_id
    ))
    conn.commit()
    changed = cursor.rowcount > 0
    conn.close()
    return changed


def delete_keyword_rule(rule_id: int) -> bool:
    conn = get_db_connection()
    cursor = conn.execute("DELETE FROM keyword_rules WHERE id = ?", (rule_id,))
    conn.commit()
    changed = cursor.rowcount > 0
    conn.close()
    return changed


def save_archived_message(message: Dict[str, Any]) -> int:
    conn = get_db_connection()
    cursor = conn.execute('''
        INSERT INTO archived_messages
        (account_name, chat_id, chat_type, chat_title, chat_username, message_id,
         sender_id, sender_name, sender_username, text, media_type, is_outgoing,
         is_edited, is_channel_post, matched_rule_id, matched_keyword, message_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_name, chat_id, message_id) DO UPDATE SET
          text=excluded.text, is_edited=excluded.is_edited,
          matched_rule_id=COALESCE(excluded.matched_rule_id, archived_messages.matched_rule_id),
          matched_keyword=COALESCE(excluded.matched_keyword, archived_messages.matched_keyword)
    ''', tuple(message.get(key) for key in (
        "account_name", "chat_id", "chat_type", "chat_title", "chat_username", "message_id",
        "sender_id", "sender_name", "sender_username", "text", "media_type", "is_outgoing",
        "is_edited", "is_channel_post", "matched_rule_id", "matched_keyword", "message_date"
    )))
    conn.commit()
    message_id = cursor.lastrowid
    conn.close()
    return int(message_id or 0)


def query_archived_messages(page: int = 1, page_size: int = 50, **filters: Any) -> Dict[str, Any]:
    page = max(1, int(page))
    page_size = min(200, max(1, int(page_size)))
    conn = get_db_connection()
    where = []
    params: List[Any] = []
    for column in ("account_name", "chat_id", "sender_id"):
        if filters.get(column) not in (None, ""):
            where.append(f"{column} = ?")
            params.append(filters[column])
    if filters.get("keyword"):
        where.append("(text LIKE ? OR chat_title LIKE ? OR sender_name LIKE ?)")
        value = f"%{filters['keyword']}%"
        params.extend([value, value, value])
    condition = " WHERE " + " AND ".join(where) if where else ""
    total = conn.execute(f"SELECT COUNT(*) FROM archived_messages{condition}", params).fetchone()[0]
    params.extend([page_size, (page - 1) * page_size])
    rows = conn.execute(
        f"SELECT * FROM archived_messages{condition} ORDER BY id DESC LIMIT ? OFFSET ?", params
    ).fetchall()
    conn.close()
    return {"total": total, "page": page, "page_size": page_size, "items": [dict(row) for row in rows]}


def list_bot_targets() -> List[Dict[str, Any]]:
    conn = get_db_connection()
    rows = conn.execute("SELECT id, name, bot_token, chat_id, enabled, created_at, updated_at FROM bot_notify_targets ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(row) for row in rows]


def save_bot_target(target: Dict[str, Any]) -> int:
    conn = get_db_connection()
    cursor = conn.execute('''INSERT INTO bot_notify_targets (name, bot_token, chat_id, enabled, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)''', (
        target.get("name") or "未命名 Bot", target.get("bot_token", ""),
        target.get("chat_id", ""), 1 if target.get("enabled", True) else 0
    ))
    conn.commit()
    result = int(cursor.lastrowid)
    conn.close()
    return result


def update_bot_target(target_id: int, target: Dict[str, Any]) -> bool:
    conn = get_db_connection()
    cursor = conn.execute("""UPDATE bot_notify_targets SET name=?, bot_token=COALESCE(NULLIF(?, ''), bot_token), chat_id=?, enabled=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""", (
        target.get("name") or "未命名 Bot", target.get("bot_token", ""), target.get("chat_id", ""),
        1 if target.get("enabled", True) else 0, target_id
    ))
    conn.commit()
    changed = cursor.rowcount > 0
    conn.close()
    return changed


def delete_bot_target(target_id: int) -> bool:
    conn = get_db_connection()
    cursor = conn.execute("DELETE FROM bot_notify_targets WHERE id = ?", (target_id,))
    conn.commit()
    result = cursor.rowcount > 0
    conn.close()
    return result


init_monitor_tables()
