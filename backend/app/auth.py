from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant_id: str
    roles: frozenset[str] = field(default_factory=frozenset)
    clusters: frozenset[str] = field(default_factory=lambda: frozenset({"*"}))
    claims: dict[str, Any] = field(default_factory=dict, compare=False)

    def has_role(self, role: str, settings: Settings) -> bool:
        hierarchy = {
            settings.viewer_role: 1,
            settings.investigator_role: 2,
            settings.admin_role: 3,
        }
        semantic = {"viewer": 1, "investigator": 2, "admin": 3}
        required = semantic.get(role, hierarchy.get(role, 99))
        highest = max((hierarchy.get(item, 0) for item in self.roles), default=0)
        return highest >= required

    def can_access_cluster(self, cluster: str) -> bool:
        return "*" in self.clusters or cluster in self.clusters

    def can_access_tenant(self, tenant_id: str) -> bool:
        return self.tenant_id == "*" or self.tenant_id == tenant_id


class AuthManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._jwk_client = (
            jwt.PyJWKClient(settings.oidc_jwks_url)
            if settings.auth_enabled and settings.oidc_jwks_url
            else None
        )

    @staticmethod
    def _claim(payload: dict[str, Any], path: str) -> Any:
        value: Any = payload
        for part in path.split("."):
            if not isinstance(value, dict):
                return None
            value = value.get(part)
        return value

    @staticmethod
    def _as_set(value: Any) -> frozenset[str]:
        if value is None:
            return frozenset()
        if isinstance(value, str):
            return frozenset(item for item in value.replace(",", " ").split() if item)
        if isinstance(value, (list, tuple, set)):
            return frozenset(str(item) for item in value if str(item))
        return frozenset({str(value)})

    async def authenticate(self, token: str | None) -> Principal:
        if not self.settings.auth_enabled:
            return Principal(
                subject="anonymous-demo",
                tenant_id=self.settings.default_tenant_id,
                roles=frozenset({
                    self.settings.viewer_role,
                    self.settings.investigator_role,
                    self.settings.admin_role,
                }),
                clusters=frozenset({"*"}),
                claims={"auth_disabled": True},
            )

        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not (
            self.settings.oidc_issuer
            and self.settings.oidc_audience
            and self.settings.oidc_jwks_url
            and self._jwk_client
        ):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OIDC authentication is enabled but not fully configured",
            )

        try:
            signing_key = await asyncio.to_thread(
                self._jwk_client.get_signing_key_from_jwt,
                token,
            )
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=self.settings.oidc_algorithm_list,
                audience=self.settings.oidc_audience,
                issuer=self.settings.oidc_issuer,
                options={"require": ["exp", "iat", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"invalid bearer token: {exc.__class__.__name__}",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

        roles = self._as_set(
            self._claim(payload, self.settings.oidc_roles_claim)
        )
        tenant = self._claim(payload, self.settings.oidc_tenant_claim)
        clusters = self._as_set(
            self._claim(payload, self.settings.oidc_clusters_claim)
        )

        return Principal(
            subject=str(payload.get("sub")),
            tenant_id=str(tenant or self.settings.default_tenant_id),
            roles=roles,
            clusters=clusters or frozenset(),
            claims=dict(payload),
        )


_bearer = HTTPBearer(auto_error=False)


async def get_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    token = credentials.credentials if credentials else None
    return await request.app.state.auth.authenticate(token)


def require_role(role: str) -> Callable[..., Any]:
    async def dependency(
        request: Request,
        principal: Principal = Depends(get_principal),
    ) -> Principal:
        if not principal.has_role(role, request.app.state.settings):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role {role} is required",
            )
        return principal

    return dependency


def authorize_cluster(principal: Principal, cluster: str) -> None:
    if not principal.can_access_cluster(cluster):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"cluster access denied: {cluster}",
        )


def authorize_tenant(principal: Principal, tenant_id: str) -> None:
    if not principal.can_access_tenant(tenant_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant access denied",
        )
