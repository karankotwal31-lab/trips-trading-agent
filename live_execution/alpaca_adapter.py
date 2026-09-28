from __future__ import annotations
import json, os, urllib.error, urllib.request
from .broker import BrokerError
from .gates import require_live_opt_in

class AlpacaBroker:
    """Minimal Alpaca Trading API adapter.

    Defaults to paper. Live endpoint requires both TRIPS_LIVE_EXECUTION=ENABLED and
    ALPACA_ENV=live. Credentials are read from environment only.
    """
    def __init__(self):
        env=os.getenv("ALPACA_ENV","paper").strip().lower()
        if env not in {"paper","live"}: raise BrokerError("ALPACA_ENV must be paper or live")
        if env=="live" and os.getenv("TRIPS_LIVE_EXECUTION","")!="ENABLED":
            raise BrokerError("live Alpaca endpoint requires explicit Trip's live opt-in")
        self.base=("https://api.alpaca.markets" if env=="live" else "https://paper-api.alpaca.markets")
        self.key=os.getenv("ALPACA_API_KEY_ID","")
        self.secret=os.getenv("ALPACA_API_SECRET_KEY","")
        if not self.key or not self.secret: raise BrokerError("Alpaca credentials missing")
    def _request(self,method,path,body=None):
        data=None if body is None else json.dumps(body,separators=(",",":")).encode()
        req=urllib.request.Request(self.base+path,data=data,method=method,headers={
            "APCA-API-KEY-ID":self.key,"APCA-API-SECRET-KEY":self.secret,"Content-Type":"application/json"})
        try:
            with urllib.request.urlopen(req,timeout=15) as r: return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise BrokerError(f"broker HTTP {e.code}") from e
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
