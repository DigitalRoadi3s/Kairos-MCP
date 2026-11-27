# Writing a provider plugin

Kairos talks to calendars through providers. The built-in `caldav` provider
covers iCloud, Nextcloud, Fastmail, Google's CalDAV endpoint and any other
CalDAV server, so you only need a plugin for a service that needs its own
code: a different API, a different sign-in, or a read-only feed.

A plugin is an ordinary Python package. Kairos finds it through an entry point,
so you install it next to Kairos and name it in a source's config. You don't
change Kairos itself.

## How a source picks its provider

Each entry in `CALDAV_SOURCES` (or the YAML sources file) can set
`"provider"`. Leave it out and Kairos uses `caldav`.

```json
[{"id": "work", "name": "Work", "provider": "zoho", "url": "https://calendar.zoho.com/", "username": "me@example.com", "password": "..."}]
```

At startup Kairos loads every installed provider. A name it doesn't know
stops startup with a `ConfigError` that lists the installed providers. A
plugin that fails to import is logged as `provider_load_failed` and skipped,
so one broken package can't take the gateway down.

## The provider object

A plugin exports a `kairos.providers.Provider`:

```python
from kairos.providers import Provider

provider = Provider(
    name="zoho",  # must match the entry point name
    display_name="Zoho Calendar",
    build_source=build_source,  # SourceConfig -> Source
    auth_types=("basic",),  # auth_type values this provider accepts
    default_url="https://calendar.zoho.com/",
    two_way=True,  # False makes its calendars read-only
)
```

| Field | Meaning |
|---|---|
| `name` | The value sources put in `"provider"`. Must match the entry point name. |
| `display_name` | A human name for logs and UIs. |
| `build_source` | A function that takes a `SourceConfig` and returns a source object. |
| `auth_types` | The `auth_type` values the provider accepts (`basic`, `oauth2`). Kairos rejects a source that asks for any other. |
| `default_url` | Optional. A suggested server URL for UIs. |
| `two_way` | `False` for a service that can't take writes. Kairos then treats every calendar from it as read-only, whatever the source's `writable` says. |
| `api_version` | Leave at the default. Kairos uses it to refuse plugins written for an incompatible interface. |

The entry point can name the `Provider` itself or a function that returns one.

## The source object

`build_source` returns an object with the methods of `kairos.service.Source`.
Kairos calls them from a worker thread, so they can block.

| Member | What Kairos expects |
|---|---|
| `id`, `config` | The source's id and its `SourceConfig`. |
| `connected` | `True` once `connect()` has succeeded. |
| `connect()` | Sign in and find the calendar. Kairos calls it at startup and again on first use if it failed. |
| `close()` | Release connections. |
| `list_events(start, end)` | Events in the range as `kairos.models.Event`, one per occurrence of a recurring event. |
| `get_event(uid)` | One event, or raise `EventNotFoundError`. |
| `create_event(ical_text)` | Store a new event given as iCalendar text, and return it. |
| `update_event(uid, patch)` | Apply an `EventPatch` (only the fields set) and return the event. Raise `ConflictError` if the event changed on the server since it was read. |
| `delete_event(uid)` | Delete the event, or the whole series for a recurring one. |

Raise the errors in `kairos.errors` so callers get the right status code:
`EventNotFoundError` (404), `ConflictError` (409), `UpstreamAuthError` (502)
when the service rejects your credentials, and `SourceUnavailableError` (503)
when it's down or rate limiting. Kairos wraps every call in a per-source
circuit breaker and retries reads only, so a plugin shouldn't retry writes
itself.

## Packaging

```toml
[project]
name = "kairos-provider-zoho"
dependencies = ["kairos-mcp>=2.0,<3"]

[project.entry-points."kairos.providers"]
zoho = "kairos_provider_zoho:provider"
```

Install it into the same environment as Kairos (`pip install kairos-provider-zoho`)
and restart the gateway.

## Testing a plugin

Check that Kairos finds and builds it:

```python
from kairos.providers import load_providers
from kairos.settings import SourceConfig

registry = load_providers()
assert "zoho" in registry.names()
source = registry.source_factory(SourceConfig(id="t", name="T", url="https://calendar.zoho.com/", provider="zoho"))
```

Then run the service against it with a fake backend, as Kairos's own
`tests/test_providers.py` does, and check that reads, writes and errors come
back the way the table above describes.
