import hmac
import json
import os
import secrets
from base64 import b64encode

from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from flask import Blueprint, abort, current_app, redirect, request, session, url_for

discord_auth = Blueprint("discord_auth", __name__)

DISCORD_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_USER_URL = "https://discord.com/api/v10/users/@me"

def _oauth_config() -> tuple[str, str, str]:
    client_id = os.environ.get("DISCORD_CLIENT_ID")
    client_secret = os.environ.get("DISCORD_CLIENT_SECRET")
    redirect_uri = os.environ.get("DISCORD_REDIRECT_URI")
    if not client_id or not client_secret or not redirect_uri:
        abort(503, description=(
            "Discord sign-in is not configured. Set DISCORD_CLIENT_ID, "
            "DISCORD_CLIENT_SECRET, and DISCORD_REDIRECT_URI."
        ))

    redirect_parts = urlsplit(redirect_uri)
    if not redirect_parts.scheme or not redirect_parts.netloc or redirect_parts.path != "/discord/callback":
        abort(503, description="DISCORD_REDIRECT_URI must be an absolute URL ending in /discord/callback.")

    if not current_app.secret_key:
        abort(503, description="Set FLASK_SECRET_KEY before enabling Discord sign-in.")
    
    return client_id, client_secret, redirect_uri

def _post_form(
    url: str,
    values: dict[str, str],
    basic_auth: tuple[str, str] | None = None,
) -> dict:
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "User-Agent": "MonkeFramesExtensionBrowser/1.0",
    }
    if basic_auth:
        credentials = f"{basic_auth[0]}:{basic_auth[1]}".encode("utf-8")
        headers["Authorization"] = f"Basic {b64encode(credentials).decode('ascii')}"
    request = Request(
        url,
        data=urlencode(values).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_user(access_token: str) -> dict:
    request = Request(
        DISCORD_USER_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "MonkeFramesExtensionBrowser/1.0",
        },
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _avatar_url(user: dict) -> str:
    avatar_hash = user.get("avatar")
    user_id = str(user["id"])
    if avatar_hash:
        extension = "gif" if avatar_hash.startswith("a_") else "png"
        return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.{extension}?size=64"
    default_index = (int(user_id) >> 22) % 6
    return f"https://cdn.discordapp.com/embed/avatars/{default_index}.png"


@discord_auth.get("/discord/login")
def login():
    client_id, _, redirect_uri = _oauth_config()
    next_path = request.args.get("next")
    if next_path not in {"/upload", "/admin"}:
        next_path = None
    callback_parts = urlsplit(redirect_uri)
    callback_origin = f"{callback_parts.scheme}://{callback_parts.netloc}"
    if not callback_parts.scheme or not callback_parts.netloc:
        abort(503, description="DISCORD_REDIRECT_URI must be an absolute URL.")

    # Create the Flask session on the same host that receives Discord's
    # callback; cookies set for localhost are not sent to 127.0.0.1 (or vice versa).
    if request.host_url.rstrip("/") != callback_origin:
        target = f"{callback_origin}/discord/login"
        if next_path:
            target += "?" + urlencode({"next": next_path})
        return redirect(target, code=302)

    state = secrets.token_urlsafe(32)
    session["discord_oauth_state"] = state
    if next_path:
        session["discord_after_login"] = next_path
    query = urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "identify",
        "state": state,
    })
    return redirect(f"{DISCORD_AUTHORIZE_URL}?{query}")


@discord_auth.get("/discord/callback")
def callback():
    client_id, client_secret, redirect_uri = _oauth_config()
    returned_state = session.pop("discord_oauth_state", None)
    state = request.args.get("state", "")
    if not returned_state:
        current_app.logger.warning(
            "Discord OAuth callback has no saved state; the session cookie was "
            "not returned. Check that the browser uses the same hostname as "
            "DISCORD_REDIRECT_URI and restart Flask after changing FLASK_SECRET_KEY."
        )
        abort(400, description="Discord sign-in session was not found. Start sign-in again using the same hostname as the callback URL.")
    if not hmac.compare_digest(returned_state, state):
        current_app.logger.warning("Discord OAuth callback state did not match the saved session state.")
        abort(400, description="Discord sign-in state validation failed.")

    if request.args.get("error"):
        return redirect(url_for("browse"))
    code = request.args.get("code")
    if not code:
        abort(400, description="Discord did not return an authorization code.")

    stage = "token exchange"
    try:
        token_data = _post_form(DISCORD_TOKEN_URL, {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        }, basic_auth=(client_id, client_secret))
        stage = "profile lookup"
        user = _get_user(token_data["access_token"])
    except HTTPError as error:
        try:
            details = json.loads(error.read().decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            details = {}
        current_app.logger.warning(
            "Discord OAuth %s failed: HTTP %s (%s: %s)",
            stage,
            error.code,
            details.get("error", "unknown_error"),
            details.get("error_description", "no description"),
        )
        if not details:
            current_app.logger.warning(
                "Discord returned a non-JSON OAuth error (content-type=%s, server=%s).",
                error.headers.get("Content-Type", "unknown"),
                error.headers.get("Server", "unknown"),
            )
        abort(502, description="Discord sign-in failed. Check the Flask log for the OAuth error details, then try again.")
    except (URLError, TimeoutError, KeyError, ValueError) as error:
        current_app.logger.warning("Discord OAuth %s failed: %s", stage, type(error).__name__)
        abort(502, description="Could not complete Discord sign-in. Please try again.")

    session.pop("discord_user", None)
    session["discord_user"] = {
        "id": str(user["id"]),
        "username": user.get("global_name") or user["username"],
        "avatar_url": _avatar_url(user),
    }
    destination = session.pop("discord_after_login", None)
    if destination in {"/upload", "/admin"}:
        return redirect(destination)
    return redirect(url_for("browse"))


@discord_auth.get("/discord/logout")
def logout():
    session.pop("discord_user", None)
    session.pop("discord_oauth_state", None)
    return redirect(url_for("browse"))
