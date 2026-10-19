/**
 * 本地 CONNECT 中转
 * ------------------------------------------------------------
 * 解决 fingerprint-chromium 的 --proxy-server 不支持代理密码鉴权的问题：
 *   浏览器 --proxy-server=http://127.0.0.1:PORT（无密码）
 *     -> 本模块（同容器内回环地址，不出网）
 *     -> 上游 socks5 代理（支持用户名/密码认证，RFC 1929）
 *
 * 每个账号起一个独立中转端口，账号之间代理完全隔离。
 */
const http = require('http');
const net = require('net');

// 与上游 socks5 建立连接（含用户名/密码认证），返回已连通的 socket
async function dialSocks5(proxyUrl, targetHost, targetPort) {
  const u = new URL(proxyUrl);
  const sock = await new Promise((resolve, reject) => {
    const s = net.connect({ host: u.hostname, port: Number(u.port || 1080) });
    s.once('connect', () => resolve(s));
    s.once('error', reject);
  });
  const readN = (n) =>
    new Promise((resolve, reject) => {
      let buf = Buffer.alloc(0);
      const onData = (d) => {
        buf = Buffer.concat([buf, d]);
        if (buf.length >= n) {
          sock.off('data', onData);
          resolve(buf.subarray(0, n));
        }
      };
      sock.on('data', onData);
      sock.once('error', reject);
    });

  // 1. 协商认证方式：0x00 无需认证 / 0x02 用户名密码
  const needAuth = Boolean(u.username || u.password);
  sock.write(Buffer.from([0x05, 0x01, needAuth ? 0x02 : 0x00]));
  const mSel = await readN(2);
  if (mSel[1] === 0xff) throw new Error('socks5: 上游不支持任何认证方式');

  // 2. 用户名/密码认证（RFC 1929）
  if (mSel[1] === 0x02) {
    const user = Buffer.from(decodeURIComponent(u.username || ''));
    const pass = Buffer.from(decodeURIComponent(u.password || ''));
    sock.write(Buffer.concat([Buffer.from([0x01, user.length]), user, Buffer.from([pass.length]), pass]));
    const aRep = await readN(2);
    if (aRep[1] !== 0x00) throw new Error('socks5: 用户名/密码认证失败');
  }

  // 3. CONNECT 目标地址（域名形式，交由上游解析，避免本地 DNS 泄漏）
  const hostBuf = Buffer.from(targetHost);
  sock.write(
    Buffer.concat([
      Buffer.from([0x05, 0x01, 0x00, 0x03, hostBuf.length]),
      hostBuf,
      Buffer.from([(targetPort >> 8) & 0xff, targetPort & 0xff]),
    ])
  );
  const rep = await readN(4);
  if (rep[1] !== 0x00) throw new Error('socks5: CONNECT 失败，code=' + rep[1]);
  return sock; // 连接已建立，后续字节为 BND.ADDR/PORT，可忽略
}

// 在 127.0.0.1:listPort 起一个无密码的 HTTP CONNECT 中转
// 返回 { server, close }：close 会先销毁所有隧道再关监听，避免 EADDRINUSE
async function startLocalRelay(proxyUrl, listenPort) {
  const sockets = new Set();
  const track = (s) => {
    sockets.add(s);
    s.on('close', () => sockets.delete(s));
  };
  const server = http.createServer();
  server.on('connection', track);
  server.on('connect', async (req, clientSocket, head) => {
    try {
      const [host, port] = req.url.split(':');
      const upstream = await dialSocks5(proxyUrl, host, Number(port || 443));
      track(upstream);
      clientSocket.write('HTTP/1.1 200 Connection Established\r\n\r\n');
      if (head && head.length) upstream.write(head);
      clientSocket.pipe(upstream).pipe(clientSocket);
      clientSocket.on('error', () => upstream.destroy());
      upstream.on('error', () => clientSocket.destroy());
    } catch (e) {
      try {
        clientSocket.write('HTTP/1.1 502 Bad Gateway\r\n\r\n');
      } catch (_) {}
      clientSocket.destroy();
    }
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(listenPort, '127.0.0.1', resolve);
  });
  return {
    server,
    async close() {
      for (const s of sockets) s.destroy();
      sockets.clear();
      await new Promise((res) => server.close(res));
    },
  };
}

module.exports = { startLocalRelay };
