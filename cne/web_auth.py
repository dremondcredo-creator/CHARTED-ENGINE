"""Google OpenID Connect authentication for the dashboard HTTP server."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from http.cookies import CookieError, SimpleCookie
from urllib.parse import urlencode, urlsplit

import requests
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token

from cne.config import settings

SESSION_COOKIE = "__Host-cne_session"
FLOW_COOKIE = "__Host-cne_oauth_flow"
SESSION_TTL_SECONDS = 12 * 60 * 60
FLOW_TTL_SECONDS = 10 * 60
GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


class OAuthError(Exception):
    """An expected OAuth configuration, validation, or provider error."""


class _TimedSession(requests.Session):
    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", 10)
        return super().request(method, url, **kwargs)


def is_configured() -> bool:
    try:
        redirect = urlsplit(settings.oauth_redirect_uri)
    except ValueError:
        return False
    return all(
        (
            settings.google_oauth_client_id,
            settings.google_oauth_client_secret,
            len(settings.oauth_cookie_secret) >= 32,
            redirect.scheme == "https",
            redirect.netloc,
            redirect.path == "/auth/callback",
            not redirect.query,
            not redirect.fragment,
            not redirect.username,
            settings.oauth_allowed_emails,
        )
    )


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign(value: str) -> str:
    secret = settings.oauth_cookie_secret.encode("utf-8")
    if len(secret) < 32:
        raise OAuthError("CNE_OAUTH_COOKIE_SECRET must contain at least 32 characters.")
    signature = hmac.new(secret, value.encode("ascii"), hashlib.sha256).digest()
    return _encode(signature)


def _signed_cookie(payload: dict) -> str:
    encoded = _encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{encoded}.{_sign(encoded)}"


def _read_signed_cookie(value: str | None) -> dict | None:
    if not value or not is_configured():
        return None
    try:
        encoded, supplied_signature = value.split(".", 1)
        if not hmac.compare_digest(_sign(encoded), supplied_signature):
            return None
        payload = json.loads(_decode(encoded))
        if not isinstance(payload, dict) or payload.get("exp", 0) < int(time.time()):
            return None
        return payload
    except (
        ValueError,
        TypeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        binascii.Error,
    ):
        return None


def _cookie_value(handler, name: str) -> str | None:
    cookie = SimpleCookie()
    try:
        cookie.load(handler.headers.get("Cookie", ""))
    except CookieError:
        return None
    morsel = cookie.get(name)
    return morsel.value if morsel else None


def _set_cookie(handler, name: str, value: str, max_age: int) -> None:
    handler.send_header(
        "Set-Cookie",
        f"{name}={value}; Path=/; Max-Age={max_age}; "
        "Secure; HttpOnly; SameSite=Lax",
    )


def _clear_cookie(handler, name: str) -> None:
    handler.send_header(
        "Set-Cookie",
        f"{name}=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Lax",
    )


def _redirect(handler, location: str, *, set_cookie: tuple[str, str, int] | None = None,
              clear_cookie: str | None = None) -> None:
    handler.send_response(302)
    handler.send_header("Location", location)
    handler.send_header("Cache-Control", "no-store")
    if set_cookie:
        _set_cookie(handler, *set_cookie)
    if clear_cookie:
        _clear_cookie(handler, clear_cookie)
    handler.end_headers()


def _login_page(handler, message: str) -> None:
    body = (
        "<!doctype html><html lang='en'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Charted News Engine sign in</title>"
        "<body style='font:16px system-ui;max-width:36rem;margin:12vh auto;padding:1rem'>"
        "<h1>Charted News Engine</h1><p>"
        + message
        + "</p><p><a href='/auth/login'>Sign in with Google</a></p></body></html>"
    ).encode("utf-8")
    handler.send_response(503 if not is_configured() else 200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _flow_cookie(handler) -> dict | None:
    return _read_signed_cookie(_cookie_value(handler, FLOW_COOKIE))


def is_authenticated(handler) -> bool:
    payload = _read_signed_cookie(_cookie_value(handler, SESSION_COOKIE))
    email = payload.get("email", "").casefold() if payload else ""
    return email in settings.oauth_allowed_emails


def begin_login(handler) -> None:
    if not is_configured():
        _login_page(
            handler,
            "Google sign-in is not configured. Contact the dashboard administrator.",
        )
        return
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    authorization = urlencode(
        {
            "client_id": settings.google_oauth_client_id,
            "redirect_uri": settings.oauth_redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "prompt": "select_account",
        }
    )
    flow = _signed_cookie(
        {"state": state, "nonce": nonce, "exp": int(time.time()) + FLOW_TTL_SECONDS}
    )
    _redirect(
        handler,
        f"{GOOGLE_AUTHORIZE_URL}?{authorization}",
        set_cookie=(FLOW_COOKIE, flow, FLOW_TTL_SECONDS),
    )


def finish_login(handler, params: dict[str, list[str]]) -> None:
    if not is_configured():
        _login_page(
            handler,
            "Google sign-in is not configured. Contact the dashboard administrator.",
        )
        return
    flow = _flow_cookie(handler)
    state_values = params.get("state", [])
    code_values = params.get("code", [])
    if (
        not flow
        or not state_values
        or not code_values
        or not hmac.compare_digest(str(flow.get("state", "")), state_values[0])
    ):
        _login_page(handler, "Sign-in could not be verified. Please try again.")
        return

    try:
        response = requests.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code_values[0],
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "redirect_uri": settings.oauth_redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=10,
        )
        response.raise_for_status()
        token_response = response.json()
        raw_token = token_response.get("id_token") if isinstance(token_response, dict) else None
        if not isinstance(raw_token, str):
            raise OAuthError("Google did not return an ID token.")
        claims = id_token.verify_oauth2_token(
            raw_token,
            GoogleRequest(session=_TimedSession()),
            settings.google_oauth_client_id,
        )
    except (requests.RequestException, GoogleAuthError, ValueError, TypeError) as exc:
        raise OAuthError("Google sign-in verification failed.") from exc

    email = claims.get("email")
    if (
        not isinstance(email, str)
        or claims.get("email_verified") is not True
        or not hmac.compare_digest(str(claims.get("nonce", "")), str(flow.get("nonce", "")))
        or email.casefold() not in settings.oauth_allowed_emails
    ):
        _redirect(handler, "/auth/login?error=account_not_allowed", clear_cookie=FLOW_COOKIE)
        return

    session = _signed_cookie(
        {"email": email.casefold(), "exp": int(time.time()) + SESSION_TTL_SECONDS}
    )
    _redirect(
        handler,
        "/",
        set_cookie=(SESSION_COOKIE, session, SESSION_TTL_SECONDS),
        clear_cookie=FLOW_COOKIE,
    )


def logout(handler) -> None:
    _redirect(
        handler,
        "/auth/login",
        clear_cookie=SESSION_COOKIE,
    )


def reject_unconfigured(handler) -> None:
    _login_page(
        handler,
        "Google sign-in is not configured. Contact the dashboard administrator.",
    )


def reject_unauthorized(handler, *, api: bool) -> None:
    if api:
        handler._json(401, {"error": "Authentication required."})
    else:
        _redirect(handler, "/auth/login")


def reject_oauth_error(handler) -> None:
    _login_page(handler, "Google sign-in failed. Please try again or contact the administrator.")
