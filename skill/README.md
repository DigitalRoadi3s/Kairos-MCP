# Kairos-MCP tools for other agents

OpenAI-compatible tool definitions and chat shortcuts for [Kairos-MCP](https://github.com/DigitalRoadi3s/Kairos-MCP), a multi-agent CalDAV gateway.

**Before using this**: if you only need Claude to talk to your calendar, you probably don't need any of this. Check these first:
- **iCloud + Claude Desktop**: [`mcp-calendars`](https://github.com/lucasheight/mcp-calendars): one install, no gateway required
- **Google Calendar + Claude**: [Google's official MCP server](https://developers.google.com/workspace/calendar/api/guides/configure-mcp-server) at `calendarmcp.googleapis.com`, 9 tools, OAuth handled for you

Use this skill when you're running the Kairos-MCP gateway specifically, because you need multiple models (Claude + Hermes + a local LLM via OpenClaw) sharing the same calendar backend.

---

## MCP clients

MCP is built into the gateway now: point Claude Code, claude.ai, Claude Desktop or OpenClaw at `https://<host>/mcp`, or run `kairos mcp-stdio` locally. The separate `skill/mcp_server.py` from 1.x is gone. Setup is in the [main README](../README.md#connecting-clients).

This folder is for everything else.

---

## Tool reference

The MCP tools and `tools.json` cover the same eight operations. MCP names are unprefixed; `tools.json` names are prefixed with `caldav_`.

| Tool | Parameters | Notes |
|---|---|---|
| `list_calendars` | none | Call first to get calendar ids |
| `list_events` | `calendar_id`, `date_min`, `date_max`, `limit` (1 to 1000) | One entry per occurrence of a recurring event. MCP also takes `timezone` |
| `get_event` | `calendar_id`, `uid` | For a recurring event, returns the series |
| `daily_brief` | `calendar_id`, `timezone` | Defaults to the calendar's zone |
| `create_event` | `calendar_id`, `title`, `start_time`, `end_time`, `description`, `location`, `all_day`, `attendees`, `recurrence_rule`, `timezone`, `status` | Times need a UTC offset. Attendees may get invitations |
| `update_event` | `calendar_id`, `uid`, then any of `title`, `description`, `location`, `start_time`, `end_time`, `all_day`, `attendees`, `status` | Only what you pass changes |
| `delete_event` | `calendar_id`, `uid` | Permanent; deletes a whole series |
| `gateway_health` | none | Connection and circuit-breaker state |

`status` is `confirmed`, `tentative` or `cancelled`. `attendees` is `[{"email": "...", "name": "..."}]`.

Change rules: a one-off event is locked from 15 minutes before it starts. A recurring series can be edited at any time except its times and its `recurrence_rule`; delete and recreate it to change those.

**Common RRULE examples:**

| Pattern | RRULE |
|

---

## Hermes / LiteLLM / Ollama

Use `tools.json` with any OpenAI function-calling framework, calling the REST API with an API token.

### System prompt

```
You have access to a CalDAV calendar gateway. Always call caldav_list_calendars
first if you don't already know the calendar ID. Use caldav_daily_brief for any
question about today's schedule. Always confirm with the user before calling
caldav_delete_event; deletions are permanent. When creating recurring events,
include a recurrence_rule in RFC 5545 RRULE format (e.g. FREQ=WEEKLY;BYDAY=MO,WE,FR).
```

### LiteLLM (Hermes, any Ollama model)

```python
import json
import os

import httpx
import litellm

tools = json.load(open("skill/tools.json"))
GATEWAY = "http://localhost:8080"
HEADERS = {"Authorization": f"Bearer {os.environ['KAIROS_TOKEN']}"}


def dispatch(tool_name: str, args: dict) -> str:
    cid = args.get("calendar_id", "")
    uid = args.get("uid", "")
    routes = {
        "caldav_list_calendars": ("GET", "/api/v1/calendars"),
        "caldav_list_events": ("GET", f"/api/v1/calendars/{cid}/events"),
        "caldav_get_event": ("GET", f"/api/v1/calendars/{cid}/events/{uid}"),
        "caldav_daily_brief": ("GET", f"/api/v1/calendars/{cid}/today"),
        "caldav_create_event": ("POST", f"/api/v1/calendars/{cid}/events"),
        "caldav_update_event": ("PATCH", f"/api/v1/calendars/{cid}/events/{uid}"),
        "caldav_delete_event": ("DELETE", f"/api/v1/calendars/{cid}/events/{uid}"),
        "caldav_gateway_health": ("GET", "/health"),
    }
    method, path = routes[tool_name]
    rest = {k: v for k, v in args.items() if k not in {"calendar_id", "uid"}}
    params = rest if method == "GET" else None
    body = rest if method in ("POST", "PATCH") else None
    r = httpx.request(method, f"{GATEWAY}{path}", params=params, json=body, headers=HEADERS, timeout=30)
    return r.text  # errors come back as {"detail": ..., "error": ...}, which the model can read


def chat(user_message: str, model: str = "ollama/hermes3"):
    messages = [{"role": "user", "content": user_message}]
    while True:
        response = litellm.completion(model=model, messages=messages, tools=tools)
        msg = response.choices[0].message
        if not msg.tool_calls:
            return msg.content
        messages.append(msg)
        for tc in msg.tool_calls:
            result = dispatch(tc.function.name, json.loads(tc.function.arguments))
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})


print(chat("What's on my icloud calendar this week?"))
```

### Ollama directly (tool-capable models)

Pass `tools.json` in the `/api/chat` request body:

```python
import json, httpx

tools = json.load(open("skill/tools.json"))

response = httpx.post(
    "http://localhost:11434/api/chat",
    json={
        "model": "hermes3",
        "messages": [{"role": "user", "content": "What's on my calendar today?"}],
        "tools": tools,
        "stream": False,
    },
)

# Handle tool_calls in response.json()["message"]["tool_calls"] with dispatch() above
```

Tool-capable models for Ollama: `hermes3`, `qwen2.5`, `mistral`, `llama3.1` (8B+), `command-r`.

---

## Chat slash commands

For Claude Desktop / claude.ai's saved-prompt `/commands` (not Claude Code's),
see [`commands.md`](commands.md) for ready-to-paste shortcuts like `/agenda`,
`/today`, and `/calendars`.

---

## Files

```
skill/tools.json       OpenAI tool definitions: Hermes, LiteLLM, Ollama
skill/commands.md      Chat slash-command shortcuts
```
