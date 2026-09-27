import unittest
from live_execution.kill_switch import KillSwitch
from live_execution.readiness import REQUIRED_CHECKS,evaluate
from live_execution.state import verify_reconciliation

class SafetyTests(unittest.TestCase):
    def test_reconciliation_detects_unknown_broker_order(self):
        r=verify_reconciliation([], [{"broker_order_id":"x","symbol":"A","side":"BUY","qty":1,"filled_qty":0,"status":"NEW"}])
        self.assertFalse(r["ok"]); self.assertEqual(r["unknown_remote"],["x"])
    def test_reconciliation_exact(self):
        x={"broker_order_id":"x","symbol":"A","side":"BUY","qty":1,"filled_qty":1,"status":"FILLED"}
        self.assertTrue(verify_reconciliation([x],[dict(x)])["ok"])
    def test_kill_switch_requires_reconciliation_and_operator(self):
        k=KillSwitch(); k.engage("test")
        with self.assertRaises(RuntimeError): k.require_clear()
        with self.assertRaises(RuntimeError): k.clear(reconciliation_ok=False,explicit_operator_clear=True)
        with self.assertRaises(RuntimeError): k.clear(reconciliation_ok=True,explicit_operator_clear=False)
        k.clear(reconciliation_ok=True,explicit_operator_clear=True); k.require_clear()
    def test_readiness_fails_closed(self):
        self.assertFalse(evaluate({})["ready"])
        self.assertTrue(evaluate({k:True for k in REQUIRED_CHECKS})["ready"])
if __name__=="__main__": unittest.main()
