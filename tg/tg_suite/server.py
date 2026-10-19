import os
import hmac
import re
import base64
import hashlib
import tempfile
import shutil
import zipfile
import subprocess
import asyncio
import httpx
import time
# 自动加载 .env 配置文件 (纯内置实现，无需第三方库)
env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(env_file):
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from starlette.middleware.gzip import GZipMiddleware
from pydantic import BaseModel
import glob
import json

import database
from core.scraper import TelegramScraper
from core.inviter import TelegramInviter
from core.sender import TelegramMassSender, SpintaxParser, TelegramAutoResponder
from core.cloner import ChannelCloner
from core.interceptor import KeywordInterceptor
from core.account_actions import AccountActionManager
from core.broadcaster import GroupAutoPoster, ChannelAutoCommenter
from core.contacts_stories import ContactsAndStoriesManager

try:
    from telethon import TelegramClient
    from telethon.errors import (
        SessionRevokedError, UserDeactivatedBanError, SessionPasswordNeededError,
        PhoneCodeInvalidError, PhoneCodeExpiredError, PasswordHashInvalidError,
    )
    import socks
    from opentele.td import TDesktop
    from opentele.api import UseCurrentSession
except ImportError:
    pass

security = HTTPBearer(auto_error=False)
TG_ENV = os.getenv("TG_ENV", "development").lower()
# 仅在部署者明确设置 TG_AUTH_DISABLED=true 时开放公共控制台；默认仍保持鉴权。
AUTH_DISABLED = os.getenv("TG_AUTH_DISABLED", "false").lower() == "true"
ADMIN_API_KEY = os.getenv("TG_ADMIN_API_KEY", "")
OPERATOR_API_KEY = os.getenv("TG_OPERATOR_API_KEY", "")
ADMIN_USERNAME = os.getenv("TG_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("TG_ADMIN_PASSWORD", "")
SESSION_SECRET = os.getenv("TG_SESSION_SECRET", "") or ADMIN_API_KEY or hashlib.sha256(
    f"{BASE_DIR if 'BASE_DIR' in globals() else __file__}:tg-suite".encode()
).hexdigest()
SESSION_COOKIE = "tg_admin_session"
SESSION_TTL_SECONDS = int(os.getenv("TG_SESSION_TTL_SECONDS", "43200"))

ACCOUNT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def validate_account_name(account_name: str) -> str:
    """只允许安全的单段账号标识，阻断路径遍历和分隔符。"""
    if not ACCOUNT_NAME_RE.fullmatch(account_name):
        raise HTTPException(status_code=400, detail="账号名称格式无效")
    return account_name


def _create_session_token(role: str) -> str:
    expires = int(time.time()) + SESSION_TTL_SECONDS
    payload = f"{role}:{expires}".encode()
    signature = hmac.new(SESSION_SECRET.encode(), payload, hashlib.sha256).hexdigest().encode()
    return base64.urlsafe_b64encode(payload + b":" + signature).decode()


def _safe_text_equal(left: str, right: str) -> bool:
    """以 UTF-8 字节比较文本，兼容中文账号并保持恒定时间比较。"""
    return hmac.compare_digest((left or "").encode("utf-8"), (right or "").encode("utf-8"))


def _verify_session_token(token: str) -> Optional[str]:
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        role, expires_text, signature = raw.split(":", 2)
        payload = f"{role}:{expires_text}".encode()
        expected = hmac.new(SESSION_SECRET.encode(), payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected) or int(expires_text) < int(time.time()):
            return None
        return role if role in {"admin", "operator"} else None
    except (ValueError, TypeError):
        return None


def _request_role(request: Request, credentials: HTTPAuthorizationCredentials | None = None) -> Optional[str]:
    if AUTH_DISABLED:
        return "admin"
    if credentials and credentials.scheme.lower() == "bearer":
        token = credentials.credentials
        if ADMIN_API_KEY and hmac.compare_digest(token, ADMIN_API_KEY):
            return "admin"
        if OPERATOR_API_KEY and hmac.compare_digest(token, OPERATOR_API_KEY):
            return "operator"
    cookie_token = request.cookies.get(SESSION_COOKIE, "")
    return _verify_session_token(cookie_token) if cookie_token else None


def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str:
    """验证管理员 Cookie 或 Bearer API Key。"""
    role = _request_role(request, credentials)
    if role:
        return role
    raise HTTPException(
        status_code=401,
        detail="需要登录或 Bearer API Key",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_admin(role: str = Depends(require_auth)) -> str:
    """限制账号导入、账号操作和主动营销等高风险接口。"""
    if role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return role



class AuthLoginRequest(BaseModel):
    username: str
    password: str

class PhoneLoginStartRequest(BaseModel):
    phone_number: str
    remark: str = ""

class PhoneLoginCodeRequest(BaseModel):
    account_name: str
    code: str

class PhoneLoginPasswordRequest(BaseModel):
    account_name: str
    password: str

class KeywordRuleRequest(BaseModel):
    account_name: Optional[str] = None
    rule_name: str = "未命名规则"
    keyword_value: str
    match_mode: str = "contains"
    exclude_words: str = ""
    sender_filter: str = ""
    action: str = "monitor"
    case_sensitive: bool = False
    enabled: bool = True
    priority: int = 0
    remark: str = ""

class AICopyRequest(BaseModel):
    topic: str
    context: str = ""
    tone: str = "专业、自然"
    language: str = "中文"
    count: int = 3
    max_tokens: int = 800

class BotTargetRequest(BaseModel):
    name: str = "未命名 Bot"
    bot_token: str
    chat_id: str
    enabled: bool = True


def _ai_setting(name: str, default: str = "") -> str:
    value = os.getenv(name, "").strip()
    if value: return value
    try: return str(database.get_ai_settings().get(name, default)).strip()
    except Exception: return default

app = FastAPI(
    title="Telegram 一体化运营控制台",
    docs_url=None if TG_ENV == "production" else "/docs",
    redoc_url=None if TG_ENV == "production" else "/redoc",
)
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)

@app.get("/api/ai/config")
async def get_ai_config(_: str = Depends(require_auth)):
    """只返回 AI 配置元数据，绝不返回 API Key。"""
    provider = _ai_setting("TG_AI_PROVIDER", "OpenAI-compatible")
    return {
        "configured": bool(_ai_setting("TG_AI_API_KEY") or _ai_setting("MISTRAL_API_KEY")),
        "base_url": _ai_setting("TG_AI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        "model": _ai_setting("TG_AI_MODEL", "gpt-4o-mini"),
        "provider": provider,
        "agent_id": _ai_setting("TG_AI_AGENT_ID", "") if provider.lower() in {"mistral", "mistral_conversations", "mistral-agents"} else "",
    }

@app.post("/api/ai/generate-copy")
async def generate_ai_copy(payload: AICopyRequest, _: str = Depends(require_admin)):
    """调用任意 OpenAI-compatible /chat/completions 接口生成营销文案。"""
    provider = _ai_setting("TG_AI_PROVIDER", "OpenAI-compatible").strip().lower()
    api_key = _ai_setting("TG_AI_COPY_API_KEY") or _ai_setting("TG_AI_API_KEY") or _ai_setting("MISTRAL_API_KEY")
    base_url = _ai_setting("TG_AI_COPY_BASE_URL", _ai_setting("TG_AI_BASE_URL", "https://api.openai.com/v1")).strip().rstrip("/")
    model = _ai_setting("TG_AI_MODEL", "gpt-4o-mini").strip()
    if provider in {"mistral", "mistral_conversations", "mistral-agents"}:
        base_url = _ai_setting("TG_AI_BASE_URL", "https://api.mistral.ai/v1").strip().rstrip("/")
    if not api_key:
        raise HTTPException(status_code=503, detail="尚未配置 TG_AI_API_KEY 或 MISTRAL_API_KEY，请先配置 AI API Key")
    if not payload.topic.strip():
        raise HTTPException(status_code=400, detail="请输入文案主题或原始需求")
    count = max(1, min(int(payload.count), 10))
    max_tokens = max(100, min(int(payload.max_tokens), 4000))
    system = f"你是 Telegram 营销文案助手。输出语言：{payload.language}。语气：{payload.tone}。一次生成 {count} 个不同版本。每个版本独立编号，只输出文案，不要解释。"
    user = f"主题/原始需求：{payload.topic.strip()}\n补充上下文：{payload.context.strip() or '无'}\n请生成适合 Telegram 发布的短文案，避免夸大、诱导和违规承诺。"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if provider in {"mistral", "mistral_conversations", "mistral-agents"}:
        agent_id = _ai_setting("TG_AI_COPY_AGENT_ID", _ai_setting("TG_AI_AGENT_ID", "")).strip()
        agent_version = _ai_setting("TG_AI_COPY_AGENT_VERSION", _ai_setting("TG_AI_AGENT_VERSION", "")).strip()
        if not agent_id:
            raise HTTPException(status_code=503, detail="Mistral 模式需要配置 TG_AI_AGENT_ID")
        body = {"agent_id": agent_id, "inputs": [{"role": "user", "content": f"{system}\n\n{user}"}]}
        if agent_version:
            body["agent_version"] = int(agent_version)
        endpoint = f"{base_url}/conversations"
    else:
        body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "temperature": 0.8, "max_tokens": max_tokens}
        endpoint = f"{base_url}/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=75.0) as client:
            response = await client.post(endpoint, headers=headers, json=body)
        if response.status_code >= 400:
            detail = response.text[:1000]
            raise HTTPException(status_code=502, detail=f"AI 服务返回 {response.status_code}: {detail}")
        data = response.json()
        if provider in {"mistral", "mistral_conversations", "mistral-agents"}:
            content = ""
            for entry in reversed(data.get("outputs", []) or data.get("conversation", {}).get("outputs", []) or data.get("entries", [])):
                if isinstance(entry, dict) and entry.get("type") in {"message.output", "message"}:
                    value = entry.get("content", "")
                    if isinstance(value, list): value = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in value)
                    if value: content = str(value).strip(); break
            if not content:
                content = str(data.get("output", "") or data.get("content", "")).strip()
        else:
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if not content:
            raise HTTPException(status_code=502, detail="AI 服务返回了空内容")
        return {"status": "success", "model": model, "provider": provider, "content": content, "usage": data.get("usage", {})}
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="AI 服务请求超时")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"无法连接 AI 服务: {exc}")

async def _generate_ai_text(prompt: str, context: str = "", purpose: str = "回复") -> str:
    """统一 AI 文本生成，供文案、自动回复、频道评论复用。"""
    provider = _ai_setting("TG_AI_PROVIDER", "OpenAI-compatible").strip().lower()
    api_key = _ai_setting("TG_AI_AUTO_API_KEY") or _ai_setting("TG_AI_API_KEY") or _ai_setting("MISTRAL_API_KEY")
    base_url = _ai_setting("TG_AI_AUTO_BASE_URL", _ai_setting("TG_AI_BASE_URL", "https://api.openai.com/v1")).strip().rstrip("/")
    model = _ai_setting("TG_AI_MODEL", "gpt-4o-mini").strip()
    if provider in {"mistral", "mistral_conversations", "mistral-agents"}:
        base_url = _ai_setting("TG_AI_AUTO_BASE_URL", _ai_setting("TG_AI_BASE_URL", "https://api.mistral.ai/v1")).strip().rstrip("/")
    if not api_key: raise RuntimeError("未配置 AI API Key")
    system = f"你是 Telegram 社区运营助手，负责生成{purpose}。回复简洁、自然、符合上下文，不要编造事实，不要夸大承诺。"
    user = f"上下文：{context or '无'}\n待处理内容：{prompt}\n请只输出最终{purpose}文本。"
    headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json"}
    if provider in {"mistral", "mistral_conversations", "mistral-agents"}:
        agent_id=_ai_setting("TG_AI_AUTO_AGENT_ID", _ai_setting("TG_AI_AGENT_ID", "")).strip()
        if not agent_id: raise RuntimeError("Mistral 模式未配置 TG_AI_AGENT_ID")
        body={"agent_id":agent_id,"inputs":[{"role":"user","content":f"{system}\n\n{user}"}]}
        version=_ai_setting("TG_AI_AUTO_AGENT_VERSION", _ai_setting("TG_AI_AGENT_VERSION", "")).strip()
        if version: body["agent_version"]=int(version)
        endpoint=f"{base_url}/conversations"
    else:
        body={"model":model,"messages":[{"role":"system","content":system},{"role":"user","content":user}],"temperature":0.7,"max_tokens":500}
        endpoint=f"{base_url}/chat/completions"
    async with httpx.AsyncClient(timeout=75.0) as client:
        response=await client.post(endpoint,headers=headers,json=body)
    if response.status_code >= 400: raise RuntimeError(f"AI 服务返回 {response.status_code}: {response.text[:500]}")
    data=response.json()
    if provider in {"mistral", "mistral_conversations", "mistral-agents"}:
        content=""
        entries=data.get("outputs",[]) or data.get("entries",[]) or data.get("conversation",{}).get("outputs",[])
        for entry in reversed(entries):
            if isinstance(entry,dict) and entry.get("type") in {"message.output","message"}:
                value=entry.get("content","")
                if isinstance(value,list): value="".join(x.get("text","") if isinstance(x,dict) else str(x) for x in value)
                if value: content=str(value).strip(); break
        content=content or str(data.get("output","") or data.get("content","")).strip()
    else: content=data.get("choices",[{}])[0].get("message",{}).get("content","").strip()
    if not content: raise RuntimeError("AI 返回空内容")
    return content

@app.get("/api/ai/settings")
async def get_ai_settings(_: str = Depends(require_auth)):
    provider=_ai_setting("TG_AI_PROVIDER", "OpenAI-compatible")
    return {"provider":provider,"base_url":_ai_setting("TG_AI_BASE_URL", "https://api.openai.com/v1"),"model":_ai_setting("TG_AI_MODEL", "gpt-4o-mini"),"agent_id":_ai_setting("TG_AI_AGENT_ID", ""),"agent_version":_ai_setting("TG_AI_AGENT_VERSION", "1"),"configured":bool(_ai_setting("TG_AI_API_KEY") or _ai_setting("MISTRAL_API_KEY"))}

class AISettingsRequest(BaseModel):
    provider: str = "mistral_conversations"
    base_url: str = "https://api.mistral.ai/v1"
    model: str = ""
    api_key: str = ""
    agent_id: str = ""
    agent_version: str = "1"
    copy_api_key: str = ""
    copy_agent_id: str = ""
    copy_agent_version: str = ""
    auto_api_key: str = ""
    auto_agent_id: str = ""
    auto_agent_version: str = ""

@app.post("/api/ai/settings")
async def save_ai_settings(payload: AISettingsRequest, _: str = Depends(require_admin)):
    os.environ["TG_AI_PROVIDER"]=payload.provider.strip() or "mistral_conversations"
    os.environ["TG_AI_BASE_URL"]=payload.base_url.strip().rstrip("/")
    if payload.model.strip(): os.environ["TG_AI_MODEL"]=payload.model.strip()
    if payload.api_key.strip(): os.environ["MISTRAL_API_KEY" if payload.provider.lower().startswith("mistral") else "TG_AI_API_KEY"]=payload.api_key.strip()
    if payload.agent_id.strip(): os.environ["TG_AI_AGENT_ID"]=payload.agent_id.strip()
    values={"TG_AI_PROVIDER":payload.provider,"TG_AI_BASE_URL":payload.base_url,"TG_AI_MODEL":payload.model,"TG_AI_AGENT_ID":payload.agent_id,"TG_AI_AGENT_VERSION":payload.agent_version}
    if payload.api_key.strip(): values["MISTRAL_API_KEY" if payload.provider.lower().startswith("mistral") else "TG_AI_API_KEY"] = payload.api_key
    if payload.copy_agent_id.strip(): values["TG_AI_COPY_AGENT_ID"] = payload.copy_agent_id
    if payload.copy_agent_version.strip(): values["TG_AI_COPY_AGENT_VERSION"] = payload.copy_agent_version
    if payload.copy_api_key.strip(): values["TG_AI_COPY_API_KEY"] = payload.copy_api_key
    if payload.auto_agent_id.strip(): values["TG_AI_AUTO_AGENT_ID"] = payload.auto_agent_id
    if payload.auto_agent_version.strip(): values["TG_AI_AUTO_AGENT_VERSION"] = payload.auto_agent_version
    if payload.auto_api_key.strip(): values["TG_AI_AUTO_API_KEY"] = payload.auto_api_key
    database.save_ai_settings(values)
    for key, value in values.items():
        if value and str(value).strip(): os.environ[key]=str(value).strip()
    return {"status":"success","configured":True,"note":"已保存到服务端数据库；环境变量优先级高于页面配置"}

class AIAutomationRequest(BaseModel):
    account_name: str
    prompt: str = "请自然、简洁地回复对方"
    channels: List[str] = []
    delay_seconds: int = 5
    keywords: List[str] = []
    cooldown_seconds: int = 60
    max_comments_per_hour: int = 12
    max_retries: int = 3
    blocked_terms: List[str] = []

@app.post("/api/ai/reply/{account_name}/start")
async def start_ai_reply(account_name: str, payload: AIAutomationRequest, _: str = Depends(require_admin)):
    validate_account_name(account_name)
    if account_name in ACTIVE_AI_REPLIES: return {"status":"success","message":"AI 自动回复已在运行"}
    client=get_client_for_account(account_name); await client.start()
    async def callback(text, user_info): return await _generate_ai_text(text, str(user_info), "自动回复")
    responder=TelegramAutoResponder(client, {}, ai_callback=callback); responder.start()
    ACTIVE_AI_REPLIES[account_name]={"client":client,"responder":responder}
    return {"status":"success","account_name":account_name,"message":"AI 自动回复已启动"}

@app.post("/api/ai/reply/{account_name}/stop")
async def stop_ai_reply(account_name: str, _: str = Depends(require_admin)):
    runtime=ACTIVE_AI_REPLIES.pop(account_name,None)
    if runtime: await runtime["client"].disconnect()
    return {"status":"success","message":"AI 自动回复已停止"}

@app.post("/api/ai/comment/{account_name}/start")
async def start_ai_comment(account_name: str, payload: AIAutomationRequest, _: str = Depends(require_admin)):
    validate_account_name(account_name)
    if not payload.channels: raise HTTPException(status_code=400, detail="至少填写一个频道")
    if account_name in ACTIVE_AI_COMMENTS: return {"status":"success","message":"AI 评论已在运行"}
    client=get_client_for_account(account_name); await client.start()
    async def callback(post): return await _generate_ai_text(post, payload.prompt, "频道评论")
    commenter=ChannelAutoCommenter(
        client, payload.channels, "", delay_seconds=max(0, payload.delay_seconds), ai_callback=callback,
        keywords=payload.keywords, cooldown_seconds=max(0, payload.cooldown_seconds),
        max_comments_per_hour=max(1, min(payload.max_comments_per_hour, 100)),
        max_retries=max(1, min(payload.max_retries, 5)), blocked_terms=payload.blocked_terms
    )
    task=asyncio.create_task(commenter.start_monitoring())
    ACTIVE_AI_COMMENTS[account_name]={"client":client,"task":task}
    return {"status":"success","account_name":account_name,"message":"AI 评论监听已启动"}

@app.post("/api/ai/comment/{account_name}/stop")
async def stop_ai_comment(account_name: str, _: str = Depends(require_admin)):
    runtime=ACTIVE_AI_COMMENTS.pop(account_name,None)
    if runtime:
        runtime["task"].cancel(); await runtime["client"].disconnect()
    return {"status":"success","message":"AI 评论监听已停止"}

@app.get("/healthz")
async def healthz():
    return {"status": "ok", "service": "telegram-control-plane"}

@app.middleware("http")
async def protect_api_routes(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path != "/api/auth/login":
        credentials = None
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=authorization[7:].strip())
        if not _request_role(request, credentials):
            return JSONResponse({"detail": "需要登录或 Bearer API Key"}, status_code=401)
    return await call_next(request)

@app.middleware("http")
async def add_cache_headers(request: Request, call_next):
    """缓存带版本参数的静态资源，同时避免 HTML/API 被旧内容长期缓存。"""
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    elif path in {"/", "/demo"} or path.endswith(".html"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_DIR = os.path.join(BASE_DIR, "accounts")
SESSIONS_DIR = os.path.join(BASE_DIR, "sessions")
EXPORTS_DIR = os.path.join(BASE_DIR, "exports")
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
MAX_UPLOAD_BYTES = int(os.getenv("TG_MAX_SESSION_UPLOAD_BYTES", str(200 * 1024 * 1024)))
MAX_ZIP_MEMBERS = int(os.getenv("TG_MAX_SESSION_ZIP_MEMBERS", "2000"))
MAX_EXTRACTED_BYTES = int(os.getenv("TG_MAX_SESSION_EXTRACTED_BYTES", str(512 * 1024 * 1024)))

for d in [ACCOUNTS_DIR, SESSIONS_DIR, EXPORTS_DIR, UPLOADS_DIR]:
    os.makedirs(d, exist_ok=True)

try:
    DEFAULT_API_ID = int(os.getenv("TG_API_ID", "0"))
except ValueError as exc:
    raise RuntimeError("TG_API_ID 必须是整数") from exc
DEFAULT_API_HASH = os.getenv("TG_API_HASH", "")

# 全局任务与监听器状态
ACTIVE_INTERCEPTOR = None
ACTIVE_POSTER = None
ACTIVE_MONITORS: Dict[str, Dict[str, Any]] = {}
ACTIVE_AI_REPLIES: Dict[str, Dict[str, Any]] = {}
ACTIVE_AI_COMMENTS: Dict[str, Dict[str, Any]] = {}
LOGIN_RUNTIMES: Dict[str, Dict[str, Any]] = {}

# ================================
# 辅助函数：获取客户端实例
# ================================
def get_client_for_account(acc_name: str) -> TelegramClient:
    validate_account_name(acc_name)
    if DEFAULT_API_ID <= 0 or not DEFAULT_API_HASH:
        raise RuntimeError("未配置 TG_API_ID/TG_API_HASH")
    acc_path = os.path.join(ACCOUNTS_DIR, acc_name)
    proxy_params = None
    proxy_file = os.path.join(acc_path, "proxy.txt")
    if os.path.exists(proxy_file):
        with open(proxy_file, "r", encoding="utf-8") as f:
            p_str = f.read().strip()
            if p_str:
                p_clean = p_str.replace("socks5://", "").replace("http://", "")
                parts = p_clean.split(":")
                if len(parts) >= 2:
                    proxy_params = (socks.SOCKS5, parts[0], int(parts[1]))

    spath = os.path.join(SESSIONS_DIR, acc_name)
    return TelegramClient(spath, DEFAULT_API_ID, DEFAULT_API_HASH, proxy=proxy_params)

async def get_all_active_clients() -> List[TelegramClient]:
    clients = []
    if not os.path.exists(ACCOUNTS_DIR):
        return clients

    for acc_name in os.listdir(ACCOUNTS_DIR):
        acc_path = os.path.join(ACCOUNTS_DIR, acc_name)
        if os.path.isdir(acc_path):
            try:
                c = get_client_for_account(acc_name)
                await c.start()
                clients.append(c)
            except Exception as e:
                print(f"[!] 启动账号 [{acc_name}] 失败: {e}")
    return clients

def _safe_extract_zip(zip_path: Path, destination: Path) -> None:
    """安全解压 TData ZIP，阻断 Zip Slip、符号链接和压缩炸弹。"""
    extracted_bytes = 0
    with zipfile.ZipFile(zip_path) as archive:
        members = archive.infolist()
        if len(members) > MAX_ZIP_MEMBERS:
            raise ValueError("ZIP 文件包含过多条目")

        destination = destination.resolve()
        for member in members:
            member_path = (destination / member.filename).resolve()
            if member_path != destination and destination not in member_path.parents:
                raise ValueError("ZIP 包含非法路径")
            file_type = (member.external_attr >> 16) & 0o170000
            if file_type == 0o120000:
                raise ValueError("ZIP 不允许包含符号链接")
            extracted_bytes += member.file_size
            if extracted_bytes > MAX_EXTRACTED_BYTES:
                raise ValueError("ZIP 解压后超过大小限制")

        for member in members:
            member_path = destination / member.filename
            if member.is_dir():
                member_path.mkdir(parents=True, exist_ok=True)
                continue
            member_path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as src, member_path.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)


def _find_tdata_root(extracted: Path) -> Optional[Path]:
    """只接受根目录或一层包装目录中的 tdata/key_datas 结构。"""
    candidates = [extracted / "tdata"]
    candidates.extend(p / "tdata" for p in extracted.iterdir() if p.is_dir())
    for candidate in candidates:
        if candidate.is_dir() and (candidate / "key_datas").exists():
            return candidate

    key_datas = extracted / "key_datas"
    if key_datas.is_file():
        return extracted
    for child in extracted.iterdir():
        if child.is_dir() and (child / "key_datas").is_file():
            return child
    return None


def _copy_session_assets(source: Path, destination: Path) -> None:
    """复制并收紧 TData 文件权限，避免把 ZIP 中其他文件带入账号目录。"""
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)
    for path in [destination, *destination.rglob("*")]:
        if path.is_dir():
            path.chmod(0o700)
        else:
            path.chmod(0o600)


@app.post("/api/auth/login")
async def auth_login(payload: AuthLoginRequest, response: Response):
    password_matches = bool(ADMIN_PASSWORD) and _safe_text_equal(payload.password, ADMIN_PASSWORD)
    api_key_matches = bool(ADMIN_API_KEY) and _safe_text_equal(payload.password, ADMIN_API_KEY)
    if not _safe_text_equal(payload.username, ADMIN_USERNAME) or not (password_matches or api_key_matches):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    response.set_cookie(
        SESSION_COOKIE, _create_session_token("admin"), max_age=SESSION_TTL_SECONDS,
        httponly=True, samesite="strict", secure=TG_ENV == "production",
    )
    return {"status": "success", "role": "admin", "username": ADMIN_USERNAME}

@app.get("/api/auth/me")
async def auth_me(role: str = Depends(require_auth)):
    return {"authenticated": True, "role": role, "username": ADMIN_USERNAME if role == "admin" else "operator"}

@app.post("/api/auth/logout")
async def auth_logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"status": "success"}

# ================================
# 1. 仪表盘统计 API
# ================================
@app.get("/api/dashboard/stats")
async def get_dashboard_stats():
    db_stats = database.get_database_stats()
    
    # 计算账号总数
    acc_count = len([d for d in os.listdir(ACCOUNTS_DIR) if os.path.isdir(os.path.join(ACCOUNTS_DIR, d))]) if os.path.exists(ACCOUNTS_DIR) else 0
    
    # 计算截获商机数
    conn = database.get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM intercepted_leads;")
    leads_count = cursor.fetchone()[0]
    conn.close()

    return {
        "accounts_count": acc_count,
        "total_users": db_stats["total_users"],
        "active_chatters": db_stats["active_chatters"],
        "premium_users": db_stats["premium_users"],
        "intercepted_leads": leads_count,
        "sources_count": db_stats["sources_count"]
    }


def _account_name_from_phone(phone: str) -> str:
    normalized = re.sub(r"[^0-9]", "", phone or "")
    if not 6 <= len(normalized) <= 20:
        raise HTTPException(status_code=400, detail="手机号格式无效")
    return normalized


def _write_account_metadata(account_name: str, data: Dict[str, Any]) -> None:
    folder = Path(ACCOUNTS_DIR) / account_name
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "account.json"
    current: Dict[str, Any] = {}
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = {}
    current.update(data)
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    path.chmod(0o600)


@app.post("/api/accounts/login/start")
async def start_phone_login(payload: PhoneLoginStartRequest, _: str = Depends(require_admin)):
    if DEFAULT_API_ID <= 0 or not DEFAULT_API_HASH:
        raise HTTPException(status_code=400, detail="请先配置 TG_API_ID/TG_API_HASH")
    account_name = _account_name_from_phone(payload.phone_number)
    phone = "+" + account_name
    previous = LOGIN_RUNTIMES.pop(account_name, None)
    if previous and previous.get("client"):
        await previous["client"].disconnect()
    client = TelegramClient(str(Path(SESSIONS_DIR) / account_name), DEFAULT_API_ID, DEFAULT_API_HASH)
    try:
        await client.connect()
        if await client.is_user_authorized():
            me = await client.get_me()
            _write_account_metadata(account_name, {
                "phone": phone, "remark": payload.remark.strip(), "user_id": me.id,
                "username": me.username, "first_name": me.first_name, "last_name": me.last_name,
                "authorized": True, "last_login_at": datetime.now().isoformat(),
            })
            await client.disconnect()
            return {"account_name": account_name, "state": "logged_in", "message": "账号已经登录"}
        sent = await client.send_code_request(phone)
        LOGIN_RUNTIMES[account_name] = {
            "client": client, "phone": phone, "phone_code_hash": sent.phone_code_hash,
            "created_at": time.time(), "remark": payload.remark.strip(),
        }
        _write_account_metadata(account_name, {"phone": phone, "remark": payload.remark.strip(), "authorized": False})
        return {"account_name": account_name, "state": "waiting_code", "message": "验证码已发送"}
    except Exception as exc:
        await client.disconnect()
        raise HTTPException(status_code=400, detail=f"发起登录失败: {exc}") from exc


@app.post("/api/accounts/login/code")
async def submit_phone_code(payload: PhoneLoginCodeRequest, _: str = Depends(require_admin)):
    validate_account_name(payload.account_name)
    runtime = LOGIN_RUNTIMES.get(payload.account_name)
    if not runtime:
        raise HTTPException(status_code=400, detail="登录流程已失效，请重新发送验证码")
    client = runtime["client"]
    try:
        await client.sign_in(
            phone=runtime["phone"], code=payload.code.strip(),
            phone_code_hash=runtime["phone_code_hash"],
        )
    except SessionPasswordNeededError:
        return {"account_name": payload.account_name, "state": "waiting_password", "message": "请输入二步验证密码"}
    except (PhoneCodeInvalidError, PhoneCodeExpiredError) as exc:
        raise HTTPException(status_code=400, detail=f"验证码无效或已过期: {exc}") from exc
    return await _finalize_phone_login(payload.account_name)


@app.post("/api/accounts/login/password")
async def submit_phone_password(payload: PhoneLoginPasswordRequest, _: str = Depends(require_admin)):
    validate_account_name(payload.account_name)
    runtime = LOGIN_RUNTIMES.get(payload.account_name)
    if not runtime:
        raise HTTPException(status_code=400, detail="登录流程已失效，请重新发送验证码")
    try:
        await runtime["client"].sign_in(password=payload.password)
    except PasswordHashInvalidError as exc:
        raise HTTPException(status_code=400, detail="二步验证密码错误") from exc
    return await _finalize_phone_login(payload.account_name)


async def _finalize_phone_login(account_name: str) -> Dict[str, Any]:
    runtime = LOGIN_RUNTIMES.pop(account_name)
    client = runtime["client"]
    me = await client.get_me()
    _write_account_metadata(account_name, {
        "phone": runtime["phone"], "remark": runtime.get("remark", ""),
        "user_id": me.id, "username": me.username, "first_name": me.first_name,
        "last_name": me.last_name, "authorized": True,
        "last_login_at": datetime.now().isoformat(),
    })
    await client.disconnect()
    return {
        "account_name": account_name, "state": "logged_in", "message": "登录成功",
        "account": {"phone": runtime["phone"], "username": me.username, "display_name": " ".join(filter(None, [me.first_name, me.last_name]))},
    }


@app.delete("/api/accounts/{account_name}")
async def delete_account(account_name: str, delete_session_files: bool = False, _: str = Depends(require_admin)):
    validate_account_name(account_name)
    runtime = ACTIVE_MONITORS.pop(account_name, None)
    if runtime:
        if runtime.get("task") and not runtime["task"].done():
            runtime["task"].cancel()
        if runtime.get("client"):
            await runtime["client"].disconnect()
    folder = Path(ACCOUNTS_DIR) / account_name
    if folder.exists():
        shutil.rmtree(folder)
    if delete_session_files:
        for suffix in (".session", ".session-journal"):
            session_file = Path(SESSIONS_DIR) / f"{account_name}{suffix}"
            if session_file.exists():
                session_file.unlink()
    return {"status": "success", "account_name": account_name}


# ================================
# 2. 账号资产与管理 API
# ================================
@app.get("/api/accounts")
async def list_accounts():
    accounts = []
    if not os.path.exists(ACCOUNTS_DIR):
        return accounts

    for name in os.listdir(ACCOUNTS_DIR):
        acc_path = os.path.join(ACCOUNTS_DIR, name)
        if os.path.isdir(acc_path):
            tdata_path = os.path.join(acc_path, "tdata")
            has_tdata = os.path.exists(tdata_path)
            
            proxy_file = os.path.join(acc_path, "proxy.txt")
            proxy_info = ""
            if os.path.exists(proxy_file):
                with open(proxy_file, "r", encoding="utf-8") as f:
                    proxy_info = f.read().strip()

            session_file = os.path.join(SESSIONS_DIR, name + ".session")
            runtime = ACTIVE_MONITORS.get(name, {})
            metadata = {}
            metadata_file = Path(acc_path) / "account.json"
            if metadata_file.exists():
                try:
                    metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    metadata = {}
            monitoring = bool(runtime.get("task") and not runtime["task"].done())
            accounts.append({
                "name": name,
                "phone": metadata.get("phone") or name,
                "username": metadata.get("username"),
                "display_name": " ".join(filter(None, [metadata.get("first_name"), metadata.get("last_name")])),
                "remark": metadata.get("remark", ""),
                "has_tdata": has_tdata,
                "type": "TData" if has_tdata else "Session",
                "dc_id": metadata.get("dc_id", 2),
                "proxy": proxy_info or "未绑定代理",
                "authorized": bool(metadata.get("authorized") or os.path.exists(session_file)),
                "connected": monitoring,
                "monitoring": monitoring,
                "last_login_at": metadata.get("last_login_at"),
                "status": "monitoring" if monitoring else ("ready" if os.path.exists(session_file) else "pending")
            })
    return accounts

@app.post("/api/accounts/import")
async def import_account(
    file: UploadFile = File(...),
    account_name: str = Form(...),
    proxy: Optional[str] = Form(None),
    _: str = Depends(require_admin),
):
    validate_account_name(account_name)
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="只支持包含 TData 的 ZIP 文件")

    proxy_value = (proxy or "").strip()
    if len(proxy_value) > 256 or any(char in proxy_value for char in "\r\n"):
        raise HTTPException(status_code=400, detail="代理配置格式无效")

    target_folder = Path(ACCOUNTS_DIR) / account_name
    if target_folder.exists():
        raise HTTPException(status_code=400, detail="该账号名称已存在，请换一个名称")

    try:
        with tempfile.TemporaryDirectory(prefix="session-import-", dir=UPLOADS_DIR) as temp_dir:
            temp_root = Path(temp_dir)
            zip_path = temp_root / "upload.zip"
            received_bytes = 0
            with zip_path.open("wb") as destination:
                while chunk := await file.read(1024 * 1024):
                    received_bytes += len(chunk)
                    if received_bytes > MAX_UPLOAD_BYTES:
                        raise ValueError("上传文件超过大小限制")
                    destination.write(chunk)

            extracted_root = temp_root / "extracted"
            extracted_root.mkdir()
            _safe_extract_zip(zip_path, extracted_root)
            tdata_root = _find_tdata_root(extracted_root)
            if tdata_root is None:
                raise ValueError("ZIP 中未找到有效的 TData/key_datas 结构")

            staged_account = temp_root / "account"
            staged_account.mkdir()
            _copy_session_assets(tdata_root, staged_account / "tdata")
            if proxy_value:
                proxy_path = staged_account / "proxy.txt"
                proxy_path.write_text(proxy_value, encoding="utf-8")
                proxy_path.chmod(0o600)

            target_folder.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged_account, target_folder)
    except HTTPException:
        raise
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=400, detail=f"会话资产导入失败: {exc}") from exc
    finally:
        await file.close()

    return {
        "status": "success",
        "message": f"账号 [{account_name}] 会话资产恢复就绪",
        "has_tdata": True,
        "session_ready": False,
    }

async def _process_single_account_zip(zip_path: str, proxy_value: str = "") -> dict:
    filename = os.path.basename(zip_path)
    phone = filename.replace('.zip', '')
    
    target_folder = Path(ACCOUNTS_DIR) / phone
    if target_folder.exists():
        return {"phone": phone, "status": "error", "message": "账号已存在，已跳过"}
        
    with tempfile.TemporaryDirectory(prefix="batch-check-", dir=UPLOADS_DIR) as tmpdir:
        try:
            # 完整解压 ZIP 到临时目录
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(tmpdir)

            # 在解压目录中深度查找 .session 和 .json 文件
            all_sessions = glob.glob(os.path.join(tmpdir, "**", "*.session"), recursive=True)
            all_jsons = glob.glob(os.path.join(tmpdir, "**", "*.json"), recursive=True)

            if not all_sessions:
                return {"phone": phone, "status": "error", "message": "包内未找到 .session 文件"}

            actual_session_file = all_sessions[0]
            session_base_path = os.path.splitext(actual_session_file)[0]

            # 读取 JSON 获取 app_id / app_hash
            app_id = DEFAULT_API_ID
            app_hash = DEFAULT_API_HASH
            json_path = all_jsons[0] if all_jsons else None

            if json_path and os.path.exists(json_path):
                try:
                    with open(json_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        app_id = data.get('app_id') or DEFAULT_API_ID
                        app_hash = data.get('app_hash') or DEFAULT_API_HASH
                except Exception:
                    pass

            if not app_id or not app_hash:
                return {"phone": phone, "status": "error", "message": "未配置有效的 API_ID/API_HASH"}

            # 连接 Telegram 测活
            client = TelegramClient(session_base_path, app_id, app_hash)
            await client.connect()
            if not await client.is_user_authorized():
                await client.disconnect()
                return {"phone": phone, "status": "error", "message": "授权已失效 (Logged out)"}

            me = await client.get_me()
            name = f"{me.first_name or ''} {me.last_name or ''}".strip()
            user_phone = getattr(me, 'phone', '') or phone
            await client.disconnect()

            # 测试成功，移入正式目录
            target_folder.mkdir(parents=True, exist_ok=True)
            if json_path and os.path.exists(json_path):
                shutil.copy2(json_path, target_folder / f"{phone}.json")
            if os.path.exists(actual_session_file):
                shutil.copy2(actual_session_file, Path(SESSIONS_DIR) / f"{phone}.session")

            if proxy_value:
                proxy_path = target_folder / "proxy.txt"
                proxy_path.write_text(proxy_value, encoding="utf-8")
                proxy_path.chmod(0o600)

            return {"phone": phone, "status": "success", "message": f"登录成功: {name or user_phone}"}
            
        except UserDeactivatedBanError:
            return {"phone": phone, "status": "error", "message": "账号已被封禁"}
        except KeyError:
            return {"phone": phone, "status": "error", "message": "包格式无效 (缺少json/session)"}
        except Exception as e:
            err = str(e)
            if "two different IP addresses" in err:
                return {"phone": phone, "status": "error", "message": "双IP冲突被封禁"}
            elif "authorization key" in err.lower():
                return {"phone": phone, "status": "error", "message": "Session密钥已失效"}
            else:
                return {"phone": phone, "status": "error", "message": f"错误: {err[:80]}"}


@app.post("/api/accounts/batch-upload")
async def batch_upload_accounts(
    files: List[UploadFile] = File(...),
    proxy: Optional[str] = Form(None),
    _: str = Depends(require_admin),
):
    """支持从网页多选/拖拽上传一个或多个账号 ZIP 压缩包进行批量导入与测活"""
    if not files:
        raise HTTPException(status_code=400, detail="未上传任何文件")

    proxy_value = (proxy or "").strip()
    results = []
    success_count = 0

    with tempfile.TemporaryDirectory(prefix="upload-batch-", dir=UPLOADS_DIR) as upload_tmp:
        for file in files:
            fname = file.filename or ""
            if not fname.lower().endswith(".zip"):
                results.append({"phone": fname, "status": "error", "message": "仅支持 .zip 格式压缩包"})
                continue

            save_zip_path = os.path.join(upload_tmp, fname)
            with open(save_zip_path, "wb") as dest:
                while chunk := await file.read(1024 * 1024):
                    dest.write(chunk)

            res = await _process_single_account_zip(save_zip_path, proxy_value)
            if res.get("status") == "success":
                success_count += 1
            results.append(res)

    return {
        "status": "success",
        "total_found": len(files),
        "imported_count": success_count,
        "results": results,
    }


@app.post("/api/accounts/batch-import-local")
async def batch_import_local(
    dir_path: str = Form(...),
    proxy: Optional[str] = Form(None),
    _: str = Depends(require_admin),
):
    dir_path = dir_path.strip()
    if not os.path.exists(dir_path):
        raise HTTPException(status_code=400, detail="指定的文件夹不存在")
    if not os.path.isdir(dir_path):
        raise HTTPException(status_code=400, detail="指定的路径不是一个文件夹")

    proxy_value = (proxy or "").strip()
    zip_files = glob.glob(os.path.join(dir_path, "*.zip"))
    zip_files = [f for f in zip_files if "telegram_full_web" not in f]
    
    if not zip_files:
        return {"status": "success", "total_found": 0, "imported_count": 0, "message": "该目录下没有找到有效ZIP文件", "results": []}

    results = []
    success_count = 0

    for zip_path in sorted(zip_files):
        res = await _process_single_account_zip(zip_path, proxy_value)
        if res.get("status") == "success":
            success_count += 1
        results.append(res)
                
    return {
        "status": "success",
        "total_found": len(zip_files),
        "imported_count": success_count,
        "message": f"批量检测完成，共找到 {len(zip_files)} 个账号包，成功导入 {success_count} 个",
        "results": results
    }

@app.post("/api/accounts/launch-desktop")
async def launch_desktop(
    account_name: str = Form(...),
    portable_exe_path: str = Form(...),
    _: str = Depends(require_admin),
):
    validate_account_name(account_name)
    acc_path = os.path.join(ACCOUNTS_DIR, account_name)
    tdata_path = os.path.join(acc_path, "tdata")
    if not os.path.exists(tdata_path):
        raise HTTPException(status_code=404, detail="未找到该账号的 tdata 文件夹")
    if not os.path.exists(portable_exe_path):
        raise HTTPException(status_code=400, detail="指定的 Telegram.exe 便携版路径不存在")

    sandbox_exe = os.path.join(acc_path, "Telegram.exe")
    if not os.path.exists(sandbox_exe):
        shutil.copy2(portable_exe_path, sandbox_exe)

    try:
        subprocess.Popen([sandbox_exe, "-workdir", acc_path], cwd=acc_path)
        return {"status": "success", "message": f"已在独立沙箱中唤起账号 [{account_name}]"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"启动失败: {str(e)}")

@app.post("/api/accounts/get-code")
async def get_login_code(
    account_name: str = Form(...),
    _: str = Depends(require_admin),
):
    validate_account_name(account_name)
    acc_path = os.path.join(ACCOUNTS_DIR, account_name)
    tdata_path = os.path.join(acc_path, "tdata")
    if not os.path.exists(tdata_path):
        raise HTTPException(status_code=404, detail="tdata 目录不存在")

    proxy_params = None
    proxy_file = os.path.join(acc_path, "proxy.txt")
    if os.path.exists(proxy_file):
        with open(proxy_file, "r", encoding="utf-8") as f:
            p_str = f.read().strip()
            if p_str:
                p_clean = p_str.replace("socks5://", "").replace("http://", "")
                parts = p_clean.split(":")
                if len(parts) >= 2:
                    proxy_params = (socks.SOCKS5, parts[0], int(parts[1]))

    try:
        tdesk = TDesktop(tdata_path)
        if not tdesk.isLoaded():
            raise HTTPException(status_code=400, detail="无法解析此 tdata 文件")

        session_path = os.path.join(SESSIONS_DIR, account_name)
        client = await tdesk.ToTelethon(session=session_path, flag=UseCurrentSession, proxy=proxy_params)
        await client.connect()

        if not await client.is_user_authorized():
            await client.disconnect()
            return {"status": "unauthorized", "message": "该账号会话已被服务端注销/踢下线"}

        messages = []
        async for message in client.iter_messages(777000, limit=5):
            messages.append({"date": str(message.date), "text": message.text})

        await client.disconnect()
        return {"status": "success", "messages": messages}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/accounts/check-spambot")
async def check_spambot(
    account_name: str = Form(...),
    _: str = Depends(require_admin),
):
    try:
        client = get_client_for_account(account_name)
        await client.start()
        mgr = AccountActionManager(client)
        res = await mgr.check_spambot_status()
        await client.disconnect()
        return res
    except Exception as e:
        return {"status": "error", "detail": f"查询失败: {str(e)}"}

@app.post("/api/accounts/batch-leave")
async def batch_leave_chats(
    account_name: str = Form(...),
    _: str = Depends(require_admin),
):
    try:
        client = get_client_for_account(account_name)
        await client.start()
        mgr = AccountActionManager(client)
        res = await mgr.batch_leave_chats()
        await client.disconnect()
        return {"status": "success", "stats": res}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

# ================================
# 3. 受众采集与筛选 API
# ================================
@app.post("/api/scraper/start")
async def start_scraping(
    account_name: str = Form(...),
    target_link: str = Form(...),
    scrape_mode: str = Form(...),  # members, active_chatters, commenters
    limit: int = Form(1000),
    days_lookback: int = Form(7),
    _: str = Depends(require_admin),
):
    try:
        client = get_client_for_account(account_name)
        await client.start()
        scraper = TelegramScraper(client)

        if scrape_mode == "members":
            results = await scraper.scrape_group_members(target_link, limit=limit)
        elif scrape_mode == "active_chatters":
            results = await scraper.scrape_active_chatters(target_link, days_lookback=days_lookback, max_messages=limit*3)
        elif scrape_mode == "commenters":
            results = await scraper.scrape_channel_commenters(target_link, recent_posts_count=30)
        else:
            await client.disconnect()
            raise HTTPException(status_code=400, detail="未知的采集模式")

        await client.disconnect()
        return {"status": "success", "count": len(results), "message": f"成功采集 {len(results)} 位目标受众"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"采集失败: {str(e)}")

@app.get("/api/scraper/users")
async def get_collected_users(
    tag: Optional[str] = None,
    only_active: bool = False,
    limit: int = 500
):
    users = database.query_collected_users(tag=tag, only_active=only_active, limit=limit)
    return users

@app.get("/api/scraper/export-csv")
async def export_csv():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(EXPORTS_DIR, f"telegram_leads_{timestamp}.csv")
    database.export_users_to_csv(filepath)
    return FileResponse(filepath, filename=f"telegram_leads_{timestamp}.csv", media_type="text/csv")

# ================================
# 4. 营销矩阵与拉群/群发 API
# ================================
@app.post("/api/inviter/start")
async def start_inviting(
    target_group: str = Form(...),
    tag_filter: Optional[str] = Form(None),
    max_limit: int = Form(100),
    max_per_account: int = Form(15),
    _: str = Depends(require_admin),
):
    clients = await get_all_active_clients()
    if not clients:
        raise HTTPException(status_code=400, detail="当前没有任何已就绪的工作账号")

    users = database.query_collected_users(tag=tag_filter or None, only_active=True, limit=max_limit)
    if not users:
        raise HTTPException(status_code=400, detail="数据库中没有符合条件的受众数据，请先采集！")

    inviter = TelegramInviter(clients, min_delay=20, max_delay=45, max_per_account=max_per_account)
    stats = await inviter.execute_batch_invite(target_group, users)

    for c in clients:
        await c.disconnect()

    return {"status": "success", "stats": stats}

@app.post("/api/sender/preview-spintax")
async def preview_spintax(template: str = Form(...)):
    mock_user = {"first_name": "张总", "username": "zhang_vip"}
    variants = [SpintaxParser.parse(template, mock_user) for _ in range(5)]
    return {"variants": variants}

@app.post("/api/sender/start-dm")
async def start_mass_dm(
    template: str = Form(...),
    tag_filter: Optional[str] = Form(None),
    max_limit: int = Form(50),
    max_per_account: int = Form(20),
    _: str = Depends(require_admin),
):
    clients = await get_all_active_clients()
    if not clients:
        raise HTTPException(status_code=400, detail="没有可用的工作账号")

    users = database.query_collected_users(tag=tag_filter or None, limit=max_limit)
    if not users:
        raise HTTPException(status_code=400, detail="受众数据池为空，请先采集受众")

    sender = TelegramMassSender(clients, min_delay=20, max_delay=40, max_per_account=max_per_account)
    stats = await sender.send_mass_dm(users, template)

    for c in clients:
        await c.disconnect()

    return {"status": "success", "stats": stats}

# ================================
# 5. PRO 高级工具 API
# ================================
@app.post("/api/pro/clone-channel")
async def clone_channel(
    account_name: str = Form(...),
    source_channel: str = Form(...),
    target_channel: str = Form(...),
    limit: int = Form(200),
    replace_old: Optional[str] = Form(None),
    replace_new: Optional[str] = Form(None),
    _: str = Depends(require_admin),
):
    try:
        client = get_client_for_account(account_name)
        await client.start()
        
        rules = {replace_old: replace_new} if replace_old and replace_new else None
        cloner = ChannelCloner(client, min_delay=3.0, max_delay=7.0)
        stats = await cloner.clone_channel_content(
            source_channel_link=source_channel,
            target_channel_link=target_channel,
            limit=limit,
            replace_rules=rules
        )
        await client.disconnect()
        return {"status": "success", "stats": stats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"克隆失败: {str(e)}")

@app.get("/api/pro/interceptor/leads")
async def get_intercepted_leads(limit: int = 50):
    conn = database.get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM intercepted_leads ORDER BY created_at DESC LIMIT ?;", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]



@app.post("/api/accounts/{account_name}/monitor/start")
async def start_account_monitor(account_name: str, _: str = Depends(require_admin)):
    validate_account_name(account_name)
    existing = ACTIVE_MONITORS.get(account_name, {})
    if existing.get("task") and not existing["task"].done():
        return {"status": "success", "message": "监听已经在运行", "account_name": account_name}
    try:
        client = get_client_for_account(account_name)
        await client.start()
        interceptor = KeywordInterceptor(client=client, keywords=[], account_name=account_name, archive_all=True)
        task = asyncio.create_task(interceptor.start_listening())
        ACTIVE_MONITORS[account_name] = {"client": client, "task": task, "interceptor": interceptor}
        return {"status": "success", "message": "账号监听已启动", "account_name": account_name}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"监听启动失败: {exc}") from exc

@app.post("/api/accounts/{account_name}/monitor/stop")
async def stop_account_monitor(account_name: str, _: str = Depends(require_admin)):
    validate_account_name(account_name)
    runtime = ACTIVE_MONITORS.pop(account_name, None)
    if not runtime:
        return {"status": "success", "message": "监听未运行", "account_name": account_name}
    task = runtime.get("task")
    if task and not task.done():
        task.cancel()
    client = runtime.get("client")
    if client:
        await client.disconnect()
    return {"status": "success", "message": "账号监听已停止", "account_name": account_name}

@app.post("/api/accounts/{account_name}/reconnect")
async def reconnect_account(account_name: str, _: str = Depends(require_admin)):
    validate_account_name(account_name)
    runtime = ACTIVE_MONITORS.get(account_name)
    if runtime and runtime.get("client"):
        await runtime["client"].disconnect()
        ACTIVE_MONITORS.pop(account_name, None)
    try:
        client = get_client_for_account(account_name)
        await client.connect()
        authorized = await client.is_user_authorized()
        await client.disconnect()
        return {"status": "success" if authorized else "unauthorized", "authorized": authorized, "account_name": account_name}
    except Exception as exc:
        return {"status": "error", "authorized": False, "detail": str(exc), "account_name": account_name}

# ================================
# 监控产品化 API：规则、历史消息、Bot 目标
# ================================
@app.get("/api/monitor/rules")
async def list_monitor_rules(account_name: Optional[str] = None, _: str = Depends(require_auth)):
    return database.list_keyword_rules(account_name)

@app.post("/api/monitor/rules")
async def create_monitor_rule(rule: KeywordRuleRequest, _: str = Depends(require_admin)):
    if not rule.keyword_value.strip():
        raise HTTPException(status_code=400, detail="关键词不能为空")
    if rule.match_mode not in {"exact", "contains", "regex", "fuzzy"}:
        raise HTTPException(status_code=400, detail="不支持的匹配方式")
    if rule.action not in {"monitor", "exclude"}:
        raise HTTPException(status_code=400, detail="不支持的规则动作")
    return {"id": database.save_keyword_rule(rule.model_dump()), "status": "success"}

@app.put("/api/monitor/rules/{rule_id}")
async def update_monitor_rule(rule_id: int, rule: KeywordRuleRequest, _: str = Depends(require_admin)):
    if not database.update_keyword_rule(rule_id, rule.model_dump()):
        raise HTTPException(status_code=404, detail="规则不存在")
    return {"status": "success", "id": rule_id}

@app.delete("/api/monitor/rules/{rule_id}")
async def delete_monitor_rule(rule_id: int, _: str = Depends(require_admin)):
    if not database.delete_keyword_rule(rule_id):
        raise HTTPException(status_code=404, detail="规则不存在")
    return {"status": "success"}

@app.get("/api/monitor/messages")
async def list_monitor_messages(
    page: int = 1, page_size: int = 50, account_name: Optional[str] = None,
    chat_id: Optional[int] = None, sender_id: Optional[int] = None,
    keyword: Optional[str] = None, _: str = Depends(require_auth),
):
    return database.query_archived_messages(
        page=page, page_size=page_size, account_name=account_name,
        chat_id=chat_id, sender_id=sender_id, keyword=keyword,
    )

@app.get("/api/monitor/bot-targets")
async def list_monitor_bot_targets(_: str = Depends(require_auth)):
    targets = database.list_bot_targets()
    for target in targets:
        token = target.get("bot_token", "")
        target["bot_token"] = (token[:6] + "…") if len(token) > 6 else "***"
    return targets

@app.post("/api/monitor/bot-targets")
async def create_monitor_bot_target(target: BotTargetRequest, _: str = Depends(require_admin)):
    if not target.bot_token.strip() or not target.chat_id.strip():
        raise HTTPException(status_code=400, detail="Bot Token 和 Chat ID 不能为空")
    return {"id": database.save_bot_target(target.model_dump()), "status": "success"}

@app.put("/api/monitor/bot-targets/{target_id}")
async def update_monitor_bot_target(target_id: int, target: BotTargetRequest, _: str = Depends(require_admin)):
    if not target.chat_id.strip():
        raise HTTPException(status_code=400, detail="Chat ID 不能为空")
    if not database.update_bot_target(target_id, target.model_dump()):
        raise HTTPException(status_code=404, detail="Bot 通知目标不存在")
    return {"status": "success", "id": target_id}

@app.delete("/api/monitor/bot-targets/{target_id}")
async def delete_monitor_bot_target(target_id: int, _: str = Depends(require_admin)):
    if not database.delete_bot_target(target_id):
        raise HTTPException(status_code=404, detail="Bot 通知目标不存在")
    return {"status": "success"}

# 背景图显式路由：避免 Railway 静态挂载对二进制资源返回 404
@app.get("/assets/yun-ding.jpg")
async def yun_ding_background():
    cache_headers = {
        "Cache-Control": "public, max-age=31536000, immutable",
        "CDN-Cache-Control": "public, max-age=31536000",
        "Surrogate-Control": "public, max-age=31536000",
    }
    return FileResponse(os.path.join(BASE_DIR, "static", "assets", "yun-ding.jpg"), media_type="image/jpeg", headers=cache_headers)

@app.get("/demo", include_in_schema=False)
async def public_demo_page():
    return FileResponse(os.path.join(BASE_DIR, "static", "demo.html"), media_type="text/html")

# 挂载静态网页
app.mount("/", StaticFiles(directory=os.path.join(BASE_DIR, "static"), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "18000"))
    print("=" * 60)
    print("  🚀 Telegram 一体化自动化运营大控制台已就绪")
    print(f"  👉 请在浏览器中打开: http://127.0.0.1:{port}")
    print("=" * 60)
    uvicorn.run(app, host="127.0.0.1", port=port)
