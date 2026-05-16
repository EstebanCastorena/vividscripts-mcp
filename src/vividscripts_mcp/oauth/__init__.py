"""OAuth 2.1 surface for the VividScripts MCP server.

Phase 1 (KAN-29) implements:

- ``metadata`` — RFC 9728 Protected Resource Metadata (KAN-48)
- ``dcr`` — RFC 7591 Dynamic Client Registration (KAN-49)
- ``authorize`` — RFC 6749 § 4.1 + RFC 7636 PKCE authorize endpoint (KAN-50)
- ``token`` — RFC 6749 § 5 token endpoint (KAN-51)
- ``bearer`` — RFC 6750 Bearer token validator (KAN-52)

Supporting modules (``store``, ``session``, ``audit``) provide the
in-memory infrastructure the offline mode ships against.

The production broker (KAN-85, decided by KAN-36 Cognito-direct
pass-through) adds ``cognito`` + ``callback``: ``/oauth/authorize``
delegates to Cognito Hosted UI, ``/oauth/callback`` exchanges Cognito's
code, and ``/oauth/token`` passes Cognito's tokens through unchanged
(the package stays the RFC 7591 DCR facade Cognito user pools can't
provide). ``server.build_app(cognito=...)`` selects broker vs offline.
"""
