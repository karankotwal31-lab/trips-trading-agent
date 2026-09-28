from __future__ import annotations
import json, os, urllib.error, urllib.parse, urllib.request
from .broker import BrokerError

class UpstoxBroker:
    """Trip's Upstox execution adapter.

    Credentials are environment-only. Sandbox is the default and production
    requires an explicit Trip's operator opt-in.
    """
    def __init__(self):
        env=os.getenv("UPSTOX_ENV","sandbox").strip().lower()
        if env not in {"sandbox","live"}:
            raise BrokerError("UPSTOX_ENV must be sandbox or live")
        if env=="live" and os.getenv("TRIPS_LIVE_EXECUTION","")!="ENABLED":
            raise BrokerError("live Upstox endpoint requires explicit Trip's live opt-in")
        self.env=env
        self.token=os.getenv("UPSTOX_ACCESS_TOKEN","")
        if not self.token:
            raise BrokerError("Upstox access token missing")
        self.base="https://api-sandbox.upstox.com/v3" if env=="sandbox" else "https://api-hft.upstox.com"

    def _request(self,method,path,body=None):
        data=None if body is None else json.dumps(body,separators=(",",":")).encode()
        headers={"Authorization":"Bearer "+self.token,"Accept":"application/json","Content-Type":"application/json"}
        algo=os.getenv("UPSTOX_ALGO_NAME","").strip()
        if algo: headers["X-Algo-Name"]=algo
        req=urllib.request.Request(self.base+path,data=data,method=method,headers=headers)
        try:
            with urllib.request.urlopen(req,timeout=15) as r:
                raw=r.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raise BrokerError(f"broker HTTP {e.code}") from e
        except Exception as e:
            raise BrokerError("broker transport failure") from e

    def account(self):
        return self._request("GET","/v2/user/profile")

    def positions(self):
        r=self._request("GET","/v2/portfolio/short-term-positions")
        return r.get("data",[]) if isinstance(r,dict) else []

    def orders(self):
        r=self._request("GET","/v2/order/retrieve-all")
        return r.get("data",[]) if isinstance(r,dict) else []

    def submit_order(self,intent):
        payload={
            "quantity":int(intent["qty"]),
            "product":intent.get("product","D"),
            "validity":intent.get("validity","DAY"),
            "price":float(intent.get("price",0)),
            "tag":intent["idempotency_key"][:40],
            "instrument_token":intent["instrument_token"],
            "order_type":intent.get("order_type","MARKET"),
            "transaction_type":intent["side"].upper(),
            "disclosed_quantity":0,
            "trigger_price":float(intent.get("trigger_price",0)),
            "is_amo":False,
            "slice":False,
            "market_protection":intent.get("market_protection",-1),
        }
        order_path="/order/place" if self.env=="sandbox" else "/v3/order/place"\n        r=self._request("POST",order_path,payload)
        data=r.get("data",{}) if isinstance(r,dict) else {}
        oid=data.get("order_id")
        if not oid: raise BrokerError("broker did not return an order id")
        return {"broker_order_id":oid,"client_order_id":intent["idempotency_key"],
                "status":"submitted","symbol":intent.get("symbol"),
                "side":intent["side"].upper(),"qty":float(intent["qty"]),"filled_qty":0.0}

    def cancel_order(self,broker_order_id):
        q=urllib.parse.quote(str(broker_order_id),safe="")
        cancel_path=f"/order/cancel?order_id={q}" if self.env=="sandbox" else f"/v3/order/cancel?order_id={q}"\n        self._request("DELETE",cancel_path)
        return {"broker_order_id":broker_order_id,"cancel_requested":True}
