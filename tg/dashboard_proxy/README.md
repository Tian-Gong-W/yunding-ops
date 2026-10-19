# TopMen Dashboard HTTPS Proxy

This Railway service exposes the persistent-server dashboard through `https://topmen.app` without moving its database, crawling process, Telegram configuration, or runtime state.

The service proxies all HTTP and SSE routes to the configured `DASHBOARD_ORIGIN`, including the dashboard's `/stream` endpoint. Railway terminates public HTTPS; the VPS remains the only host running the collector and SQLite database.

## Required Railway variables

| Variable | Value |
|---|---|
| `DASHBOARD_ORIGIN` | `http://66.42.48.141:7860` |
| `REQUEST_TIMEOUT_MS` | `65000` |

The `/health` endpoint validates the proxy container itself. It does not disclose the upstream URL to normal browser traffic.
