import os, unittest
from unittest.mock import patch
from live_execution.alpaca_adapter import AlpacaBroker
from live_execution.broker import BrokerError

class AlpacaAdapterTests(unittest.TestCase):
    def setUp(self):
        for k in ("ALPACA_ENV","ALPACA_API_KEY_ID","ALPACA_API_SECRET_KEY","TRIPS_LIVE_EXECUTION"):
            os.environ.pop(k,None)
    def test_defaults_paper(self):
        os.environ["ALPACA_API_KEY_ID"]="x"; os.environ["ALPACA_API_SECRET_KEY"]="y"
        self.assertEqual(AlpacaBroker().base,"https://paper-api.alpaca.markets")
    def test_live_requires_trip_opt_in(self):
        os.environ.update(ALPACA_ENV="live",ALPACA_API_KEY_ID="x",ALPACA_API_SECRET_KEY="y")
        with self.assertRaises(BrokerError): AlpacaBroker()
    def test_live_after_explicit_opt_in(self):
        os.environ.update(ALPACA_ENV="live",ALPACA_API_KEY_ID="x",ALPACA_API_SECRET_KEY="y",TRIPS_LIVE_EXECUTION="ENABLED")
        self.assertEqual(AlpacaBroker().base,"https://api.alpaca.markets")
    def test_credentials_required(self):
        with self.assertRaises(BrokerError): AlpacaBroker()
if __name__=="__main__": unittest.main()
