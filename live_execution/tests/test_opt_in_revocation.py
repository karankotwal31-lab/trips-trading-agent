import os
import unittest
from unittest.mock import patch
from live_execution.alpaca_adapter import AlpacaBroker
from live_execution.oauth_broker import AlpacaOAuthBroker
from live_execution.upstox_adapter import UpstoxBroker
from live_execution.gates import LiveGateError


class OptInRevocationTests(unittest.TestCase):
    def test_live_adapters_recheck_opt_in_before_submission(self):
        config = dict(TRIPS_LIVE_EXECUTION='ENABLED', UPSTOX_ENV='live',
                      UPSTOX_ACCESS_TOKEN='test', ALPACA_ENV='live',
                      ALPACA_API_KEY_ID='test', ALPACA_API_SECRET_KEY='test')
        with patch.dict(os.environ, config, clear=True):
            brokers = [UpstoxBroker(), AlpacaBroker(), AlpacaOAuthBroker('test')]
            os.environ.pop('TRIPS_LIVE_EXECUTION')
            for broker in brokers:
                with self.subTest(broker=type(broker).__name__), patch.object(broker, '_request') as request:
                    with self.assertRaises(LiveGateError):
                        broker.submit_order({})
                    request.assert_not_called()
