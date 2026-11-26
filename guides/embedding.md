# Embedding Kairos in a multi-user host

A self-hosted gateway has one set of calendars that every caller shares. A
host that serves many people needs each caller to see only their own
calendars. Kairos supports that with two hooks on `create_app`. You don't need
either for a self-hosted setup.

## Per-caller services

```python
from kairos.app import create_app


async def resolve(token):
    # token is the verified mcp.server.auth.provider.AccessToken, or None
    return await calendar_service_for(token.subject)


app = create_app(settings, resolver=resolve)
```

Kairos calls the resolver for every REST request and every MCP tool call, with
the token that request presented. It returns a `kairos.service.CalendarService`
for that caller. Build it however you like, usually from connections you
store per user:

```python
from kairos.service import CalendarService
from kairos.settings import Settings, SourceConfig

service = CalendarService(Settings(sources=(SourceConfig(...), ...)))
```

Rules that keep callers apart:

- Take the user's identity only from the verified token (its `subject`),
  never from the request body or a query parameter.
- Raise a `kairos.errors.KairosError` subclass when you can't serve the
  caller. Its `status_code` and `code` become the HTTP response, for example
  401 when the token is missing and 404 when the user has no calendars.
- Building a service can be slow, so cache it per user for a few minutes, and
  `await service.close()` when you drop one.

Pass `service=` or `resolver=`, not both. With a resolver, `/health` reports
`{"status": "healthy", "calendars": {}}` because no shared calendars exist.

## Your own token verifier

By default Kairos checks API tokens and, when configured, JWTs from your
identity provider. A host with its own rules can pass a verifier:

```python
from kairos.auth import TokenVerifier


class MyVerifier(TokenVerifier):
    async def verify_token(self, token): ...  # return an AccessToken, or None to refuse


app = create_app(settings, resolver=resolve, verifier=MyVerifier(settings))
```

A `TokenVerifier` can also wrap your own `kairos.auth.OIDCVerifier` subclass
(`TokenVerifier(settings, oidc=...)`), for an identity provider whose access
tokens need checks that differ from the defaults, such as tokens with no
`aud` claim. Kairos uses the same verifier for REST and for `/mcp`.
