# Security Policy

## Reporting a Vulnerability

If you believe you've found a security vulnerability in `vividscripts-mcp`,
**please do not open a public GitHub issue.**

Report privately via one of:

- [GitHub Security Advisories](https://github.com/EstebanCastorena/vividscripts-mcp/security/advisories/new)
  (preferred — keeps everything in one place)
- Email: `estebancastorenajr@gmail.com` with subject `[security] vividscripts-mcp:`

We will acknowledge receipt within **3 business days** and provide a more
detailed response within **7 days** with next steps.

## Scope

### In scope

- The `vividscripts-mcp` Python package in this repository
- OAuth 2.1 + DCR + PKCE implementation
- Bearer token validation and Cognito JWKS handling
- Magic-link signing, redemption, and replay protection
- MCP protocol handling (Tools, Resources, Prompts)
- JSON schema validation logic
- Supply chain (dependency manifests, CI workflow permissions, packaging)

### Out of scope

- The VividScripts platform itself — report via VividScripts support channels
- Issues in third-party dependencies — please report to the upstream maintainers;
  we'll triage Dependabot alerts in parallel
- Social engineering, physical attacks, or attacks requiring privileged access
  to the user's own machine
- Denial-of-service via standard L3/L4 floods against `app.vividscripts.com`
  (handled at the infrastructure layer)

## Disclosure Policy

We follow responsible-disclosure practice:

1. Reporter submits privately (see above).
2. We acknowledge within 3 business days.
3. We confirm reproducibility within 7 days.
4. We develop and test a fix; coordinate with the reporter on disclosure timing.
5. Public disclosure no earlier than **90 days from confirmation**, or sooner
   if a fix is shipped and we've notified affected parties.

Reporters acting in good faith will not be subject to legal action for
research within the in-scope areas above.

## Supported Versions

Pre-1.0 (`0.x.y`): only the latest minor receives security patches.

Once `1.0.0` ships, the most recent two minor releases will receive security
fixes for at least 6 months.

## Hardening Checklist

The repo enforces the following baseline:

- Pre-commit `gitleaks` scan for accidentally committed secrets
- `mypy --strict` type checking on every PR
- `ruff` lint + format on every PR
- GitHub Actions workflows declare least-privilege permissions
  (`permissions: contents: read` by default)
- Dependabot weekly updates for `pip` and `github-actions` ecosystems
- GitHub native secret scanning enabled

## Security Design

For a complete writeup of the security design — auth flow with PKCE, magic-link
replay protection, schema validation, cryptography choices, supply-chain
hardening — see [`docs/security.md`](docs/security.md). That document is the
public-facing security architecture; this file is the reporting policy.

The internal threat model and per-phase operational security checklist are
kept private.
