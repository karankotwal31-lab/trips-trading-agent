from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .broker import BrokerError


class UpstoxBroker:
    """Fail-closed Upstox adapter for Trip's execution boundary."""

    def __init__(self):
        env = os.getenv("UPSTOX_ENV", "sandbox").strip().lower()
        if env not in {"sandbox", "live"}:
            raise BrokerError("UPSTOX_ENV must be sandbox or live")
        if env == "live" and os.getenv("TRIPS_LIVE_EXECUTION", "") != "ENABLED":
            raise BrokerError("live Upstox endpoint requires explicit Trip's live opt-in")
        token = os.getenv("UPSTOX_ACCESS_TOKEN", "").strip()
        if not token:
            raise BrokerError("Upstox access token missing")
        self.env = env
        self.token = token
        self.data_base = "https://api.upstox.com"
        self.trade_base = (
            "https://api-sandbox.upstox.com/v3"
            if env == "sandbox"
            else "https://api-hft.upstox.com/v3"
        )

    def _request(self, method, base, path, body=None):
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
        headers = {
            "Authorization": "Bearer " + self.token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        algo = os.getenv("UPSTOX_ALGO_NAME", "").strip()
        if algo:
            headers["X-Algo-Name"] = algo
        req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                raw = response.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raise BrokerError(f"broker HTTP {exc.code}") from exc
        except Exception as exc:
            raise BrokerError("broker transport failure") from exc

    def account(self):
        return self._request("GET", self.data_base, "/v2/user/profile")

    def positions(self):
        response = self._request(
            "GET", self.data_base, "/v2/portfolio/short-term-positions"
        )
        return response.get("data", []) if isinstance(response, dict) else []

    def orders(self):
        response = self._request("GET", self.data_base, "/v2/order/retrieve-all")
        rows = response.get("data", []) if isinstance(response, dict) else []
        normalized = []
        for row in rows if isinstance(rows, list) else []:
            order_id = row.get("order_id")
            if not order_id:
                continue
            normalized.append(
                {
                    "broker_order_id": str(order_id),
                    "symbol": row.get("trading_symbol") or row.get("tradingsymbol"),
                    "side": row.get("transaction_type"),
                    "qty": float(row.get("quantity", 0)),
                    "filled_qty": float(row.get("filled_quantity", 0)),
                    "status": row.get("status"),
                }
            )
        return normalized

    def submit_order(self, intent):
        qty = float(intent["qty"])
        if qty <= 0 or not qty.is_integer():
            raise BrokerError("Upstox order quantity must be a positive whole number")
        payload = {
            "quantity": int(qty),
            "product": intent.get("product", "D"),
            "validity": intent.get("validity", "DAY"),
            "price": float(intent.get("price", 0)),
            "tag": intent["idempotency_key"][:40],
            "instrument_token": intent["instrument_token"],
            "order_type": intent.get("order_type", "MARKET"),
            "transaction_type": intent["side"].upper(),
            "disclosed_quantity": 0,
            "trigger_price": float(intent.get("trigger_price", 0)),
            "is_amo": False,
            "slice": bool(intent.get("slice", False)),
            "market_protection": int(intent.get("market_protection", -1)),
        }
        response = self._request("POST", self.trade_base, "/order/place", payload)
        data = response.get("data", {}) if isinstance(response, dict) else {}
        order_ids = data.get("order_ids", [])
        if not isinstance(order_ids, list) or not order_ids:
            legacy_id = data.get("order_id")
            order_ids = [legacy_id] if legacy_id else []
        if not order_ids:
            raise BrokerError("broker did not return a verifiable order id")
        if len(order_ids) != 1:
            raise BrokerError("sliced multi-order response requires explicit reconciliation")
        return {
            "broker_order_id": str(order_ids[0]),
            "client_order_id": intent["idempotency_key"],
            "status": "submitted",
            "symbol": intent.get("symbol"),
            "side": intent["side"].upper(),
            "qty": qty,
            "filled_qty": 0.0,
        }

    def cancel_order(self, broker_order_id):
        order_id = urllib.parse.quote(str(broker_order_id), safe="")
        self._request(
            "DELETE", self.trade_base, f"/order/cancel?order_id={order_id}"
        )
        return {
            "broker_order_id": str(broker_order_id),
            "cancel_requested": True,
        }
