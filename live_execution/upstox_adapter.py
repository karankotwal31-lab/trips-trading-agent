from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request

from .broker import BrokerError
from .gates import require_live_opt_in


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
        data = None if body is None else json.dumps(body, separators=(",", ":"), allow_nan=False).encode()
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
        return self._rows(response)

    @staticmethod
    def _rows(response):
        if not isinstance(response, dict) or response.get("status") != "success":
            raise BrokerError("broker snapshot unavailable")
        rows = response.get("data")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise BrokerError("malformed broker snapshot")
        return rows

    def orders(self):
        response = self._request("GET", self.data_base, "/v2/order/retrieve-all")
        rows = self._rows(response)
        normalized = []
        for row in rows:
            order_id = row.get("order_id")
            if not order_id:
                raise BrokerError("broker order missing identity")
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
        if self.env == "live":
            require_live_opt_in()
        if intent.get("slice", False) is not False:
            raise BrokerError("sliced orders are unsupported")
        key = intent.get("idempotency_key")
        if not isinstance(key, str) or not key.strip() or len(key) > 40:
            raise BrokerError("order tag must contain 1 to 40 characters")
        try:
            qty = float(intent["qty"])
            price = float(intent.get("price", 0))
            trigger = float(intent.get("trigger_price", 0))
        except (KeyError, TypeError, ValueError, OverflowError):
            raise BrokerError("invalid order numbers") from None
        if isinstance(intent["qty"], bool) or not math.isfinite(qty) or qty <= 0 or not qty.is_integer():
            raise BrokerError("Upstox order quantity must be a positive whole number")
        if any(not math.isfinite(value) or value < 0 for value in (price, trigger)):
            raise BrokerError("invalid order price")
        payload = {
            "quantity": int(qty),
            "product": intent.get("product", "D"),
            "validity": intent.get("validity", "DAY"),
            "price": price,
            "tag": key,
            "instrument_token": intent["instrument_token"],
            "order_type": intent.get("order_type", "MARKET"),
            "transaction_type": intent["side"].upper(),
            "disclosed_quantity": 0,
            "trigger_price": trigger,
            "is_amo": False,
            "slice": False,
            "market_protection": int(intent.get("market_protection", -1)),
        }
        response = self._request("POST", self.trade_base, "/order/place", payload)
        if not isinstance(response, dict) or response.get("status") != "success":
            raise BrokerError("broker did not acknowledge order; reconcile before retry")
        data = response.get("data")
        if not isinstance(data, dict):
            raise BrokerError("malformed order acknowledgement; reconcile before retry")
        order_ids = data.get("order_ids", [])
        if not isinstance(order_ids, list) or not order_ids:
            legacy_id = data.get("order_id")
            order_ids = [legacy_id] if legacy_id else []
        if not order_ids:
            raise BrokerError("broker did not return a verifiable order id")
        if len(order_ids) != 1:
            raise BrokerError("sliced multi-order response requires explicit reconciliation")
        if not isinstance(order_ids[0], str) or not order_ids[0].strip():
            raise BrokerError("invalid broker order identity; reconcile before retry")
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
        response = self._request(
            "DELETE", self.trade_base, f"/order/cancel?order_id={order_id}"
        )
        if not isinstance(response, dict) or response.get("status") != "success":
            raise BrokerError("broker did not acknowledge cancellation")
        return {
            "broker_order_id": str(broker_order_id),
            "cancel_requested": True,
        }
