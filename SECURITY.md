# Security

Report a vulnerability in this integration through GitHub private vulnerability reporting:

https://github.com/steamEngineer/soundtrack-home-assistant/security/advisories/new

Include the Home Assistant version, the integration version, and what an attacker can do. Leave access tokens, refresh tokens, and passwords out of the report. If a log line contains one, redact it first.

Do not open a public issue for a vulnerability.

The current `main` branch is the supported line. It requires Home Assistant 2026.4 or newer.

A rejected Soundtrack refresh token is the integration signing the user out and asking for the password again. That password is not stored.
