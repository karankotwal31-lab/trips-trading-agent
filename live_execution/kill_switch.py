from __future__ import annotations
from dataclasses import dataclass

@dataclass
class KillState:
    engaged: bool=False
    reason: str=""

class KillSwitch:
    def __init__(self): self.state=KillState()
    def engage(self, reason:str) -> None:
        self.state=KillState(True, reason[:500] or "operator kill")
    def clear(self, *, reconciliation_ok:bool, explicit_operator_clear:bool) -> None:
        if not reconciliation_ok: raise RuntimeError("cannot clear kill switch before reconciliation")
        if not explicit_operator_clear: raise RuntimeError("explicit operator clear required")
        self.state=KillState()
    def require_clear(self) -> None:
        if self.state.engaged: raise RuntimeError(f"execution halted: {self.state.reason}")
