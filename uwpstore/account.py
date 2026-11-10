"""Microsoft account sign-in: the OAuth device-code flow, as Wine-UWP's winexboxauth does it.

The store never sees the password. It asks login.live.com for a short code, opens
https://www.microsoft.com/link?otc=CODE in the user's own browser, and polls until the user approves.
What it keeps is the refresh token, in the desktop keyring (libsecret) when there is one, otherwise in
the settings file (mode 0600). Access tokens for each service scope are minted from it on demand and
kept in memory only.

Xbox Live (user.auth -> XSTS) supplies the gamertag shown in the header.
"""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.parse
from dataclasses import dataclass
from typing import Callable

from . import net, settings

CLIENT_ID = "000000004C12AE6F"                       # the Xbox app's MSA client, as Wine-UWP uses
SIGNIN_SCOPE = "service::user.auth.xboxlive.com::MBI_SSL"
FE3_SCOPE = "service::dcat.update.microsoft.com::MBI_SSL"   # Windows Update / Store delivery tickets
CONNECT_URL = "https://login.live.com/oauth20_connect.srf"
TOKEN_URL = "https://login.live.com/oauth20_token.srf"
LINK_URL = "https://www.microsoft.com/link?otc={code}"

_SECRET_LABEL = "UWP Store: Microsoft account"
_lock = threading.RLock()
_access: dict[str, tuple[str, float]] = {}            # scope -> (token, expiry)
_profile: "Profile | None" = None


class AccountError(Exception):
    pass


@dataclass
class DeviceCode:
    user_code: str
    device_code: str
    link: str
    interval: int
    expires_at: float


@dataclass
class Profile:
    gamertag: str
    xuid: str
    user_hash: str


# ---- refresh-token storage --------------------------------------------------------------------------

def _secret():
    try:
        import gi
        gi.require_version("Secret", "1")
        from gi.repository import Secret
        schema = Secret.Schema.new("io.github.uwpstore.Store.Account", Secret.SchemaFlags.NONE,
                                   {"kind": Secret.SchemaAttributeType.STRING})
        return Secret, schema
    except (ImportError, ValueError):
        return None, None


def _load_refresh() -> str:
    Secret, schema = _secret()
    if Secret:
        try:
            value = Secret.password_lookup_sync(schema, {"kind": "msa-refresh"}, None)
            if value:
                return value
        except Exception:  # no keyring daemon: fall back to the settings file
            pass
    return settings.load().get("msa_refresh_token") or ""


def _store_refresh(token: str) -> None:
    Secret, schema = _secret()
    if Secret:
        try:
            if token:
                Secret.password_store_sync(schema, {"kind": "msa-refresh"}, Secret.COLLECTION_DEFAULT,
                                           _SECRET_LABEL, token, None)
            else:
                Secret.password_clear_sync(schema, {"kind": "msa-refresh"}, None)
            settings.save(msa_refresh_token="")      # never keep a second copy on disk
            return
        except Exception:
            pass
    settings.save(msa_refresh_token=token)


# ---- OAuth -------------------------------------------------------------------------------------------

def _form(url: str, fields: dict[str, str]) -> tuple[int, dict]:
    status, body = net.post(url, urllib.parse.urlencode(fields).encode(),
                            {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    try:
        return status, json.loads(body or b"{}")
    except ValueError:
        return status, {}


def start_device_code() -> DeviceCode:
    status, data = _form(CONNECT_URL, {"client_id": CLIENT_ID, "scope": SIGNIN_SCOPE, "response_type": "device_code"})
    if status != 200 or not data.get("user_code"):
        raise AccountError(data.get("error_description") or f"login.live.com refused the sign-in request (HTTP {status})")
    return DeviceCode(data["user_code"], data["device_code"], LINK_URL.format(code=data["user_code"]),
                      max(1, int(data.get("interval") or 5)), time.time() + float(data.get("expires_in") or 900))


def poll_device_code(code: DeviceCode, cancelled: Callable[[], bool] = lambda: False) -> Profile:
    """Wait until the user approves the code in their browser, then sign in to Xbox Live."""
    interval = code.interval
    while time.time() < code.expires_at:
        for _ in range(interval * 10):
            if cancelled():
                raise AccountError("Sign-in cancelled")
            time.sleep(0.1)
        try:
            status, data = _form(TOKEN_URL, {"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                                             "client_id": CLIENT_ID, "device_code": code.device_code})
        except net.NetError:
            continue                                   # transient; keep polling
        if status == 200 and data.get("access_token"):
            with _lock:
                _access.clear()
                _access[SIGNIN_SCOPE] = (data["access_token"], time.time() + float(data.get("expires_in") or 3600))
                if data.get("refresh_token"):
                    _store_refresh(data["refresh_token"])
            return profile(refresh=True)
        error = data.get("error", "")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        if error == "expired_token":
            break
        raise AccountError(data.get("error_description") or error or f"HTTP {status}")
    raise AccountError("The code expired before the sign-in was finished. Try again.")


def signed_in() -> bool:
    return bool(_load_refresh())


def access_token(scope: str = SIGNIN_SCOPE) -> str:
    """An access token (an RPS ticket) for a service scope, minted from the refresh token."""
    with _lock:
        cached = _access.get(scope)
        if cached and cached[1] > time.time() + 120:
            return cached[0]
        refresh = _load_refresh()
        if not refresh:
            raise AccountError("Not signed in")
        status, data = _form(TOKEN_URL, {"grant_type": "refresh_token", "client_id": CLIENT_ID,
                                         "scope": scope, "refresh_token": refresh})
        if status != 200 or not data.get("access_token"):
            error = data.get("error", "")
            if error in ("invalid_grant", "interaction_required"):
                raise AccountError("Your Microsoft account sign-in has expired. Sign in again.")
            raise AccountError(data.get("error_description") or error or f"HTTP {status}")
        if data.get("refresh_token") and data["refresh_token"] != refresh:
            _store_refresh(data["refresh_token"])
        _access[scope] = (data["access_token"], time.time() + float(data.get("expires_in") or 3600))
        return data["access_token"]


def store_ticket() -> str | None:
    """The MSA ticket for the Store's package delivery (FE3), or None when signed out or refused."""
    if not signed_in():
        return None
    try:
        # FE3 takes the RPS ticket as base64("t=" + token); the bare token fails its security check
        return base64.b64encode(("t=" + access_token(FE3_SCOPE)).encode()).decode()
    except (AccountError, net.NetError):
        return None


# ---- Xbox Live profile -------------------------------------------------------------------------------

def _xbl_post(url: str, body: dict) -> dict:
    status, data = net.post(url, json.dumps(body).encode(), {"Content-Type": "application/json",
                                                             "Accept": "application/json",
                                                             "x-xbl-contract-version": "1"})
    if status != 200:
        raise AccountError(f"Xbox Live refused the sign-in (HTTP {status})")
    return json.loads(data)


def profile(refresh: bool = False) -> Profile:
    """The signed-in account's gamertag, via user.auth.xboxlive.com and xsts.auth.xboxlive.com."""
    global _profile
    with _lock:
        if _profile and not refresh:
            return _profile
        cached = settings.load().get("msa_profile") or {}
        if cached and not refresh:
            _profile = Profile(**cached)
            return _profile
    rps = access_token(SIGNIN_SCOPE)
    user = _xbl_post("https://user.auth.xboxlive.com/user/authenticate", {
        "RelyingParty": "http://auth.xboxlive.com", "TokenType": "JWT",
        "Properties": {"AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com", "RpsTicket": f"t={rps}"}})
    xsts = _xbl_post("https://xsts.auth.xboxlive.com/xsts/authorize", {
        "RelyingParty": "http://xboxlive.com", "TokenType": "JWT",
        "Properties": {"SandboxId": "RETAIL", "UserTokens": [user["Token"]]}})
    claims = (xsts.get("DisplayClaims", {}).get("xui") or [{}])[0]
    result = Profile(claims.get("gtg", ""), claims.get("xid", ""), claims.get("uhs", ""))
    with _lock:
        _profile = result
    settings.save(msa_profile=result.__dict__)       # gamertag only; no tokens
    return result


def cached_profile() -> Profile | None:
    """The last known profile without touching the network (for drawing the header)."""
    if not signed_in():
        return None
    data = settings.load().get("msa_profile") or {}
    return Profile(**data) if data else _profile


def sign_out() -> None:
    global _profile
    with _lock:
        _access.clear()
        _profile = None
        _store_refresh("")
    settings.save(msa_profile={})


def _jwt_claims(token: str) -> dict:  # debugging helper
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return {}
