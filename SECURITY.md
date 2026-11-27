# Security policy

Kairos holds calendar credentials and can change events, so we take reports
seriously.

## Supported versions

| Version | Supported |
|---|---|
| 2.x | Yes |
| 1.x | No; upgrade to 2.x |

## Reporting a vulnerability

Report it privately through GitHub: open the repository's **Security** tab
and choose **Report a vulnerability**. Please don't open a public issue,
pull request or discussion for it.

Include what's affected, how to reproduce it, and the impact you see. Leave
out real passwords, tokens and calendar data. We aim to reply within a few
days and will tell you when a fix ships.

## Running Kairos safely

- Keep auth on. Use API tokens of 32 or more random characters, or OAuth,
  and never set `KAIROS_INSECURE_NO_AUTH=true` on a network you don't trust.
- Use app-specific passwords for iCloud and Nextcloud, never your account
  password, and revoke one you no longer need.
- Put the gateway behind HTTPS, and set `KAIROS_ALLOWED_HOSTS` when `/mcp`
  is reachable from outside.
- With OAuth, list the users who may connect in
  `KAIROS_OIDC_ALLOWED_SUBJECTS`.
- Mark calendars that agents should only read as `"writable": false`.
