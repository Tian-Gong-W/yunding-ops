/**
 * WhatsApp 多账号隔离 Worker
 * ============================================================
 * 隔离原则（防封核心）：
 *  1. 每个账号 = 独立 fingerprint-chromium 进程
 *  2. 每个账号 = 固定 --user-data-dir（永不清理，扫码一次长期保活）
 *  3. 每个账号 = 固定 --fingerprint 种子（浏览器指纹不变）
 *  4. 每个账号 = 独立代理 IP（国家/地区与手机号归属一致）
 *
 * 扫码：在 http://<host>:8080 查看各账号二维码扫码登录
 * 接码：先用接码平台收短信在手机端完成 WA 注册，再到 8080 页面扫码上 Web 端
 */

const fs = require('fs');
const path = require('path');
const http = require('http');
const puppeteer = require('rebrowser-puppeteer');
const { startLocalRelay } = require('./proxy-relay');

const CONFIG_PATH = process.env.ACCOUNTS_JSON || '/config/accounts.json';
const DATA_DIR = process.env.DATA_DIR || '/data';
const CHROME_PATH = process.env.CHROME_PATH || '/opt/fingerprint-chromium/chrome';
const STATUS_PORT = parseInt(process.env.STATUS_PORT || '8080', 10);

const PROFILES_DIR = path.join(DATA_DIR, 'profiles');
const QR_DIR = path.join(DATA_DIR, 'qr');
for (const d of [PROFILES_DIR, QR_DIR]) fs.mkdirSync(d, { recursive: true });

const state = {}; // name -> { status, lastSeen, qrPath, phone }
const browsers = [];

// ---------------- 配置 ----------------

function loadAccounts() {
  // 优先读统一 yaml 注册表（yunding-ops/accounts/accounts.yaml 的 whatsapp 一节），
  // 兼容老的 JSON 数组格式
  const yamlPath = process.env.ACCOUNTS_YAML;
  let list;
  if (yamlPath && fs.existsSync(yamlPath)) {
    const yaml = require('js-yaml');
    const doc = yaml.load(fs.readFileSync(yamlPath, 'utf8')) || {};
    list = doc.whatsapp || [];
    if (list.length === 0) throw new Error('accounts.yaml 的 whatsapp 一节为空');
  } else {
    list = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
    if (!Array.isArray(list)) throw new Error('accounts.json 格式错误（需要数组）');
  }
  if (list.length === 0) throw new Error('账号列表为空');
  const names = new Set();
  const seeds = new Set();
  for (const a of list) {
    if (!a.name || !/^[A-Za-z0-9][A-Za-z0-9_-]{1,32}$/.test(a.name)) throw new Error(`账号名非法: ${a.name}`);
    if (names.has(a.name)) throw new Error(`账号名重复: ${a.name}`);
    if (!Number.isInteger(a.fingerprintSeed)) throw new Error(`账号 ${a.name} 缺少 fingerprintSeed（32 位整数）`);
    if (seeds.has(a.fingerprintSeed)) throw new Error(`fingerprintSeed 重复: ${a.fingerprintSeed}（每个账号必须唯一）`);
    names.add(a.name);
    seeds.add(a.fingerprintSeed);
  }
  return list;
}

function parseProxy(proxyUrl) {
  if (!proxyUrl) return null;
  const u = new URL(proxyUrl);
  const scheme = u.protocol.replace(':', '');
  if (!['http', 'https', 'socks5', 'socks'].includes(scheme)) throw new Error(`不支持的代理协议: ${scheme}`);
  return {
    scheme,
    server: `${scheme}://${u.hostname}:${u.port}`,
    username: decodeURIComponent(u.username || ''),
    password: decodeURIComponent(u.password || ''),
    hasAuth: Boolean(u.username),
  };
}

// ---------------- WhatsApp Web 状态判断 ----------------

async function isLoggedIn(page) {
  // 登录成功后会出现聊天列表侧栏；多种选择器做兼容
  return page
    .evaluate(
      () =>
        !!document.querySelector(
          '#pane-side, [aria-label="Chat list"], [data-testid="chat-list"], div[contenteditable="true"][data-tab="3"]'
        )
    )
    .catch(() => false);
}

async function captureQR(page, name) {
  // 二维码以 canvas 形式渲染，轮询等待出现后截图
  const canvas = await page.waitForSelector('canvas', { timeout: 45000 }).catch(() => null);
  if (!canvas) return false;
  const out = path.join(QR_DIR, `${name}.png`);
  await canvas.screenshot({ path: out });
  state[name].qrPath = `/qr/${name}.png`;
  state[name].status = 'waiting_scan';
  return true;
}

// ---------------- 号码关联登录（WhatsApp 网页版“Link with phone number”） ----------------

// 区号 -> 国家英文名（WhatsApp 国家选择器搜索用）
const COUNTRY_NAMES = {
  '852': 'Hong Kong', '853': 'Macao', '886': 'Taiwan', '86': 'China',
  '81': 'Japan', '82': 'Korea', '65': 'Singapore', '60': 'Malaysia',
  '66': 'Thailand', '84': 'Vietnam', '62': 'Indonesia', '63': 'Philippines',
  '91': 'India', '61': 'Australia', '64': 'New Zealand',
  '1': 'United States', '44': 'United Kingdom', '33': 'France', '49': 'Germany',
  '39': 'Italy', '34': 'Spain', '31': 'Netherlands', '41': 'Switzerland',
  '7': 'Russia', '55': 'Brazil', '52': 'Mexico', '54': 'Argentina',
  '971': 'United Arab Emirates', '966': 'Saudi Arabia', '20': 'Egypt', '27': 'South Africa',
  '234': 'Nigeria', '90': 'Turkey',
};

function splitPhone(phone) {
  const digits = String(phone || '').replace(/\D/g, '');
  const codes = Object.keys(COUNTRY_NAMES).sort((a, b) => b.length - a.length);
  for (const c of codes) {
    if (digits.startsWith(c) && digits.length > c.length + 5) {
      return { cc: c, national: digits.slice(c.length), country: COUNTRY_NAMES[c] };
    }
  }
  return { cc: '', national: digits, country: '' };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 尝试走“号码关联”流程，成功返回 8 位关联码（如 ABCD-1234），失败返回 null（永不抛错）
async function tryPhoneLink(page, account) {
  try {
    const { national, country } = splitPhone(account.phone);
    if (!national || national.length < 5) return null;

    // 1. 点 "Link with phone number instead"
    const opened = await page.evaluate(() => {
      const els = [...document.querySelectorAll('*')].filter(
        (el) => el.children.length <= 2 && /link with phone number instead/i.test(el.textContent || '')
      );
      if (!els.length) return false;
      const el = els.sort((a, b) => (a.textContent || '').length - (b.textContent || '').length)[0];
      let c = el;
      for (let i = 0; i < 6 && c; i++) {
        if (c.getAttribute && c.getAttribute('role') === 'button') { c.click(); return true; }
        c = c.parentElement;
      }
      el.click();
      return true;
    });
    if (!opened) return null;
    await sleep(4000);

    // 2. 如果国家选择器不是目标国家，尝试切换（代理 IP 国家与手机号归属一致时通常默认就是对的）
    if (country) {
      await page.evaluate((want) => {
        // 找电话输入框，回溯容器找国家行按钮
        const input = document.querySelector('input[aria-label*="phone number" i]');
        if (!input) return;
        let box = input;
        for (let i = 0; i < 7 && box; i++) {
          box = box.parentElement;
          if (!box) break;
          const btn = [...box.querySelectorAll('[role="button"], button')].find((b) => {
            const t = (b.textContent || '').trim();
            return t.length > 1 && t.length < 40 && !/next/i.test(t) && box.contains(input);
          });
          if (btn && !new RegExp(want, 'i').test(btn.textContent || '')) {
            btn.click();
            return;
          }
          if (btn) return; // 已经是目标国家
        }
      }, country).catch(() => {});
      await sleep(2500);
      // 国家列表打开后：搜索 -> 选第一项
      const picked = await page.evaluate((want) => {
        const search = [...document.querySelectorAll('input')].find(
          (x) => /search/i.test(x.placeholder || '') || /search/i.test(x.getAttribute('aria-label') || '')
        );
        if (!search) return 'NOSEARCH';
        search.focus();
        document.execCommand('selectAll', false, null);
        document.execCommand('insertText', false, want);
        return 'SEARCHED';
      }, country).catch(() => 'ERR');
      if (picked === 'SEARCHED') {
        await sleep(2500);
        await page.evaluate((want) => {
          const opts = [...document.querySelectorAll('[role="button"], [role="option"], li')].filter(
            (el) => new RegExp(want, 'i').test(el.textContent || '') && (el.textContent || '').length < 60
          );
          if (opts[0]) opts[0].click();
        }, country).catch(() => {});
        await sleep(2500);
      }
    }

    // 3. 填号码 -> Next
    const filled = await page.evaluate((num) => {
      const input = document.querySelector('input[aria-label*="phone number" i]') ||
        [...document.querySelectorAll('input[type="text"]')].find((x) => x.offsetParent !== null);
      if (!input) return false;
      input.focus();
      document.execCommand('selectAll', false, null);
      document.execCommand('insertText', false, num);
      return true;
    }, national).catch(() => false);
    if (!filled) return null;
    await sleep(1500);
    await page.evaluate(() => {
      const btns = [...document.querySelectorAll('button, [role="button"]')].filter(
        (b) => /^next$/i.test((b.textContent || '').trim())
      );
      if (btns[0]) btns[0].click();
    }).catch(() => {});
    // 4. 轮询读取 8 位关联码（形如 XXXX-XXXX）
    for (let i = 0; i < 10; i++) {
      await sleep(3000);
      const code = await page.evaluate(() => {
        const texts = [];
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        let n;
        while ((n = walker.nextNode())) {
          const t = n.textContent.trim();
          if (t.length > 0 && t.length < 60) texts.push(t);
        }
        const hit = texts.find((t) => /^[A-Z0-9]{4}-[A-Z0-9]{4}$/.test(t));
        const invalid = texts.some((t) => /valid phone number is required/i.test(t));
        return hit || (invalid ? 'INVALID' : null);
      }).catch(() => null);
      if (code && code !== 'INVALID') return code;
      if (code === 'INVALID') break;
    }
    return null;
  } catch (_) {
    return null;
  } finally {
    // 回到二维码屏（失败也不影响扫码登录）
    await page.evaluate(() => {
      const els = [...document.querySelectorAll('*')].filter(
        (el) => el.children.length <= 2 && /log in with qr code/i.test(el.textContent || '')
      );
      if (els.length) {
        const el = els.sort((a, b) => (a.textContent || '').length - (b.textContent || '').length)[0];
        let c = el;
        for (let i = 0; i < 6 && c; i++) {
          if (c.getAttribute && c.getAttribute('role') === 'button') { c.click(); return; }
          c = c.parentElement;
        }
        el.click();
      }
    }).catch(() => {});
    await sleep(3000);
  }
}

// ---------------- 单账号启动 ----------------

async function launchAccount(account, idx) {
  await new Promise((r) => setTimeout(r, idx * 20000)); // 错峰启动，避免同时指纹启动被关联
  // 监督循环：单个账号的任何异常只重建该账号，绝不拖垮整个进程
  for (;;) {
    try {
      await runAccountSession(account, idx);
    } catch (e) {
      const msg = String((e && e.message) || e).slice(0, 120);
      state[account.name].status = 'error: ' + msg;
      console.log(`[${account.name}] 会话异常，60 秒后重建: ${msg}`);
    }
    await new Promise((r) => setTimeout(r, 60000));
  }
}

async function runAccountSession(account, idx) {
  const profileDir = path.join(PROFILES_DIR, account.name);
  fs.mkdirSync(profileDir, { recursive: true });
  // 清理上次崩溃残留的 Chromium 单例锁（本设计保证一个账号同一时间只有一个浏览器实例，
  // 且每次重建前旧实例已在 finally 中 close，因此这里删除是安全的）
  for (const f of ['SingletonSocket', 'SingletonLock', 'SingletonCookie', 'DevToolsActivePort']) {
    try {
      fs.rmSync(path.join(profileDir, f), { force: true });
    } catch (_) {}
  }
  state[account.name] = {
    status: 'starting',
    lastSeen: new Date().toISOString(),
    qrPath: null,
    linkCode: null,
    linkCodeAt: null,
    phone: account.phone || '',
  };

  const proxy = parseProxy(account.proxy);
  let browser;
  let relay = null;
  const args = [
    `--fingerprint=${account.fingerprintSeed}`,
    `--user-data-dir=${profileDir}`,
    `--fingerprint-platform=${account.platform || 'windows'}`,
    '--fingerprint-brand=Chrome',
    `--timezone=${account.timezone || 'Asia/Shanghai'}`,
    `--lang=${account.lang || 'en-US'}`,
    '--disable-non-proxied-udp', // 防止 WebRTC 泄露真实 IP
    '--no-sandbox',
    '--disable-dev-shm-usage',
    '--no-first-run',
    '--no-default-browser-check',
  ];
  // fingerprint-chromium 的 --proxy-server 不支持账号密码鉴权，分三种情况：
  //  - 无密码代理 -> 直接 --proxy-server
  //  - http/https 代理带密码 -> --proxy-server + page.authenticate()
  //  - socks5 代理带密码 -> 起本地 127.0.0.1 中转（proxy-relay.js），浏览器走无密码中转
  if (proxy && !proxy.hasAuth) {
    args.push(`--proxy-server=${proxy.server}`);
  } else if (proxy && proxy.hasAuth && (proxy.scheme === 'socks5' || proxy.scheme === 'socks')) {
    const relayPort = 18000 + idx;
    relay = await startLocalRelay(account.proxy, relayPort);
    args.push(`--proxy-server=http://127.0.0.1:${relayPort}`);
    console.log(`[${account.name}] socks5 密码代理已启用本地中转 127.0.0.1:${relayPort}`);
  } else if (proxy && proxy.hasAuth) {
    args.push(`--proxy-server=${proxy.server}`);
  }

  try {
  browser = await puppeteer.launch({
    executablePath: CHROME_PATH,
    headless: false, // 在 xvfb 虚拟显示下以有头模式运行，反检测效果最好
    args,
    defaultViewport: { width: 1366, height: 900 },
  });
  browsers.push(browser);

  const page = (await browser.pages())[0] || (await browser.newPage());
  if (proxy && proxy.hasAuth && (proxy.scheme === 'http' || proxy.scheme === 'https')) {
    await page.authenticate({ username: proxy.username, password: proxy.password });
  }
  // socks5 带密码的情况已在上面经本地中转处理，这里无需 page.authenticate()

  await page.goto('https://web.whatsapp.com/', { waitUntil: 'networkidle2', timeout: 90000 });

  // 主循环：扫码等待 -> 已登录 -> 保活（永不删 profile）
  for (;;) {
    try {
      if (await isLoggedIn(page)) {
        if (state[account.name].status !== 'online') console.log(`[${account.name}] 登录成功，进入保活`);
        state[account.name].status = 'online';
        state[account.name].lastSeen = new Date().toISOString();
        state[account.name].qrPath = null;
        state[account.name].linkCode = null;
        const old = path.join(QR_DIR, `${account.name}.png`);
        if (fs.existsSync(old)) fs.unlinkSync(old);
      } else {
        const ok = await captureQR(page, account.name);
        if (!ok) {
          state[account.name].status = 'loading';
          await page.reload({ waitUntil: 'networkidle2', timeout: 60000 }).catch(() => {});
        } else {
          console.log(`[${account.name}] 等待扫码: http://localhost:${STATUS_PORT}/qr/${account.name}.png`);
          // 二维码就绪后，尝试再拿一个“号码关联码”（手机 WhatsApp 用电话号码关联登录）
          // 失败不影响扫码，静默回落
          if (!state[account.name].linkCode) {
            console.log(`[${account.name}] 尝试获取号码关联码…`);
            const code = await tryPhoneLink(page, account);
            if (code) {
              state[account.name].linkCode = code;
              state[account.name].linkCodeAt = new Date().toISOString();
              console.log(`[${account.name}] 号码关联码: ${code}`);
              // 回到二维码屏后重新截图（二维码会刷新）
              await captureQR(page, account.name).catch(() => {});
            } else {
              console.log(`[${account.name}] 号码关联码获取失败，仅二维码可用`);
            }
          }
        }
      }
    } catch (e) {
      state[account.name].status = 'error: ' + String((e && e.message) || e).slice(0, 100);
      try {
        if (page.isClosed()) return; // 页面被关则退出该账号协程
      } catch (_) {}
    }
    await new Promise((r) => setTimeout(r, 15000));
  }
  } finally {
    if (browser) await browser.close().catch(() => {});
    if (relay) await relay.close().catch(() => {});
  }
}

// ---------------- 状态页 ----------------

function startStatusServer() {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname === '/api/status') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(state, null, 2));
      return;
    }
    if (url.pathname === '/assets/yun-ding.jpg') {
      const f = path.join(__dirname, 'assets', 'yun-ding.jpg');
      if (fs.existsSync(f)) {
        res.writeHead(200, { 'Content-Type': 'image/jpeg', 'Cache-Control': 'public, max-age=86400' });
        fs.createReadStream(f).pipe(res);
      } else {
        res.writeHead(404);
        res.end('no bg');
      }
      return;
    }
    const m = url.pathname.match(/^\/qr\/([A-Za-z0-9_-]+)\.png$/);
    if (m) {
      const f = path.join(QR_DIR, `${m[1]}.png`);
      if (fs.existsSync(f)) {
        res.writeHead(200, { 'Content-Type': 'image/png', 'Cache-Control': 'no-store' });
        fs.createReadStream(f).pipe(res);
      } else {
        res.writeHead(404);
        res.end('QR not ready');
      }
      return;
    }
    // 首页：雲頂天宮 风格（与 tg.tian-gong.cc 同款背景 + 毛玻璃卡片）
    const cards = Object.entries(state)
      .map(([name, s]) => {
        const online = s.status === 'online';
        const waiting = s.status === 'waiting_scan';
        const dot = online ? '#22c55e' : waiting ? '#f59e0b' : '#ef4444';
        const label = online ? '在线' : waiting ? '等待扫码' : '异常/启动中';
        const qr = s.qrPath
          ? `<div style="background:#fff;border-radius:12px;padding:10px;display:inline-block;margin:10px 0">
               <img src="${s.qrPath}?t=${Date.now()}" width="230" style="display:block"/></div>
             <div style="font-size:12px;opacity:.75">手机 WhatsApp → 设置 → 已关联设备 → 扫码</div>`
          : `<div style="font-size:13px;opacity:.7;margin:14px 0;min-height:60px">${waiting ? '二维码加载中…' : String(s.status).slice(0, 80)}</div>`;
        const linkBlock = s.linkCode
          ? `<div style="background:#fff;color:#0f172a;border-radius:12px;padding:14px 18px;margin:10px auto;max-width:300px">
               <div style="font-size:11px;opacity:.6;margin-bottom:6px">号码关联码（免扫码）</div>
               <div style="font-size:32px;font-weight:800;letter-spacing:5px;font-family:monospace">${s.linkCode}</div>
               <div style="font-size:11px;opacity:.6;margin-top:6px">手机 WhatsApp → 已关联设备 → 用电话号码关联 → 输入此码</div>
             </div>`
          : '';
        return `<div style="background:rgba(255,255,255,.08);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);
          border:1px solid rgba(255,255,255,.16);border-radius:18px;padding:22px;margin:12px;
          display:inline-block;vertical-align:top;text-align:center;min-width:300px;box-shadow:0 8px 32px rgba(0,0,0,.35)">
          <div style="display:flex;align-items:center;justify-content:center;gap:8px;margin-bottom:6px">
            <span style="width:10px;height:10px;border-radius:50%;background:${dot};box-shadow:0 0 10px ${dot}"></span>
            <b style="font-size:18px">${name}</b>
          </div>
          <div style="font-size:13px;opacity:.8">${s.phone || ''} · ${label}</div>
          ${qr}
          ${linkBlock}
          <div style="font-size:11px;opacity:.55;margin-top:8px">最后活跃: ${s.lastSeen || '-'}</div>
        </div>`;
      })
      .join('');
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end(`<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
      <meta http-equiv="refresh" content="30"><title>雲頂天宮 · WhatsApp 账号矩阵</title>
      <style>
        * { box-sizing: border-box; }
        body { font-family: "Noto Sans SC", system-ui, -apple-system, sans-serif; margin: 0; padding: 0;
          min-height: 100vh; color: #f1f5f9; position: relative; isolation: isolate; }
        body::before { content: ""; position: fixed; inset: 0; z-index: -1; pointer-events: none;
          background-color: #081c35;
          background-image: linear-gradient(rgba(0,29,54,.34), rgba(0,76,96,.42)), url('/assets/yun-ding.jpg');
          background-size: cover; background-position: center center; background-repeat: no-repeat; }
        .wrap { max-width: 1100px; margin: 0 auto; padding: 40px 20px; text-align: center; }
        .badge { display: inline-block; font-size: 12px; letter-spacing: 4px; padding: 6px 18px 6px 22px;
          border: 1px solid rgba(255,255,255,.3); border-radius: 999px; background: rgba(255,255,255,.08);
          backdrop-filter: blur(8px); margin-bottom: 16px; }
        h1 { font-size: 30px; margin: 0 0 8px; font-weight: 700; text-shadow: 0 2px 16px rgba(0,0,0,.5); }
        .sub { font-size: 13px; opacity: .75; margin-bottom: 26px; }
        .legend { font-size: 12px; opacity: .7; margin-bottom: 20px; }
        .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin: 0 4px 0 12px; }
      </style></head>
      <body><div class="wrap">
        <div class="badge">雲頂天宮</div>
        <h1>WhatsApp 账号矩阵</h1>
        <div class="sub">1 号 1 指纹浏览器 · 1 独立代理 IP · 扫码即上线</div>
        <div class="legend"><span class="dot" style="background:#22c55e"></span>在线
          <span class="dot" style="background:#f59e0b"></span>等待扫码
          <span class="dot" style="background:#ef4444"></span>异常/启动中
          <span style="margin-left:12px">页面每 30 秒自动刷新</span></div>
        <div>${cards || '<p>启动中…</p>'}</div>
      </div></body></html>`);
  });
  server.listen(STATUS_PORT, () => console.log(`状态页: http://localhost:${STATUS_PORT}`));
}

// ---------------- 主入口 ----------------

async function main() {
  if (!fs.existsSync(CHROME_PATH)) {
    console.error(`找不到 fingerprint-chromium: ${CHROME_PATH}`);
    console.error('请先运行 ./fetch-chromium.sh <Release资产直链> 下载，或用 -v 挂载到 /opt/fingerprint-chromium');
    process.exit(1);
  }
  const accounts = loadAccounts();
  console.log(`加载 ${accounts.length} 个 WhatsApp 账号，开始隔离启动…`);
  startStatusServer();
  await Promise.all(accounts.map((a, i) => launchAccount(a, i)));

  const shutdown = async () => {
    console.log('正在关闭浏览器…');
    for (const b of browsers) await b.close().catch(() => {});
    process.exit(0);
  };
  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);
}

main().catch((e) => {
  console.error('启动失败:', e.message);
  process.exit(1);
});
