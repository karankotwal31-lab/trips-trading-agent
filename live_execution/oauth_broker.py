from __future__ import annotations
import json, urllib.error, urllib.request
from .broker import BrokerError
from .gates import require_live_opt_in

class AlpacaOAuthBroker:
    def __init__(self, access_token, *, env="live"):
        if env not in {"live","paper"}: raise BrokerError("invalid Alpaca environment")
        if not access_token: raise BrokerError("missing OAuth access token")
        self.token=access_token
        self.base="https://api.alpaca.markets" if env=="live" else "https://paper-api.alpaca.markets"
    def _request(self,method,path,body=None):
        data=None if body is None else json.dumps(body,separators=(",",":")).encode()
        req=urllib.request.Request(self.base+path,data=data,method=method,
            headers={"Authorization":f"Bearer {self.token}","Content-Type":"application/json"})
        try:
            with urllib.request.urlopen(req,timeout=15) as r:
                raw=r.read(); return {} if not raw else json.loads(raw.decode())
        except urllib.error.HTTPError as e: raise BrokerError(f"broker HTTP {e.code}") from e
        except Exception as e: raise BrokerError("broker transport failure") from e
    def account(self): return self._request("GET","/v2/account")
    def positions(self): return self._request("GET","/v2/positions")
    def orders(self): return self._request("GET","/v2/orders?status=all&limit=500")
    def submit_order(self,intent):
        if self.base == "https://api.alpaca.markets":
            require_live_opt_in()
        payload={"symbol":intent["symbol"],"qty":str(intent["qty"]),"side":intent["side"].lower(),
          "type":intent.get("type","market"),"time_in_force":intent.get("time_in_force","day"),
          "client_order_id":intent["idempotency_key"]}
        r=self._request("POST","/v2/orders",payload)
        return {"broker_order_id":r.get("id"),"client_order_id":r.get("client_order_id"),
          "status":r.get("status"),"symbol":r.get("symbol"),"side":str(r.get("side","")).upper(),
          "qty":float(r.get("qty") or 0),"filled_qty":float(r.get("filled_qty") or 0)}
    def cancel_order(self,broker_order_id):
        self._request("DELETE",f"/v2/orders/{broker_order_id}")
        return {"broker_order_id":broker_order_id,"cancel_requested":True}
