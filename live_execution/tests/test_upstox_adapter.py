import os
import unittest
from unittest.mock import patch
from live_execution.upstox_adapter import UpstoxBroker
from live_execution.broker import BrokerError


class UpstoxTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'UPSTOX_ACCESS_TOKEN': 'test-only'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.broker = UpstoxBroker()
        self.intent = dict(qty=1, side='BUY', symbol='TEST', instrument_token='NSE_EQ|TEST', idempotency_key='unique-1')

    def test_default_sandbox(self):
        self.assertEqual(self.broker.trade_base, 'https://api-sandbox.upstox.com/v3')

    def test_live_requires_opt_in(self):
        os.environ['UPSTOX_ENV'] = 'live'
        with self.assertRaises(BrokerError):
            UpstoxBroker()

    def test_slicing_rejected_before_network(self):
        with patch.object(self.broker, '_request') as request:
            for value in (True, 'false', 1, None):
                with self.subTest(value=value), self.assertRaises(BrokerError):
                    self.broker.submit_order(dict(self.intent, slice=value))
            request.assert_not_called()

    def test_bad_quantities_rejected_before_network(self):
        with patch.object(self.broker, '_request') as request:
            for value in (True, None, 'nan', 'inf', -1, 0, 1.5):
                with self.subTest(value=value), self.assertRaises(BrokerError):
                    self.broker.submit_order(dict(self.intent, qty=value))
            request.assert_not_called()

    def test_bad_prices_rejected_before_network(self):
        with patch.object(self.broker, '_request') as request:
            for field in ('price', 'trigger_price'):
                for value in ('nan', 'inf', -1, None):
                    with self.subTest(field=field, value=value), self.assertRaises(BrokerError):
                        self.broker.submit_order(dict(self.intent, **{field: value}))
            request.assert_not_called()

    def test_tag_never_silently_truncated(self):
        with patch.object(self.broker, '_request') as request:
            for key in ('x' * 41, '', ' ', None, 1):
                with self.subTest(key=key), self.assertRaises(BrokerError):
                    self.broker.submit_order(dict(self.intent, idempotency_key=key))
            request.assert_not_called()

    def test_place_contract(self):
        with patch.object(self.broker, '_request', return_value={'status': 'success', 'data': {'order_ids': ['123']}}) as request:
            result = self.broker.submit_order(self.intent)
            self.assertEqual(result['broker_order_id'], '123')
            method, base, path, payload = request.call_args.args
            self.assertEqual((method, base, path), ('POST', self.broker.trade_base, '/order/place'))
            self.assertIs(payload['slice'], False)
            self.assertEqual(payload['tag'], 'unique-1')
            self.assertEqual(payload['market_protection'], -1)

    def test_bad_acknowledgements_fail(self):
        values = [None, {}, {'status': 'error', 'data': {'order_ids': ['1']}},
                  {'status': 'success', 'data': []}]
        values += [{'status': 'success', 'data': {'order_ids': ids}}
                   for ids in ([], [None], [''], [True], ['1', '2'])]
        for response in values:
            with self.subTest(response=response), patch.object(self.broker, '_request', return_value=response), self.assertRaises(BrokerError):
                self.broker.submit_order(self.intent)

    def test_snapshot_errors_are_not_empty_accounts(self):
        for method in (self.broker.orders, self.broker.positions):
            for response in (None, {}, {'status': 'error', 'data': []}, {'status': 'success', 'data': {}}, {'status': 'success', 'data': [None]}):
                with self.subTest(method=method.__name__, response=response), patch.object(self.broker, '_request', return_value=response), self.assertRaises(BrokerError):
                    method()

    def test_empty_successful_snapshot(self):
        with patch.object(self.broker, '_request', return_value={'status': 'success', 'data': []}):
            self.assertEqual(self.broker.orders(), [])
            self.assertEqual(self.broker.positions(), [])

    def test_order_without_identity_is_not_dropped(self):
        with patch.object(self.broker, '_request', return_value={'status': 'success', 'data': [{}]}), self.assertRaises(BrokerError):
            self.broker.orders()

    def test_cancel_error_not_reported_as_success(self):
        with patch.object(self.broker, '_request', return_value={'status': 'error'}), self.assertRaises(BrokerError):
            self.broker.cancel_order('123')

    def test_cancel_contract(self):
        with patch.object(self.broker, '_request', return_value={'status': 'success', 'data': {'order_id': '123'}}) as request:
            self.assertTrue(self.broker.cancel_order('123')['cancel_requested'])
            request.assert_called_once_with('DELETE', self.broker.trade_base, '/order/cancel?order_id=123')
