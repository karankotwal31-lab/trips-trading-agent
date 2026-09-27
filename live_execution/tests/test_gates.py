import os, unittest
from live_execution.gates import LiveGateError, LiveLimits, require_live_opt_in, validate_intent

class GateTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("TRIPS_LIVE_EXECUTION", None)
        self.intent={"side":"BUY","qty":1,"reference_price":50,"idempotency_key":"x","data_fresh":True,"approved_core":True}

    def test_default_is_disabled(self):
        with self.assertRaises(LiveGateError): require_live_opt_in()

    def test_explicit_opt_in(self):
        os.environ["TRIPS_LIVE_EXECUTION"]="ENABLED"; require_live_opt_in()

    def test_notional_cap(self):
        bad=dict(self.intent, qty=3)
        with self.assertRaises(LiveGateError): validate_intent(bad, limits=LiveLimits(max_order_notional=100), daily_pnl=0, open_positions=0)

    def test_loss_kill_switch(self):
        with self.assertRaises(LiveGateError): validate_intent(self.intent, limits=LiveLimits(), daily_pnl=-25, open_positions=0)

    def test_stale_data_fails_closed(self):
        bad=dict(self.intent, data_fresh=False)
        with self.assertRaises(LiveGateError): validate_intent(bad, limits=LiveLimits(), daily_pnl=0, open_positions=0)

if __name__=="__main__": unittest.main()
