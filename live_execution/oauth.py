from __future__ import annotations
import hashlib, os, secrets
from urllib.parse import urlencode
from .broker import BrokerError

AUTHORIZE_URL="https://app.alpaca.markets/oauth/authorize"
TOKEN_URL="https://api.alpaca.markets/oauth/token"

def _cfg(name):
    value=os.getenv(name,"").strip()
    if not value: raise BrokerError(f"missing {name}")
    return value

def new_state():
    return secrets.token_urlsafe(32)

def state_digest(state):
    return hashlib.sha256(state.encode()).hexdigest()

def authorization_url(state, *, env="live", scope="trading"):
    if env not in {"live","paper"}: raise BrokerError("invalid Alpaca OAuth env")
    q=urlencode({"response_type":"code","client_id":_cfg("ALPACA_OAUTH_CLIENT_ID"),
                 "redirect_uri":_cfg("ALPACA_OAUTH_REDIRECT_URI"),"state":state,
                 "scope":scope,"env":env})
    return f"{AUTHORIZE_URL}?{q}"
