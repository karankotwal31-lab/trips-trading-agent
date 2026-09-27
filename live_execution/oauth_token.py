from __future__ import annotations
import json, os, urllib.parse, urllib.request, urllib.error
from .broker import BrokerError
from .oauth import TOKEN_URL

def exchange_code(code):
    fields={"grant_type":"authorization_code","code":code,
            "client_id":os.environ.get("ALPACA_OAUTH_CLIENT_ID",""),
            "client_secret":os.environ.get("ALPACA_OAUTH_CLIENT_SECRET",""),
            "redirect_uri":os.environ.get("ALPACA_OAUTH_REDIRECT_URI","")}
    if not all(fields.values()): raise BrokerError("OAuth exchange configuration incomplete")
    req=urllib.request.Request(TOKEN_URL,data=urllib.parse.urlencode(fields).encode(),
        method="POST",headers={"Content-Type":"application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req,timeout=15) as r: result=json.loads(r.read().decode())
    except urllib.error.HTTPError as e: raise BrokerError(f"OAuth token exchange HTTP {e.code}") from e
    except Exception as e: raise BrokerError("OAuth token exchange failed") from e
    token=result.get("access_token")
    if not token: raise BrokerError("OAuth response missing access token")
    return {"access_token":token,"token_type":result.get("token_type","bearer"),"scope":result.get("scope","")}
