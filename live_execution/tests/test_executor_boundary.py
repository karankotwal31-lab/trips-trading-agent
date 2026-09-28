import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from live_execution.executor import execute
from live_execution.journal import ExecutionJournal, JournalError
from live_execution.kill_switch import KillSwitch
from live_execution.gates import LiveGateError
from live_execution.readiness import REQUIRED_CHECKS


class ExecutorBoundaryTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {'TRIPS_LIVE_EXECUTION': 'ENABLED'})
        env.start(); self.addCleanup(env.stop)
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'orders.sqlite'
        self.intent = dict(side='BUY', qty=1, reference_price=50, idempotency_key='1', data_fresh=True, approved_core=True)
        self.broker = Mock()
        self.broker.submit_order.return_value = {'broker_order_id': 'order-1'}
        self.kw = dict(daily_pnl=0, open_positions=0, readiness_checks={k: True for k in REQUIRED_CHECKS},
                       kill_switch=KillSwitch(), journal=ExecutionJournal(self.path))

    def test_missing_readiness_blocks_network(self):
        for evidence in (None, {}, dict(self.kw['readiness_checks'], reconciliation_clean=False)):
            with self.subTest(evidence=evidence), self.assertRaises(LiveGateError):
                execute(self.intent, self.broker, **dict(self.kw, readiness_checks=evidence))
        self.broker.submit_order.assert_not_called()

    def test_missing_host_controls_blocks_network(self):
        for field in ('kill_switch', 'journal'):
            with self.subTest(field=field), self.assertRaises(LiveGateError):
                execute(self.intent, self.broker, **dict(self.kw, **{field: None}))
        self.broker.submit_order.assert_not_called()

    def test_engaged_kill_switch_blocks_network(self):
        self.kw['kill_switch'].engage('operator halt')
        with self.assertRaises(RuntimeError):
            execute(self.intent, self.broker, **self.kw)
        self.broker.submit_order.assert_not_called()

    def test_duplicate_blocked_after_restart(self):
        execute(self.intent, self.broker, **self.kw)
        self.kw['journal'] = ExecutionJournal(self.path)
        with self.assertRaises(JournalError):
            execute(self.intent, self.broker, **self.kw)
        self.assertEqual(self.broker.submit_order.call_count, 1)

    def test_unknown_acceptance_blocks_new_intents_after_restart(self):
        self.broker.submit_order.side_effect = TimeoutError('uncertain')
        with self.assertRaises(TimeoutError):
            execute(self.intent, self.broker, **self.kw)
        self.assertTrue(self.kw['kill_switch'].state.engaged)
        self.kw.update(journal=ExecutionJournal(self.path), kill_switch=KillSwitch())
        with self.assertRaises(JournalError):
            execute(dict(self.intent, idempotency_key='2'), self.broker, **self.kw)
        self.assertEqual(self.broker.submit_order.call_count, 1)

    def test_crashed_reservation_blocks_new_intents(self):
        self.kw['journal'].reserve(self.intent)
        self.kw['journal'] = ExecutionJournal(self.path)
        with self.assertRaises(JournalError):
            execute(dict(self.intent, idempotency_key='2'), self.broker, **self.kw)
        self.broker.submit_order.assert_not_called()

    def test_invalid_acknowledgement_halts(self):
        self.broker.submit_order.return_value = {'broker_order_id': None}
        with self.assertRaises(RuntimeError):
            execute(self.intent, self.broker, **self.kw)
        self.assertTrue(self.kw['kill_switch'].state.engaged)

    def test_memory_only_journal_rejected(self):
        with self.assertRaises(JournalError):
            ExecutionJournal(':memory:')
