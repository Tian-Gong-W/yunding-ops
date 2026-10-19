#!/usr/bin/env node
/**
 * check-proxies.mjs — 代理实测校验
 *   node scripts/check-proxies.mjs
 *
 * 对 accounts/accounts.yaml 里每个 whatsapp / telegram 账号：
 *   1. 走该账号的代理请求 ip-api.com，拿到出口 IP + 国家
 *   2. 根据手机号国际区号推导期望国家，核对是否一致（防封关键）
 *   3. 输出每号的 OK / 出口国不符 / 连不通
 *
 * 占位代理（如 1.2.3.4）会自动跳过并提示未填写。
 * 依赖 curl（服务器上一般自带）。任一账号不通过则退出码为 1。
 */
import { execFile } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import yaml from 'js-yaml';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const REGISTRY = path.join(ROOT, 'accounts', 'accounts.yaml');

// 国际区号 -> 期望出口国家（按长度降序排列，优先匹配更长的前缀）
const PREFIX_COUNTRY = [
  ['+852', 'HK'], ['+853', 'MO'], ['+886', 'TW'], ['+86', 'CN'],
  ['+81', 'JP'], ['+82', 'KR'], ['+65', 'SG'], ['+60', 'MY'],
  ['+66', 'TH'], ['+84', 'VN'], ['+63', 'PH'], ['+62', 'ID'],
  ['+91', 'IN'], ['+971', 'AE'], ['+966', 'SA'], ['+90', 'TR'],
  ['+44', 'GB'], ['+49', 'DE'], ['+33', 'FR'], ['+34', 'ES'],
  ['+39', 'IT'], ['+31', 'NL'], ['+61', 'AU'], ['+64', 'NZ'],
  ['+7', 'RU'], ['+380', 'UA'], ['+55', 'BR'], ['+52', 'MX'],
  ['+54', 'AR'], ['+56', 'CL'], ['+57', 'CO'], ['+51', 'PE'],
  ['+1', 'US'], // +1 覆盖美加，出口国 US/CA 都算通过
];

const PLACEHOLDER_HOSTS = new Set(['1.2.3.4', '5.6.7.8', '9.10.11.12', 'example.com', 'localhost']);

function expectedCountry(phone) {
  const digits = String(phone || '').replace(/[^\d]/g, '');
  for (const [prefix, cc] of PREFIX_COUNTRY) {
    if (('+' + digits).startsWith(prefix)) return { code: cc, prefix };
  }
  return null;
}

function isPlaceholder(proxy) {
  if (!proxy) return true;
  try {
    const u = new URL(proxy);
    return PLACEHOLDER_HOSTS.has(u.hostname) || u.hostname.includes('example') || u.username === 'user';
  } catch {
    return true;
  }
}

function curlProxy(proxy) {
  // socks: 统一成 socks5h（远端解析域名，避免本地 DNS 泄漏）
  const normalized = proxy.replace(/^socks:/i, 'socks5h:');
  return new Promise((resolve) => {
    execFile(
      'curl',
      ['-sS', '--max-time', '20', '--proxy', normalized, 'http://ip-api.com/json/?fields=status,message,countryCode,query'],
      { timeout: 25000 },
      (err, stdout, stderr) => {
        if (err) {
          resolve({ ok: false, error: (stderr || err.message).trim().split('\n').pop() });
          return;
        }
        try {
          const j = JSON.parse(stdout);
          if (j.status === 'success') resolve({ ok: true, ip: j.query, country: j.countryCode });
          else resolve({ ok: false, error: j.message || '查询失败' });
        } catch {
          resolve({ ok: false, error: '返回不是合法 JSON' });
        }
      }
    );
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  if (!fs.existsSync(REGISTRY)) {
    console.error(`找不到 ${REGISTRY}`);
    process.exit(1);
  }
  const doc = yaml.load(fs.readFileSync(REGISTRY, 'utf8')) || {};
  const accounts = [
    ...(doc.whatsapp || []).map((a) => ({ ...a, kind: 'whatsapp' })),
    ...(doc.telegram || []).map((a) => ({ ...a, kind: 'telegram' })),
  ];
  if (!accounts.length) {
    console.log('注册表里没有账号');
    process.exit(0);
  }

  let bad = 0;
  let checked = 0;
  console.log(`逐个实测代理（${accounts.length} 个账号）：\n`);
  for (const a of accounts) {
    const label = `${a.kind}/${a.name}`;
    if (isPlaceholder(a.proxy)) {
      console.log(`- ${label}: 跳过（代理还是占位符，先填真实代理）`);
      continue;
    }
    if (/^vless:/i.test(a.proxy || '')) {
      console.log(`- ${label}: 跳过（vless 需经 sing-box 桥接，curl 测不了；看 wa-worker 启动日志确认）`);
      continue;
    }
    checked++;
    const exp = expectedCountry(a.phone);
    const expNote = exp ? `期望 ${exp.code}（${a.phone} 区号 ${exp.prefix}）` : '手机号区号未知，仅测连通性';
    process.stdout.write(`- ${label}: 测速中… `);
    const r = await curlProxy(a.proxy);
    await sleep(1100); // 对免费查询接口礼貌一点
    if (!r.ok) {
      bad++;
      console.log(`\n  ✗ 连不通：${r.error}`);
      continue;
    }
    const match = !exp || r.country === exp.code || (exp.code === 'US' && r.country === 'CA');
    if (match) {
      console.log(`\n  ✓ 出口 ${r.ip}（${r.country}），${expNote}——一致`);
    } else {
      bad++;
      console.log(`\n  ✗ 出口 ${r.ip}（${r.country}），${expNote}——国家不符，扫码前请换代理！`);
    }
  }
  if (!checked) {
    console.log('\n都没填真实代理，先填完再测。');
  } else {
    console.log(bad ? `\n${bad} 个账号有问题，请处理后再扫码。` : '\n全部通过，可以扫码了。');
  }
  process.exit(bad ? 1 : 0);
}

main().catch((e) => {
  console.error(e.message);
  process.exit(1);
});
