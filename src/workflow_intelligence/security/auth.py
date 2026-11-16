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


DISALLOWED_TOKEN_PATTERNS = (
    "replace_",
    "placeholder",
    "changeme",
    "your_token",
    "secret_token",
    "dev_mcp",
    "default",
    "example",
    "test_token",
)

DISALLOWED_PASSWORD_PATTERNS = (
    "replace_",
    "placeholder",
    "changeme",
    "postgres",
    "password",
    "admin",
    "root",
    "123456",
    "example",
)


def validate_production_auth_token(token: str | None) -> None:
    """Validate that an MCP_AUTH_TOKEN is safe for production network deployment.

    Raises:
        RuntimeError: If token is missing, too short, or contains template placeholders.
    """
    cleaned = (token or "").strip()
    if not cleaned:
        raise RuntimeError(
            "Production network startup aborted: MCP_AUTH_TOKEN is required for network exposure. "
            "A gateway flag alone cannot be treated as proof of authentication; network endpoints fail closed."
        )
    if len(cleaned) < 16:
        raise RuntimeError(
            "Production network startup aborted: MCP_AUTH_TOKEN is too short (minimum 16 characters required, >=32 recommended)."
        )
    lowered = cleaned.lower()
    if any(pattern in lowered for pattern in DISALLOWED_TOKEN_PATTERNS):
        raise RuntimeError(
            "Production network startup aborted: MCP_AUTH_TOKEN contains an unchanged template placeholder or insecure value. "
            "Configure a strong, unique production token."
        )


def validate_production_database_credentials(
    database_url: str | None, postgres_password: str | None = None
) -> None:
    """Validate that database credentials in production are not default, trivial, or template placeholders.

    Raises:
        RuntimeError: If credentials match known defaults or template placeholders.
    """
    if postgres_password is not None:
        pw = postgres_password.strip()
        if not pw or len(pw) < 8:
            raise RuntimeError(
                "Production startup failed: POSTGRES_PASSWORD must be configured and at least 8 characters."
            )
        lowered_pw = pw.lower()
        if any(pattern in lowered_pw for pattern in DISALLOWED_PASSWORD_PATTERNS):
            raise RuntimeError(
                "Production startup failed: POSTGRES_PASSWORD contains default, trivial, or placeholder credentials. "
                "Configure a strong, unique production password."
            )

    if database_url:
        lowered_url = database_url.lower()
        insecure_patterns = (
            ":postgres@",
            ":password@",
            ":admin@",
            ":root@",
            ":replace_",
            ":changeme",
            ":placeholder",
            ":123456@",
            ":example",
        )
        if any(pattern in lowered_url for pattern in insecure_patterns):
            raise RuntimeError(
                "Production startup failed: default or trivial database credentials ('postgres:postgres') "
                "or placeholders are forbidden in production. Configure a secure POSTGRES_PASSWORD and DATABASE_URL."
            )
        import urllib.parse

        try:
            parsed = urllib.parse.urlsplit(database_url)
            if parsed.password:
                if len(parsed.password) < 8 or any(
                    p in parsed.password.lower() for p in DISALLOWED_PASSWORD_PATTERNS
                ):
                    raise RuntimeError(
                        "Production startup failed: default or trivial database credentials ('postgres:postgres') "
                        "or placeholders are forbidden in production. Configure a secure POSTGRES_PASSWORD and DATABASE_URL."
                    )
        except RuntimeError:
            raise
        except Exception:
            pass
