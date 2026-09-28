import unittest
from live_execution.gates import LiveGateError, LiveLimits, validate_intent


class NumericSafetyTests(unittest.TestCase):
    def setUp(self):
        self.intent = dict(side='BUY', qty=1, reference_price=50, idempotency_key='test', data_fresh=True, approved_core=True)

    def check(self, intent=None, **kwargs):
        return validate_intent(intent or self.intent, **dict(dict(limits=LiveLimits(), daily_pnl=0, open_positions=0), **kwargs))

    def test_valid_intent(self):
        self.check()

    def test_nonfinite_and_invalid_intent_numbers(self):
        for field in ('qty', 'reference_price'):
            for value in (float('nan'), float('inf'), -float('inf'), True, None, 'bad'):
                with self.subTest(field=field, value=value), self.assertRaises(LiveGateError):
                    self.check(dict(self.intent, **{field: value}))

    def test_nonfinite_pnl(self):
        for value in (float('nan'), float('inf'), -float('inf'), True, None):
            with self.subTest(value=value), self.assertRaises(LiveGateError):
                self.check(daily_pnl=value)

    def test_invalid_limits(self):
        for field in ('max_order_notional', 'max_daily_loss'):
            for value in (float('nan'), float('inf'), -1, 0, True, None):
                with self.subTest(field=field, value=value), self.assertRaises(LiveGateError):
                    self.check(limits=LiveLimits(**{field: value}))

    def test_invalid_position_counts(self):
        for value in (-1, True, 0.5, float('nan')):
            with self.subTest(value=value), self.assertRaises(LiveGateError):
                self.check(open_positions=value)
        for value in (0, -1, True, 1.5):
            with self.subTest(limit=value), self.assertRaises(LiveGateError):
                self.check(limits=LiveLimits(max_open_positions=value))

    def test_invalid_key(self):
        for value in ('', ' ', True, 123):
            with self.subTest(value=value), self.assertRaises(LiveGateError):
                self.check(dict(self.intent, idempotency_key=value))
