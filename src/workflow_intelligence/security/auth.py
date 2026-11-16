"""Authentication and authorization utilities for Workflow Intelligence MCP."""

from __future__ import annotations

import hmac
from fastmcp.server.auth import AccessToken, TokenVerifier


class StaticTokenVerifier(TokenVerifier):
    """Bearer token verifier matching a configured secret token with constant-time comparison."""

    def __init__(self, valid_token: str):
        super().__init__()
        self._valid_token = valid_token.strip() if valid_token else ""

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify the provided bearer token against the configured secret.

        Uses constant-time comparison (hmac.compare_digest) to prevent timing attacks.
        Returns an AccessToken with 'mcp:all' scope on success, or None on failure.
        """
        if not token or not self._valid_token:
            return None
        if hmac.compare_digest(token.strip(), self._valid_token):
            return AccessToken(
                token=token,
                client_id="authorized-mcp-client",
                scopes=["mcp:all"],
            )
        return None
