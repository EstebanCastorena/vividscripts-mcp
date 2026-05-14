"""OAuth 2.1 surface for the VividScripts MCP server.

Phase 1 (KAN-29) implements:

- ``metadata`` — RFC 9728 Protected Resource Metadata (KAN-48)
- ``dcr`` — RFC 7591 Dynamic Client Registration (KAN-49)
- ``authorize`` — RFC 6749 § 4.1 + RFC 7636 PKCE authorize endpoint (KAN-50)
- ``token`` — RFC 6749 § 5 token endpoint (KAN-51)
- ``bearer`` — RFC 6750 Bearer token validator (KAN-52)

Supporting modules (``store``, ``session``, ``audit``) provide the
in-memory infrastructure Phase 1 ships against. Phase 3 (KAN-31) swaps
the mocks for production-grade backings (AWS Secrets Manager, Cognito).
"""
