import os, unittest
from urllib.parse import urlparse, parse_qs
from live_execution.oauth import authorization_url, new_state
from live_execution.oauth_broker import AlpacaOAuthBroker
from live_execution.broker import BrokerError

class OAuthTests(unittest.TestCase):
    def setUp(self):
        os.environ["ALPACA_OAUTH_CLIENT_ID"]="client"
        os.environ["ALPACA_OAUTH_REDIRECT_URI"]="https://example.invalid/oauth/callback"
    def test_live_authorization_url_is_scoped_and_stateful(self):
        state=new_state(); q=parse_qs(urlparse(authorization_url(state)).query)
        self.assertEqual(q["state"],[state]); self.assertEqual(q["env"],["live"])
        self.assertEqual(q["scope"],["trading"]); self.assertEqual(q["response_type"],["code"])
    def test_oauth_broker_live_endpoint(self):
        self.assertEqual(AlpacaOAuthBroker("token").base,"https://api.alpaca.markets")
    def test_token_required(self):
        with self.assertRaises(BrokerError): AlpacaOAuthBroker("")
if __name__=="__main__": unittest.main()
