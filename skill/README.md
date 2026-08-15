# Kairos-MCP Skill

MCP server + OpenAI-compatible tool definitions for [Kairos-MCP](https://github.com/DigitalRoadi3s/Kairos-MCP) — a multi-agent CalDAV gateway.

**Before using this**: if you only need Claude to talk to your calendar, you probably don't need any of this. Check these first:
- **iCloud + Claude Desktop**: [`mcp-calendars`](https://github.com/lucasheight/mcp-calendars) — one install, no gateway required
- **Google Calendar + Claude**: [Google's official MCP server](https://developers.google.com/workspace/calendar/api/guides/configure-mcp-server) at `calendarmcp.googleapis.com` — 9 tools, OAuth handled for you

Use this skill when you're running the Kairos-MCP gateway specifically — because you need multiple models (Claude + Hermes + a local LLM via OpenClaw) sharing the same calendar backend.

---

## Prerequisites

The Kairos-MCP gateway must already be running — by default at `http://localhost:8080`. Then install the skill's dependencies:

```bash
pip install -r skill/requirements.txt
```

---

## Tool reference

All seven tools are available in both the MCP server (for Claude / OpenClaw) and `tools.json` (for Hermes / LiteLLM / Ollama). MCP tool names are unprefixed; `tools.json` names are prefixed with `caldav_`.

### `list_calendars`

Returns the IDs, names, and writable status of every calendar source configured in the gateway. Call this first if you don't already know the calendar ID.

No parameters.

---

### `list_events`

Returns events in a date range, including UIDs, titles, times, attendees, location, and recurrence rule.

| Parameter | Type | Required | Default | Notes |
|---|---|---|---|---|
| `calendar_id` | string | yes | — | From `list_calendars` |
| `date_min` | string | no | Today | ISO 8601 with timezone offset |
| `date_max` | string | no | +30 days from `date_min` | ISO 8601 with timezone offset |
| `limit` | integer | no | 100 | 1–1000 |

UIDs returned here are what `update_event` and `delete_event` require.

---

### `daily_brief`

Returns today's events plus a free-slot analysis (all gaps > 15 minutes during working hours), total calendar time for the day, and a ranked attendee list.

| Parameter | Type | Required | Default | Notes |
|---|---|---|---|---|
| `calendar_id` | string | yes | — | From `list_calendars` |
| `timezone` | string | no | `UTC` | IANA name, e.g. `America/New_York` |

---

### `create_event`

Creates a new event and returns it with its UID.

| Parameter | Type | Required | Default | Notes |
|---|---|---|---|---|
| `calendar_id` | string | yes | — | From `list_calendars` |
| `title` | string | yes | — | Max 200 characters |
| `start_time` | string | yes | — | ISO 8601, must include timezone offset |
| `end_time` | string | yes | — | ISO 8601, must be after `start_time` |
| `description` | string | no | — | Free text |
| `location` | string | no | — | Free text |
| `all_day` | boolean | no | `false` | Times ignored when true |
| `attendees` | array | no | `[]` | `[{"email": "...", "name": "..."}]` |
| `recurrence_rule` | string | no | — | RFC 5545 RRULE, e.g. `FREQ=WEEKLY;BYDAY=MO,WE,FR` |
| `status` | string | no | `confirmed` | `confirmed`, `tentative`, or `cancelled` |

Events starting within 15 minutes cannot be modified or deleted after creation.

**Common RRULE examples:**

| Pattern | RRULE |
|---|---|
| Every weekday | `FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR` |
| Every Monday | `FREQ=WEEKLY;BYDAY=MO` |
| Mon / Wed / Fri | `FREQ=WEEKLY;BYDAY=MO,WE,FR` |
| Every 2 weeks | `FREQ=WEEKLY;INTERVAL=2` |
| Monthly on the 1st | `FREQ=MONTHLY;BYMONTHDAY=1` |
| Daily for 5 days | `FREQ=DAILY;COUNT=5` |
| Every year | `FREQ=YEARLY` |

---

### `update_event`

Updates an existing event using PATCH semantics — only fields you provide are changed; everything else stays as-is.

| Parameter | Type | Required | Default | Notes |
|---|---|---|---|---|
| `calendar_id` | string | yes | — | From `list_calendars` |
| `uid` | string | yes | — | From `list_events` or `create_event` |
| `title` | string | no | unchanged | |
| `description` | string | no | unchanged | |
| `location` | string | no | unchanged | |
| `start_time` | string | no | unchanged | Cannot be changed on a recurring event |
| `end_time` | string | no | unchanged | Cannot be changed on a recurring event |
| `all_day` | boolean | no | unchanged | |
| `attendees` | array | no | unchanged | Replaces the full attendee list if provided |
| `status` | string | no | unchanged | `confirmed`, `tentative`, or `cancelled` |

The `recurrence_rule` of a recurring event is immutable via update — delete and recreate to change the recurrence pattern. Events starting within 15 minutes are locked.

---

### `delete_event`

Permanently deletes an event. Cannot be undone.

| Parameter | Type | Required | Notes |
|---|---|---|---|
| `calendar_id` | string | yes | From `list_calendars` |
| `uid` | string | yes | From `list_events` or `create_event` |

Events starting within 15 minutes are locked and cannot be deleted.

---

### `gateway_health`

Returns the connection status and circuit-breaker state for every configured calendar source. Use this to diagnose connectivity issues before other tools fail.

No parameters.

---

## Claude Desktop / Claude Code

Add to `claude_desktop_config.json`
(macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "caldav": {
      "command": "/absolute/path/to/skill/.venv/bin/python",
      "args": ["/absolute/path/to/kairos-mcp/skill/mcp_server.py"],
      "env": {
        "CALDAV_GATEWAY_URL": "http://localhost:8080"
      }
    }
  }
}
```

Use the venv's Python, not the system Python — the system Python won't have `mcp` or `httpx` installed. Create the venv once:

```bash
python3 -m venv skill/.venv
skill/.venv/bin/pip install -r skill/requirements.txt
```

Restart Claude Desktop after editing the config. The seven tools appear automatically in every conversation. If your gateway is on another host (e.g. `http://192.168.1.50:8080`), update `CALDAV_GATEWAY_URL`.

**Claude Code** uses the same config file and format — no differences.

### Combining with Google's official MCP

Run both alongside each other in the same config — they'll appear as separate tool namespaces:

```json
{
  "mcpServers": {
    "caldav": {
      "command": "/absolute/path/to/skill/.venv/bin/python",
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

Use `caldav_*` tools for iCloud / Nextcloud, `google_*` tools for Google Calendar.

---

## OpenClaw

OpenClaw proxies stdio MCP servers to local models. Add to your OpenClaw config under `mcp_servers`:

```json
{
  "mcp_servers": {
    "caldav": {
      "command": "/absolute/path/to/skill/.venv/bin/python",
      "args": ["/absolute/path/to/kairos-mcp/skill/mcp_server.py"],
      "env": {
        "CALDAV_GATEWAY_URL": "http://localhost:8080"
      }
    }
  }
}
```

OpenClaw bridges the stdio transport to whatever local model you're running — no other changes needed. The tools appear under the same names as in Claude Desktop.

---

## Hermes / LiteLLM / Ollama

Use `skill/tools.json` for any OpenAI function-calling compatible framework. Tool names are prefixed with `caldav_` to avoid namespace collisions when loading multiple tool sets.

### System prompt

Always include this to guide tool selection:

```
You have access to a CalDAV calendar gateway. Always call caldav_list_calendars
first if you don't already know the calendar ID. Use caldav_daily_brief for any
question about today's schedule. Always confirm with the user before calling
caldav_delete_event — deletions are permanent. When creating recurring events,
include a recurrence_rule in RFC 5545 RRULE format (e.g. FREQ=WEEKLY;BYDAY=MO,WE,FR).
```

### LiteLLM (Hermes, any Ollama model)

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
    body   = {k: v for k, v in args.items() if k not in skip} if method in ("POST", "PUT") else None
    r = httpx.request(method, f"{GATEWAY}{path}", params=params or None, json=body, timeout=30)
    r.raise_for_status()
    return r.text

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

# Usage
print(chat("What's on my icloud calendar this week?"))
print(chat("Schedule a 1:1 with Alice tomorrow at 2pm for 30 minutes"))
print(chat("Do I have anything on the family calendar this weekend?"))
```

### Ollama directly (tool-capable models)

Pass `tools.json` in the `/api/chat` request body:

```python
import json, httpx

tools = json.load(open("skill/tools.json"))

response = httpx.post("http://localhost:11434/api/chat", json={
    "model": "hermes3",
    "messages": [{"role": "user", "content": "What's on my calendar today?"}],
    "tools": tools,
    "stream": False,
})

# Handle tool_calls in response.json()["message"]["tool_calls"]
```

Tool-capable models for Ollama: `hermes3`, `qwen2.5`, `mistral`, `llama3.1` (8B+), `command-r`.

---

## Files

```
skill/mcp_server.py    MCP server — Claude Desktop, Claude Code, OpenClaw
skill/tools.json       OpenAI tool definitions — Hermes, LiteLLM, Ollama
skill/requirements.txt mcp>=2.0.0, httpx>=0.27.0
```
