#!/usr/bin/env node
/**
 * accounts.mjs — 统一账号注册表工具
 *   node scripts/accounts.mjs validate   校验 accounts/accounts.yaml
 *   node scripts/accounts.mjs sync-tg    把 telegram 代理写入 tg/tg_suite/accounts/<name>/proxy.txt
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import yaml from 'js-yaml';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const REGISTRY = path.join(ROOT, 'accounts', 'accounts.yaml');
const TG_ACCOUNTS_DIR = path.join(ROOT, 'tg', 'tg_suite', 'accounts');

const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{1,32}$/;
const PLATFORMS = new Set(['windows', 'linux', 'macos']);

function load() {
  if (!fs.existsSync(REGISTRY)) throw new Error(`找不到 ${REGISTRY}`);
  return yaml.load(fs.readFileSync(REGISTRY, 'utf8')) || {};
}

function checkProxy(p, label) {
  const errs = [];
  if (!p) return errs;
  let u;
  try {
    u = new URL(p);
  } catch {
    return [`${label}: 代理地址格式非法`];
  }
  if (!['http:', 'https:', 'socks5:', 'socks:'].includes(u.protocol)) errs.push(`${label}: 不支持的代理协议 ${u.protocol}`);
  if (!u.hostname || !u.port) errs.push(`${label}: 代理缺少 host 或 port`);
  return errs;
}

function validate() {
  const doc = load();
  const errs = [];
  const names = new Set();
  const seeds = new Set();

  for (const [i, a] of (doc.whatsapp || []).entries()) {
    const label = `whatsapp[${i}](${a.name || '?'})`;
    if (!a.name || !NAME_RE.test(a.name)) errs.push(`${label}: name 非法`);
    if (names.has(a.name)) errs.push(`${label}: name 重复`);
    names.add(a.name);
    if (!Number.isInteger(a.fingerprintSeed)) errs.push(`${label}: fingerprintSeed 必须是整数`);
    else {
      if (seeds.has(a.fingerprintSeed)) errs.push(`${label}: fingerprintSeed 与其他账号重复`);
      seeds.add(a.fingerprintSeed);
    }
    if (a.platform && !PLATFORMS.has(a.platform)) errs.push(`${label}: platform 必须是 windows|linux|macos`);
    errs.push(...checkProxy(a.proxy, label));
  }

  for (const [i, a] of (doc.telegram || []).entries()) {
    const label = `telegram[${i}](${a.name || '?'})`;
    if (!a.name || !NAME_RE.test(a.name)) errs.push(`${label}: name 非法`);
    if (names.has(a.name)) errs.push(`${label}: name 与 WhatsApp 账号重复`);
    names.add(a.name);
    errs.push(...checkProxy(a.proxy, label));
  }

  if (errs.length) {
    console.error('校验失败：');
    for (const e of errs) console.error('  - ' + e);
    process.exit(1);
  }
  console.log(
    `校验通过：whatsapp ${(doc.whatsapp || []).length} 个，telegram ${(doc.telegram || []).length} 个`
  );
}

function syncTg() {
  const doc = load();
  const tgs = doc.telegram || [];
  if (!tgs.length) {
    console.log('telegram 一节为空，无需同步');
    return;
  }
  let n = 0;
  for (const a of tgs) {
    if (!a.name || !NAME_RE.test(a.name)) throw new Error(`telegram 账号名非法: ${a.name}`);
    if (!a.proxy) {
      console.log(`- ${a.name}: 未填 proxy，跳过`);
      continue;
    }
    const dir = path.join(TG_ACCOUNTS_DIR, a.name);
    fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
    const f = path.join(dir, 'proxy.txt');
    fs.writeFileSync(f, String(a.proxy).trim() + '\n', { mode: 0o600 });
    console.log(`- ${a.name}: proxy 已写入 ${f}`);
    n++;
  }
  console.log(`同步完成：${n} 个 TG 账号代理已预置（导入 session/tdata 后生效）`);
}

const cmd = process.argv[2];
if (cmd === 'validate') validate();
else if (cmd === 'sync-tg') syncTg();
else {
  console.log('用法: node scripts/accounts.mjs <validate|sync-tg>');
  process.exit(1);
}
