# Kairos-MCP Skill

MCP server + OpenAI-compatible tool definitions for [Kairos-MCP](https://github.com/DigitalRoadi3s/Kairos-MCP) — a multi-agent CalDAV gateway.

**Before using this**: if you only need Claude to talk to your calendar, you probably don't need any of this. Check these first:
- **iCloud + Claude Desktop**: [`mcp-calendars`](https://github.com/lucasheight/mcp-calendars) — one install, no gateway required
- **Google Calendar + Claude**: [Google's official MCP server](https://developers.google.com/workspace/calendar/api/guides/configure-mcp-server) at `calendarmcp.googleapis.com` — 9 tools, OAuth handled for you

Use this skill when you're running the Kairos-MCP gateway specifically — because you need multiple models (Claude + Hermes + a local LLM via OpenClaw) sharing the same calendar backend.

---

Exposes seven tools:

| Tool | What it does |
|---|---|
| `list_calendars` | Discover configured calendar IDs |
| `list_events` | Read events in any date range |
| `daily_brief` | Today's schedule, free slots, and attendee summary |
| `create_event` | Create one-off or recurring events |
| `update_event` | Partial update (PATCH semantics) |
| `delete_event` | Delete an event (locked within 15 min of start) |
| `gateway_health` | Check connection status and circuit-breaker state |

## Prerequisites

The Kairos-MCP gateway must already be running — by default at `http://localhost:8080`. Then:

```bash
pip install -r skill/requirements.txt
```

---

## Claude Desktop / Claude Code (MCP)

Add to `claude_desktop_config.json`
(macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "caldav": {
      "command": "python3",
      "args": ["/absolute/path/to/kairos-mcp/skill/mcp_server.py"],
      "env": {
        "CALDAV_GATEWAY_URL": "http://localhost:8080"
      }
    }
  }
}
```

Restart Claude Desktop. If your gateway is on a different host (e.g. a homelab box at `http://192.168.1.50:8080`), update `CALDAV_GATEWAY_URL`.

If you're using Google Calendar with Claude, you can add Google's official MCP server alongside this one:

```json
{
  "mcpServers": {
    "caldav": {
      "command": "python3",
      "args": ["/absolute/path/to/kairos-mcp/skill/mcp_server.py"],
      "env": { "CALDAV_GATEWAY_URL": "http://localhost:8080" }
    },
    "google-calendar": {
      "serverUrl": "https://calendarmcp.googleapis.com/mcp/v1",
      "oauth": {
        "clientId": "YOUR_OAUTH_CLIENT_ID",
        "clientSecret": "YOUR_OAUTH_CLIENT_SECRET"
      }
    }
  }
}
```

---

## OpenClaw

OpenClaw proxies stdio MCP servers to local models. Add to your OpenClaw config under `mcp_servers`:

```json
{
  "mcp_servers": {
    "caldav": {
      "command": "python3",
      "args": ["/absolute/path/to/kairos-mcp/skill/mcp_server.py"],
      "env": {
        "CALDAV_GATEWAY_URL": "http://localhost:8080"
      }
    }
  }
}
```

---

## Hermes / OpenAI-compatible local LLMs

Use `skill/tools.json` — OpenAI function-calling format, works with Hermes (NousResearch), LiteLLM, Ollama, and most local inference frameworks.

### LiteLLM example

```python
import json
import httpx
import litellm

tools = json.load(open("skill/tools.json"))
GATEWAY = "http://localhost:8080"

def dispatch(tool_name: str, args: dict) -> str:
    cid = args.get("calendar_id", "")
    uid = args.get("uid", "")
    routes = {
        "caldav_list_calendars":  ("GET",    "/api/v1/calendars"),
        "caldav_list_events":     ("GET",    f"/api/v1/calendars/{cid}/events"),
        "caldav_daily_brief":     ("GET",    f"/api/v1/calendars/{cid}/today"),
        "caldav_create_event":    ("POST",   f"/api/v1/calendars/{cid}/events"),
        "caldav_update_event":    ("PUT",    f"/api/v1/calendars/{cid}/events/{uid}"),
        "caldav_delete_event":    ("DELETE", f"/api/v1/calendars/{cid}/events/{uid}"),
        "caldav_gateway_health":  ("GET",    "/health"),
    }
    method, path = routes[tool_name]
    skip = {"calendar_id", "uid"}
    params = {k: v for k, v in args.items() if k not in skip} if method == "GET" else {}
    body = {k: v for k, v in args.items() if k not in skip} if method in ("POST", "PUT") else None
    r = httpx.request(method, f"{GATEWAY}{path}", params=params or None, json=body, timeout=30)
    r.raise_for_status()
    return r.text

messages = [{"role": "user", "content": "What's on my icloud calendar today?"}]
response = litellm.completion(model="ollama/hermes3", messages=messages, tools=tools)

for choice in response.choices:
    if choice.message.tool_calls:
        for tc in choice.message.tool_calls:
            result = dispatch(tc.function.name, json.loads(tc.function.arguments))
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
        final = litellm.completion(model="ollama/hermes3", messages=messages, tools=tools)
        print(final.choices[0].message.content)
```

### Recommended system prompt

```
You have access to a CalDAV calendar gateway. Always call caldav_list_calendars
first if you don't already know the calendar ID. Use caldav_daily_brief for any
question about today's schedule. Always confirm with the user before calling
caldav_delete_event — deletions are permanent. When creating recurring events,
include a recurrence_rule in RFC 5545 RRULE format (e.g. FREQ=WEEKLY;BYDAY=MO,WE,FR).
```

---

## Files

```
skill/mcp_server.py    MCP server (Claude Desktop / Claude Code / OpenClaw)
skill/tools.json       OpenAI-compatible tool definitions (Hermes / LiteLLM / Ollama)
skill/requirements.txt
```
