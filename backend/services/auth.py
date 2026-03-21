from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt

from backend.config import settings
from backend.models.auth import OAuthAuthorizeRequest, OAuthTokenRequest, TokenBundle, UserContext


class AuthService:
    """Issue minimal OAuth metadata and JWT-based tokens for ChatGPT app consent."""

    def authorization_metadata(self) -> dict[str, object]:
        issuer = settings.app_base_url
        return {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/api/oauth/authorize",
            "token_endpoint": f"{issuer}/api/oauth/token",
            "registration_endpoint": f"{issuer}/api/oauth/register",
            "revocation_endpoint": f"{issuer}/api/oauth/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": [
                "profile:read",
                "profile:write",
                "plans:read",
                "plans:write",
                "metrics:write",
            ],
        }

    def protected_resource_metadata(self) -> dict[str, object]:
        return {
            "resource": f"{settings.app_base_url}/api/mcp",
            "authorization_servers": [settings.app_base_url],
        }

    def build_authorize_response(
        self, request: OAuthAuthorizeRequest, user_id: str
    ) -> dict[str, str]:
        return {
            "code": self._issue_code(
                user_id=user_id, client_id=request.client_id, scope=request.scope
            ),
            "redirect_uri": request.redirect_uri,
            "state": request.state or "",
        }

    def exchange_code(self, request: OAuthTokenRequest) -> TokenBundle:
        claims = jwt.decode(
            request.code, settings.app_jwt_secret, algorithms=["HS256"], audience=request.client_id
        )
        user_id = str(claims["sub"])
        scope = str(claims["scope"]).split()
        return TokenBundle(
            access_token=self._issue_access_token(
                user_id=user_id, client_id=request.client_id, scopes=scope
            ),
            refresh_token=str(uuid4()),
        )

    def get_user_context_from_bearer(self, token: str) -> UserContext:
        claims = jwt.decode(
            token,
            settings.app_jwt_secret,
            algorithms=["HS256"],
            audience=f"{settings.app_base_url}/api/mcp",
        )
        return UserContext(
            user_id=str(claims["sub"]),
            scopes=list(claims.get("scope", "").split()),
            client_id=str(claims.get("azp", "")) or None,
            grant_id=str(claims.get("jti", "")) or None,
        )

    def _issue_code(self, *, user_id: str, client_id: str, scope: str) -> str:
        return jwt.encode(
            {
                "sub": user_id,
                "aud": client_id,
                "scope": scope,
                "exp": datetime.now(UTC) + timedelta(minutes=10),
                "iat": datetime.now(UTC),
                "jti": str(uuid4()),
            },
            settings.app_jwt_secret,
            algorithm="HS256",
        )

    def _issue_access_token(self, *, user_id: str, client_id: str, scopes: list[str]) -> str:
        return jwt.encode(
            {
                "sub": user_id,
                "aud": f"{settings.app_base_url}/api/mcp",
                "azp": client_id,
                "scope": " ".join(scopes),
                "exp": datetime.now(UTC) + timedelta(minutes=15),
                "iat": datetime.now(UTC),
                "jti": str(uuid4()),
            },
            settings.app_jwt_secret,
            algorithm="HS256",
        )
