#!/usr/bin/env node
/**
 * vless-bridge.mjs — VLESS/Reality 代理桥接
 * ------------------------------------------------------------
 * 背景：fingerprint-chromium 只认 http/https/socks5 代理，不认 vless://。
 * 本脚本在容器启动时：
 *   1. 读账号配置（ACCOUNTS_YAML 优先，与 worker.js 同逻辑）；
 *   2. 把每个 vless:// 代理转成 sing-box 配置：
 *        本地 127.0.0.1:10801+i (socks5, 无密码)
 *          -> sing-box vless 出站（Reality + Vision + uTLS chrome 指纹）
 *      每个账号独立入站/出站 + 路由规则，代理严格隔离；
 *   3. 把账号配置改写为 socks5://127.0.0.1:PORT，输出到 /tmp/accounts.bridge.json；
 *   4. 打印改写后的配置文件路径（供 entrypoint.sh 切换 ACCOUNTS_JSON）。
 *
 * 没有 vless 代理时：不写任何文件，直接退出（entrypoint 按原逻辑启动）。
 *
 * 支持的 vless 链接形式：
 *   - 标准：vless://uuid@host:port?pbk=..&sid=..&peer=..&fingerprint=..&xtls=2&udp=1
 *   - 混淆：vless://base64("none:uuid@host:port")?同上
 */
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';
import yaml from 'js-yaml';

const OUT_CONFIG = '/tmp/sing-box.json';
const OUT_ACCOUNTS = '/tmp/accounts.bridge.json';
const BASE_PORT = 10801;

function loadAccounts() {
  const yamlPath = process.env.ACCOUNTS_YAML;
  if (yamlPath && fs.existsSync(yamlPath)) {
    const doc = yaml.load(fs.readFileSync(yamlPath, 'utf8')) || {};
    return doc.whatsapp || [];
  }
  const p = process.env.ACCOUNTS_JSON || '/config/accounts.json';
  const list = JSON.parse(fs.readFileSync(p, 'utf8'));
  if (!Array.isArray(list)) throw new Error('accounts.json 格式错误（需要数组）');
  return list;
}

// 不用 new URL() 解析 authority：非标准 scheme 下 hostname 会被小写化，
// 会破坏 base64。用正则直接切。
function parseVless(raw) {
  const m = String(raw || '').match(/^vless:\/\/([^?]+)(?:\?(.*))?$/);
  if (!m) throw new Error('vless 链接格式非法');
  const authority = m[1];
  const q = new URLSearchParams(m[2] || '');
  let uuid, server, port;
  if (authority.includes('@')) {
    const at = authority.lastIndexOf('@');
    uuid = decodeURIComponent(authority.slice(0, at));
    const hp = authority.slice(at + 1);
    const c = hp.lastIndexOf(':');
    server = hp.slice(0, c);
    port = Number(hp.slice(c + 1));
  } else {
    const dec = Buffer.from(authority, 'base64').toString('utf8');
    const dm = dec.match(/^[^:]*:([^@]+)@([^:]+):(\d+)$/);
    if (!dm) throw new Error('vless 混淆地址 base64 解码失败');
    uuid = dm[1];
    server = dm[2];
    port = Number(dm[3]);
  }
  if (!/^[0-9a-fA-F-]{36}$/.test(uuid)) throw new Error(`vless uuid 非法: ${uuid}`);
  if (!server || !port) throw new Error('vless 缺少 host/port');
  const publicKey = q.get('pbk') || '';
  if (!publicKey) throw new Error('vless 缺少 Reality 公钥 (pbk)');
  const xtls = q.get('xtls') || q.get('flow') || '';
  return {
    uuid,
    server,
    port,
    flow: xtls === '2' ? 'xtls-rprx-vision' : xtls === '1' ? 'xtls-rprx-origin' : '',
    serverName: q.get('peer') || q.get('sni') || server,
    publicKey,
    shortId: q.get('sid') || q.get('shortId') || '',
    fingerprint: q.get('fingerprint') || q.get('fp') || 'chrome',
    xudp: q.get('udp') === '1',
  };
}

function main() {
  const accounts = loadAccounts();
  const vlessAccounts = accounts.filter(
    (a) => typeof a.proxy === 'string' && a.proxy.startsWith('vless://')
  );
  if (vlessAccounts.length === 0) {
    console.log('vless-bridge: 无 vless 代理，跳过桥接');
    return;
  }

  const inbounds = [];
  const outbounds = [];
  const rules = [];
  const rewritten = accounts.map((a, i) => {
    if (typeof a.proxy !== 'string' || !a.proxy.startsWith('vless://')) return a;
    const v = parseVless(a.proxy);
    const port = BASE_PORT + i;
    const inTag = `in-${a.name}`;
    const outTag = `out-${a.name}`;
    inbounds.push({ type: 'socks', tag: inTag, listen: '127.0.0.1', listen_port: port });
    const ob = {
      type: 'vless',
      tag: outTag,
      server: v.server,
      server_port: v.port,
      uuid: v.uuid,
      tls: {
        enabled: true,
        server_name: v.serverName,
        utls: { enabled: true, fingerprint: v.fingerprint },
        reality: { enabled: true, public_key: v.publicKey, short_id: v.shortId },
      },
    };
    if (v.flow) ob.flow = v.flow;
    if (v.xudp) ob.packet_encoding = 'xudp';
    outbounds.push(ob);
    rules.push({ inbound: inTag, outbound: outTag });
    console.log(`vless-bridge: ${a.name} -> socks5://127.0.0.1:${port} (vless ${v.server}:${v.port})`);
    return { ...a, proxy: `socks5://127.0.0.1:${port}` };
  });
  outbounds.push({ type: 'direct', tag: 'direct' });

  const config = {
    log: { level: 'warn' },
    inbounds,
    outbounds,
    route: { rules, final: 'direct' },
  };
  fs.writeFileSync(OUT_CONFIG, JSON.stringify(config, null, 2));
  // 校验配置合法性，失败直接抛错（entrypoint 会中止启动，避免带病运行）
  execFileSync('sing-box', ['check', '-c', OUT_CONFIG], { stdio: 'pipe' });
  fs.writeFileSync(OUT_ACCOUNTS, JSON.stringify(rewritten, null, 2));
  console.log(`vless-bridge: sing-box 配置已生成 ${OUT_CONFIG}，账号配置已改写 ${OUT_ACCOUNTS}`);
  // 最后一行只打印路径，供 entrypoint.sh 捕获
  console.log(`BRIDGE_ACCOUNTS=${OUT_ACCOUNTS}`);
}

main();
