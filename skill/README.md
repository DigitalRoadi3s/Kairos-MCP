# Kairos-MCP Skill

MCP server + OpenAI-compatible tool definitions for the [Kairos-MCP](https://github.com/DigitalRoadi3s/Kairos-MCP).

Exposes your iCloud, Nextcloud, or Google calendars as seven tools any LLM can call:

| Tool | What it does |
|---|---|
| `list_calendars` | Discover configured calendar IDs |
| `list_events` | Read events in any date range |
| `daily_brief` | Today's schedule, free slots, and attendee summary |
| `create_event` | Create one-off or recurring events |
| `update_event` | Partial update (PATCH semantics — only provide what you want to change) |
| `delete_event` | Delete an event (locked within 15 min of start) |
| `gateway_health` | Check connection status and circuit-breaker state |

## Prerequisites

1. Kairos-MCP must already be running — by default at `http://localhost:8080`.
2. Python 3.11+

```bash
pip install -r skill/requirements.txt
```

---

## Claude Desktop / Claude Code (MCP)

Add this block to `claude_desktop_config.json`
(macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "caldav": {
      "command": "python3",
      "args": ["/absolute/path/to/caldav-gateway/skill/mcp_server.py"],
      "env": {
        "CALDAV_GATEWAY_URL": "http://localhost:8080"
      }
    }
  }
}
```

Restart Claude Desktop. The seven CalDAV tools appear automatically in every conversation.

If your gateway runs on a different host (e.g. a homelab box at `http://192.168.1.50:8080`), update `CALDAV_GATEWAY_URL`.

---

## OpenClaw

OpenClaw proxies stdio MCP servers to local models. Add the same entry to your OpenClaw config under `mcp_servers`:

```json
{
  "mcp_servers": {
    "caldav": {
      "command": "python3",
      "args": ["/absolute/path/to/caldav-gateway/skill/mcp_server.py"],
      "env": {
        "CALDAV_GATEWAY_URL": "http://localhost:8080"
      }
    }
  }
}
```

OpenClaw handles the stdio ↔ local model bridging — no other changes needed.

---

## Hermes / OpenAI-compatible local LLMs

Use `skill/tools.json` — it follows the OpenAI function-calling format supported by Hermes (NousResearch), LiteLLM, Ollama (with compatible models), and most local inference frameworks.

### LiteLLM example

```python
import json
import httpx
import litellm

tools = json.load(open("skill/tools.json"))
GATEWAY = "http://localhost:8080"

def dispatch(tool_name: str, args: dict) -> str:
    """Route a tool call from the LLM to the CalDAV gateway."""
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
