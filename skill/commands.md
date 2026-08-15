# Chat Slash Commands

Saved prompt shortcuts for Claude Desktop / claude.ai's chat-level `/commands`
feature (Settings → slash commands). These are plain saved prompts, not code —
Claude has access to the `caldav` MCP tools already configured in
`claude_desktop_config.json`, so each prompt below triggers a real tool call
when the command runs.

This is different from Claude Code's `.claude/commands/` — those are for the
Code tab specifically and use markdown files with frontmatter. See
[`skill/README.md`](README.md) if you're setting up Claude Code instead.

## Setup

Claude Desktop → Settings → slash commands → add each entry below with its
name and prompt text.

## Commands

| Command | Prompt |
|---|---|
| `/agenda` | Use the caldav MCP `list_events` tool to show me everything on all my calendars for the next 7 days, grouped by day. |
| `/today` | Use the caldav MCP `daily_brief` tool on my icloud calendar for today, timezone America/New_York. Summarize free slots too. |
| `/calendars` | Use the caldav MCP `list_calendars` tool and list every calendar I have configured. |
| `/freeslot` | Use the caldav MCP `daily_brief` tool on my icloud calendar for today, timezone America/New_York, and tell me the largest free slot remaining today. |

## Adding your own

Any prompt that references "the caldav MCP" and one of the seven tools
(`list_calendars`, `list_events`, `daily_brief`, `create_event`, `update_event`,
`delete_event`, `gateway_health`) will work the same way. See the
[tool reference](README.md#tool-reference) for full parameter details —
useful when writing a command that needs specific arguments, like a
particular calendar ID or date range.

Example — a command scoped to just the family calendar:

> Use the caldav MCP `list_events` tool on my family calendar for the next
> 14 days.
