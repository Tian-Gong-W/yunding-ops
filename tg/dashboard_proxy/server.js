"use strict";

const http = require("http");
const httpProxy = require("http-proxy");

const PORT = Number(process.env.PORT || 3000);
const TARGET = process.env.DASHBOARD_ORIGIN || "http://66.42.48.141:7860";
const REQUEST_TIMEOUT_MS = Number(process.env.REQUEST_TIMEOUT_MS || 65_000);

let target;
try {
  target = new URL(TARGET);
  if (!/^https?:$/.test(target.protocol)) throw new Error("Unsupported protocol");
} catch (error) {
  console.error("Invalid DASHBOARD_ORIGIN:", error.message);
  process.exit(1);
}

const proxy = httpProxy.createProxyServer({
  target: target.origin,
  changeOrigin: true,
  ws: true,
  proxyTimeout: REQUEST_TIMEOUT_MS,
  timeout: REQUEST_TIMEOUT_MS,
  xfwd: true,
});

proxy.on("proxyReq", (proxyReq, req) => {
  proxyReq.setHeader("Host", target.host);
  proxyReq.setHeader("X-Forwarded-Host", req.headers.host || "");
  proxyReq.setHeader("X-Forwarded-Proto", "https");
});

proxy.on("error", (error, _req, response) => {
  console.error("Upstream proxy error:", error.code || error.message);
  if (!response.headersSent) {
    response.writeHead(502, { "Content-Type": "application/json; charset=utf-8" });
  }
  response.end(JSON.stringify({ ok: false, error: "Dashboard upstream unavailable" }));
});

const server = http.createServer((req, res) => {
  if (req.url === "/health") {
    res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
    res.end(JSON.stringify({ ok: true }));
    return;
  }
  proxy.web(req, res);
});

server.on("upgrade", (req, socket, head) => proxy.ws(req, socket, head));
server.listen(PORT, "0.0.0.0", () => {
  console.log(`TopMen dashboard proxy listening on ${PORT}; upstream ${target.origin}`);
});
