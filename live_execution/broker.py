from __future__ import annotations
from typing import Protocol

class BrokerError(RuntimeError):
    pass

class Broker(Protocol):
    def account(self) -> dict: ...
    def positions(self) -> list[dict]: ...
    def submit_order(self, intent: dict) -> dict: ...
    def cancel_order(self, broker_order_id: str) -> dict: ...

class DisabledBroker:
    """Default adapter: deliberately incapable of moving money."""
    def account(self) -> dict:
        raise BrokerError("no live broker configured")
    def positions(self) -> list[dict]:
        raise BrokerError("no live broker configured")
    def submit_order(self, intent: dict) -> dict:
        raise BrokerError("no live broker configured")
    def cancel_order(self, broker_order_id: str) -> dict:
        raise BrokerError("no live broker configured")
