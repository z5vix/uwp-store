"""Microsoft accounts: device-code sign-in (as Wine-UWP's winexboxauth does it), several accounts at once.

The store never sees a password. It asks login.live.com for a short code, opens
https://www.microsoft.com/link?otc=CODE in the user's own browser, and polls until the user approves.
What it keeps for each account is the refresh token, in the desktop keyring (libsecret) when there is one,
otherwise in the settings file (mode 0600). Access tokens for each service scope are minted from it on
demand and kept in memory only.

Several accounts can be signed in at once (yours, a family member's): a product counts as owned when any of
them owns it, and an install uses the owning account's ticket. One account is "active": the header shows it,
and it is the default for everything that needs a single account.

Each account is known by its Xbox user id (xuid), from Xbox Live (user.auth -> XSTS), which also gives the
gamertag.
"""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.parse
from dataclasses import asdict, dataclass
from typing import Callable

from . import net, settings

CLIENT_ID = "000000004C12AE6F"                       # the Xbox app's MSA client, as Wine-UWP uses
SIGNIN_SCOPE = "service::user.auth.xboxlive.com::MBI_SSL"
FE3_SCOPE = "service::dcat.update.microsoft.com::MBI_SSL"   # Windows Update / Store delivery tickets
COLLECTIONS_SCOPE = "service::collections.mp.microsoft.com::MBI_SSL"
CONNECT_URL = "https://login.live.com/oauth20_connect.srf"
TOKEN_URL = "https://login.live.com/oauth20_token.srf"
COLLECTIONS_URL = "https://collections.mp.microsoft.com/v7.0/collections/query"
LINK_URL = "https://www.microsoft.com/link?otc={code}"

_SCHEMA = "io.github.uwpstore.Store.Accounts"       # one keyring item per account (attribute "account")
_LEGACY_SCHEMA = "io.github.uwpstore.Store.Account"  # the single-account item of earlier versions
_lock = threading.RLock()
_access: dict[tuple[str, str], tuple[str, float]] = {}   # (account id, scope) -> (token, expiry)
_owned: dict[tuple[str, str], tuple[bool, float]] = {}   # (account id, product id) -> (owned, expiry)


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
    user_hash: str = ""
    id: str = ""                                      # the account's key: its xuid (or MSA user id without Xbox)

    @property
    def name(self) -> str:
        return self.gamertag or self.xuid or "Microsoft account"


# ---- refresh-token storage --------------------------------------------------------------------------

def _secret():
    try:
        import gi
        gi.require_version("Secret", "1")
        from gi.repository import Secret
        return Secret
    except (ImportError, ValueError):
        return None


def _schema(Secret, name: str, attribute: str):
    return Secret.Schema.new(name, Secret.SchemaFlags.NONE, {attribute: Secret.SchemaAttributeType.STRING})


def _load_refresh(account_id: str) -> str:
    Secret = _secret()
    if Secret:
        try:
            value = Secret.password_lookup_sync(_schema(Secret, _SCHEMA, "account"), {"account": account_id}, None)
            if value:
                return value
        except Exception:  # no keyring daemon: fall back to the settings file
            pass
    return (settings.load().get("msa_refresh_tokens") or {}).get(account_id, "")


def _store_refresh(account_id: str, token: str) -> None:
    fallback = dict(settings.load().get("msa_refresh_tokens") or {})
    Secret = _secret()
    if Secret:
        try:
            schema = _schema(Secret, _SCHEMA, "account")
            if token:
                Secret.password_store_sync(schema, {"account": account_id}, Secret.COLLECTION_DEFAULT,
                                           f"UWP Store: Microsoft account {account_id}", token, None)
            else:
                Secret.password_clear_sync(schema, {"account": account_id}, None)
            if account_id in fallback:              # never keep a second copy on disk
                fallback.pop(account_id)
                settings.save(msa_refresh_tokens=fallback)
            return
        except Exception:
            pass
    if token:
        fallback[account_id] = token
    else:
        fallback.pop(account_id, None)
    settings.save(msa_refresh_tokens=fallback)


def _migrate_single_account() -> None:
    """Earlier versions kept one account (keyring item without an account id, or msa_refresh_token)."""
    cfg = settings.load()
    if cfg.get("msa_accounts"):
        return
    legacy, Secret = "", _secret()
    if Secret:
        try:
            legacy = Secret.password_lookup_sync(_schema(Secret, _LEGACY_SCHEMA, "kind"), {"kind": "msa-refresh"}, None) or ""
        except Exception:
            legacy = ""
    legacy = legacy or cfg.get("msa_refresh_token") or ""
    if not legacy:
        return
    old = cfg.get("msa_profile") or {}
    account_id = old.get("xuid") or "account-1"
    _store_refresh(account_id, legacy)
    profile = Profile(old.get("gamertag", ""), old.get("xuid", ""), old.get("user_hash", ""), account_id)
    settings.save(msa_accounts=[asdict(profile)], msa_active=account_id, msa_refresh_token="", msa_profile={})
    if Secret:
        try:
            Secret.password_clear_sync(_schema(Secret, _LEGACY_SCHEMA, "kind"), {"kind": "msa-refresh"}, None)
        except Exception:
            pass


# ---- the account list ---------------------------------------------------------------------------------

def accounts() -> list[Profile]:
    with _lock:
        _migrate_single_account()
        return [Profile(**{k: v for k, v in raw.items() if k in Profile.__dataclass_fields__})
                for raw in settings.load().get("msa_accounts") or []]


def get(account_id: str) -> Profile | None:
    return next((a for a in accounts() if a.id == account_id), None)


def active() -> Profile | None:
    found = accounts()
    wanted = settings.load().get("msa_active")
    return next((a for a in found if a.id == wanted), found[0] if found else None)


def set_active(account_id: str) -> None:
    if get(account_id):
        settings.save(msa_active=account_id)


def signed_in() -> bool:
    return bool(accounts())


def _save_account(profile: Profile) -> None:
    with _lock:
        found = [a for a in accounts() if a.id != profile.id]
        found.append(profile)
        changes: dict = {"msa_accounts": [asdict(a) for a in found]}
        if not settings.load().get("msa_active") or len(found) == 1:
            changes["msa_active"] = profile.id
        settings.save(**changes)


def sign_out(account_id: str | None = None) -> None:
    """Forget one account (the active one by default); the others stay signed in."""
    with _lock:
        target = account_id or (active().id if active() else "")
        if not target:
            return
        _store_refresh(target, "")
        for key in [k for k in _access if k[0] == target]:
            _access.pop(key)
        for key in [k for k in _owned if k[0] == target]:
            _owned.pop(key)
        rest = [a for a in accounts() if a.id != target]
        changes: dict = {"msa_accounts": [asdict(a) for a in rest]}
        if settings.load().get("msa_active") == target:
            changes["msa_active"] = rest[0].id if rest else ""
        settings.save(**changes)


# ---- OAuth -------------------------------------------------------------------------------------------

def _form(url: str, fields: dict[str, str]) -> tuple[int, dict]:
    status, body = net.post(url, urllib.parse.urlencode(fields).encode(),
                            {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    try:
        return status, json.loads(body or b"{}")
    except ValueError:
        return status, {}


def start_device_code(client_id: str = CLIENT_ID) -> DeviceCode:
    status, data = _form(CONNECT_URL, {"client_id": client_id, "scope": SIGNIN_SCOPE, "response_type": "device_code"})
    if status != 200 or not data.get("user_code"):
        raise AccountError(data.get("error_description") or f"login.live.com refused the sign-in request (HTTP {status})")
    return DeviceCode(data["user_code"], data["device_code"], LINK_URL.format(code=data["user_code"]),
                      max(1, int(data.get("interval") or 5)), time.time() + float(data.get("expires_in") or 900))


def poll_device_code(code: DeviceCode, cancelled: Callable[[], bool] = lambda: False) -> Profile:
    """Wait until the user approves the code in their browser; adds that account (or refreshes it if known)."""
    data = poll_client_code(code, CLIENT_ID, cancelled)
    profile = _xbox_profile(data["access_token"])
    profile.id = profile.xuid or data.get("user_id") or f"account-{int(time.time())}"
    with _lock:
        if data.get("refresh_token"):
            _store_refresh(profile.id, data["refresh_token"])
        _access[(profile.id, SIGNIN_SCOPE)] = (data["access_token"], time.time() + float(data.get("expires_in") or 3600))
        _save_account(profile)
    return profile


def poll_client_code(code: DeviceCode, client_id: str, cancelled: Callable[[], bool] = lambda: False) -> dict:
    """Wait until the user approves a device code for client_id; the token response (nothing is stored)."""
    interval = code.interval
    while time.time() < code.expires_at:
        for _ in range(interval * 10):
            if cancelled():
                raise AccountError("Sign-in cancelled")
            time.sleep(0.1)
        try:
            status, data = _form(TOKEN_URL, {"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                                             "client_id": client_id, "device_code": code.device_code})
        except net.NetError:
            continue                                   # transient; keep polling
        if status == 200 and data.get("access_token"):
            return data
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


def _token_grant(account_id: str, scope: str) -> dict:
    refresh = _load_refresh(account_id)
    if not refresh:
        raise AccountError("Not signed in")
    status, data = _form(TOKEN_URL, {"grant_type": "refresh_token", "client_id": CLIENT_ID,
                                     "scope": scope, "refresh_token": refresh})
    if status != 200 or not data.get("access_token"):
        error = data.get("error", "")
        if error in ("invalid_grant", "interaction_required"):
            who = get(account_id)
            raise AccountError(f"The sign-in for {who.name if who else 'this account'} has expired. Sign in again.")
        raise AccountError(data.get("error_description") or error or f"HTTP {status}")
    if data.get("refresh_token") and data["refresh_token"] != refresh:
        _store_refresh(account_id, data["refresh_token"])
    return data


def access_token(scope: str = SIGNIN_SCOPE, account_id: str | None = None) -> str:
    """An access token (an RPS ticket) for a service scope, minted from the account's refresh token."""
    with _lock:
        account_id = account_id or (active().id if active() else "")
        if not account_id:
            raise AccountError("Not signed in")
        cached = _access.get((account_id, scope))
        if cached and cached[1] > time.time() + 120:
            return cached[0]
        data = _token_grant(account_id, scope)
        _access[(account_id, scope)] = (data["access_token"], time.time() + float(data.get("expires_in") or 3600))
        return data["access_token"]


def store_ticket(account_id: str | None = None) -> str | None:
    """The MSA ticket for the Store's package delivery (FE3), or None when signed out or refused."""
    if not signed_in():
        return None
    try:
        # FE3 takes the RPS ticket as base64("t=" + token); the bare token fails its security check
        return base64.b64encode(("t=" + access_token(FE3_SCOPE, account_id)).encode()).decode()
    except (AccountError, net.NetError):
        return None


# ---- what the accounts own ----------------------------------------------------------------------------

def owns(product_id: str, account_id: str | None = None) -> bool:
    """Does this account own the product (purchase, free claim or bundle)? As Wine-UWP's winexboxauth asks:
    collections.mp.microsoft.com with the account as beneficiary. Subscription access (Game Pass) is not ownership."""
    account_id = account_id or (active().id if active() else "")
    if not account_id:
        return False
    product_id = product_id.upper()
    with _lock:
        cached = _owned.get((account_id, product_id))
        if cached and cached[1] > time.time():
            return cached[0]
        data = _token_grant(account_id, COLLECTIONS_SCOPE)
    ticket, puid = data["access_token"], data.get("user_id", "")
    body = {"beneficiaries": [{"identityType": "msa", "identityValue": ticket, "localTicketReference": puid}],
            "maxPageSize": 25, "market": settings.load().get("market") or "US",
            "productSkuIds": [{"productId": product_id}], "validityType": "Valid"}
    status, raw = net.post(COLLECTIONS_URL, json.dumps(body).encode(),
                           {"Content-Type": "application/json", "Authorization": f'WLID1.0="t={ticket}"'})
    if status != 200:
        raise AccountError(f"The licence service answered HTTP {status}")
    owned = bool(json.loads(raw or b"{}").get("items"))
    with _lock:
        _owned[(account_id, product_id)] = (owned, time.time() + 600)
    return owned


def owners(product_id: str) -> list[Profile]:
    """Every signed-in account that owns the product, the active account first."""
    found = accounts()
    current = active()
    found.sort(key=lambda a: a.id != (current.id if current else ""))
    out, errors = [], []
    for profile in found:
        try:
            if owns(product_id, profile.id):
                out.append(profile)
        except (AccountError, net.NetError) as exc:
            errors.append(exc)
    if not out and errors and len(errors) == len(found):
        raise AccountError(str(errors[0]))
    return out


# ---- Xbox Live profile -------------------------------------------------------------------------------

def _xbl_post(url: str, body: dict) -> dict:
    status, data = net.post(url, json.dumps(body).encode(), {"Content-Type": "application/json",
                                                             "Accept": "application/json",
                                                             "x-xbl-contract-version": "1"})
    if status != 200:
        raise AccountError(f"Xbox Live refused the sign-in (HTTP {status})")
    return json.loads(data)


def _xbox_profile(rps: str) -> Profile:
    user = _xbl_post("https://user.auth.xboxlive.com/user/authenticate", {
        "RelyingParty": "http://auth.xboxlive.com", "TokenType": "JWT",
        "Properties": {"AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com", "RpsTicket": f"t={rps}"}})
    xsts = _xbl_post("https://xsts.auth.xboxlive.com/xsts/authorize", {
        "RelyingParty": "http://xboxlive.com", "TokenType": "JWT",
        "Properties": {"SandboxId": "RETAIL", "UserTokens": [user["Token"]]}})
    claims = (xsts.get("DisplayClaims", {}).get("xui") or [{}])[0]
    return Profile(claims.get("gtg", ""), claims.get("xid", ""), claims.get("uhs", ""))


def profile(refresh: bool = False, account_id: str | None = None) -> Profile:
    """An account's gamertag (the active account by default); refresh asks Xbox Live again."""
    current = get(account_id) if account_id else active()
    if current is None:
        raise AccountError("Not signed in")
    if current.gamertag and not refresh:
        return current
    fresh = _xbox_profile(access_token(SIGNIN_SCOPE, current.id))
    fresh.id = current.id
    _save_account(fresh)
    return fresh


def cached_profile() -> Profile | None:
    """The active account without touching the network (for drawing the header)."""
    return active()


def _jwt_claims(token: str) -> dict:  # debugging helper
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return {}
