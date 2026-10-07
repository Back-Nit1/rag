"""Authentication: Cognito JWT validation, with fixture identity for development.

The ownership rule the whole system depends on lives here: ``user_id`` comes
from the verified token subject and from nowhere else. A request body, a query
parameter or a queue payload never decides who owns a record.

Production refuses fixture identity. ``Settings`` already rejects
``METIS_ENV=prod`` with fixture identity enabled, and this module double-checks
at request time so a misconfiguration cannot silently authenticate everyone as
the demo user.
"""

from __future__ import annotations

import hmac
import logging
import time
from dataclasses import dataclass
from typing import Any

from metis_contracts import ErrorCode, MetisError, settings

log = logging.getLogger("metis.api.auth")

_JWKS_CACHE: dict[str, tuple[float, Any]] = {}
_JWKS_TTL_SECONDS = 3600


@dataclass(frozen=True)
class Principal:
    """The authenticated caller. ``user_id`` is the partition key for all data."""

    user_id: str
    source: str  # "cognito", "fixture" or "desktop"
    email: str | None = None

    @property
    def is_fixture(self) -> bool:
        return self.source == "fixture"


def _jwks_url() -> str:
    cfg = settings().aws
    return f"https://cognito-idp.{cfg.region}.amazonaws.com/{cfg.cognito_user_pool_id}/.well-known/jwks.json"


def _jwks() -> Any:
    """Fetch and cache the pool's JWKS. Keys rotate, so the cache has a TTL."""
    import jwt

    url = _jwks_url()
    cached = _JWKS_CACHE.get(url)
    now = time.time()
    if cached and now - cached[0] < _JWKS_TTL_SECONDS:
        return cached[1]
    client = jwt.PyJWKClient(url, cache_keys=True)
    _JWKS_CACHE[url] = (now, client)
    return client


def verify_cognito_token(token: str) -> Principal:
    """Validate signature, expiry, issuer and audience, then take the subject."""
    import jwt

    cfg = settings().aws
    if not cfg.cognito_user_pool_id or not cfg.cognito_client_id:
        raise MetisError(ErrorCode.UNAUTHENTICATED, "the identity provider is not configured")

    issuer = f"https://cognito-idp.{cfg.region}.amazonaws.com/{cfg.cognito_user_pool_id}"
    try:
        signing_key = _jwks().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=issuer,
            options={"require": ["exp", "iat", "sub", "token_use"]},
            audience=cfg.cognito_client_id,
        )
    except jwt.ExpiredSignatureError as exc:
        raise MetisError(ErrorCode.UNAUTHENTICATED, "the access token has expired") from exc
    except jwt.InvalidAudienceError:
        # Access tokens carry client_id rather than aud; retry without the
        # audience check and verify client_id explicitly.
        try:
            claims = jwt.decode(
                token,
                _jwks().get_signing_key_from_jwt(token).key,
                algorithms=["RS256"],
                issuer=issuer,
                options={"verify_aud": False, "require": ["exp", "iat", "sub", "token_use"]},
            )
        except Exception as exc:
            raise MetisError(ErrorCode.UNAUTHENTICATED, "the token could not be verified") from exc
        if claims.get("client_id") != cfg.cognito_client_id:
            raise MetisError(ErrorCode.UNAUTHENTICATED, "the token was issued to another client") from None
    except Exception as exc:
        raise MetisError(ErrorCode.UNAUTHENTICATED, "the token could not be verified") from exc

    if claims.get("token_use") not in ("access", "id"):
        raise MetisError(ErrorCode.UNAUTHENTICATED, "unexpected token_use")
    subject = claims.get("sub")
    if not subject:
        raise MetisError(ErrorCode.UNAUTHENTICATED, "the token has no subject")
    return Principal(user_id=str(subject), source="cognito", email=claims.get("email"))


def _desktop_principal(authorization: str | None) -> Principal:
    """Desktop-local mode: the bearer must be the launch secret, and is never
    sent to Cognito. The owner is the install subject, not the secret."""
    from .desktop import install_subject

    cfg = settings()
    if cfg.environment == "prod":
        raise MetisError(ErrorCode.UNAUTHENTICATED, "desktop authentication is not available here")
    scheme, _, value = (authorization or "").partition(" ")
    secret = cfg.desktop.auth_secret or ""
    # compare_digest on bytes: str comparison rejects non-ASCII input.
    if scheme.lower() != "bearer" or not hmac.compare_digest(value.strip().encode(), secret.encode()):
        raise MetisError(ErrorCode.UNAUTHENTICATED, "authentication is required")
    return Principal(user_id=install_subject(cfg.desktop.identity_path), source="desktop")


def principal_from_header(authorization: str | None) -> Principal:
    """Resolve the caller from an Authorization header.

    In a non-production configuration with fixture identity enabled, a missing
    header resolves to the demo subject so the local app needs no login. That
    path is refused outright for ``METIS_ENV=prod``.
    """
    cfg = settings()
    if cfg.desktop.enabled:
        return _desktop_principal(authorization)
    token = None
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() != "bearer" or not value.strip():
            raise MetisError(ErrorCode.UNAUTHENTICATED, "expected an 'Authorization: Bearer <token>' header")
        token = value.strip()

    if token:
        return verify_cognito_token(token)

    if cfg.environment == "prod" or not cfg.allow_fixture_identity:
        raise MetisError(ErrorCode.UNAUTHENTICATED, "authentication is required")
    return Principal(user_id=cfg.fixture_user_id, source="fixture")
