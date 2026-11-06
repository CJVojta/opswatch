import tempfile
import unittest
from pathlib import Path

from app import seed_demo, validate_monitor
from monitor import Scheduler
from store import Store


class MonitorTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "test.sqlite3"
        self.store = Store(self.path)
        self.now = 1_800_000_000

    def check(self, monitor_id, offset, ok):
        self.store.record(monitor_id, self.now + offset, ok, 200 if ok else 503, 42, "probe")

    def test_incident_lifecycle_and_persistence(self):
        monitor = self.store.create("Portal", "https://portal.example", 60, 5)
        self.check(monitor, 0, True)
        self.check(monitor, 1, False)
        self.assertEqual(self.store.overview(self.now)["incidents"], [])
        self.check(monitor, 2, True)
        self.check(monitor, 3, False)
        self.check(monitor, 4, False)
        self.check(monitor, 5, False)
        self.check(monitor, 6, True)
        data = self.store.overview(self.now)
        self.assertEqual(data["monitors"][0]["state"], "down")
        self.assertEqual(len(data["incidents"]), 1)
        self.assertIsNone(data["incidents"][0]["resolved_at"])
        self.check(monitor, 7, True)
        data = Store(self.path).overview(self.now + 7)
        self.assertEqual(data["monitors"][0]["state"], "up")
        self.assertEqual(data["monitors"][0]["uptime"], 50.0)
        self.assertEqual(data["incidents"][0]["opened_at"], self.now + 4)
        self.assertEqual(data["incidents"][0]["resolved_at"], self.now + 7)

    def test_deleted_monitor_does_not_receive_inflight_result(self):
        previous = self.store.create("Old", "https://old.example", 60, 5)
        self.assertTrue(self.store.delete(previous))
        replacement = self.store.create("New", "https://new.example", 60, 5)
        self.check(previous, 0, False)
        self.assertNotEqual(previous, replacement)
        self.assertEqual(self.store.history(replacement, 10), [])
        self.assertIsNone(self.store.history(previous, 10))

    def test_scheduler_saves_probe_result_and_reschedules(self):
        monitor = self.store.create("Portal", "https://portal.example", 60, 5)
        calls = []

        def probe(url, timeout):
            calls.append((url, timeout))
            return True, 200, 18, "HTTP 200"

        scheduler = Scheduler(self.store, check=probe)
        scheduler.run_check(self.store.due(self.now)[0])
        check = self.store.history(monitor, 1)[0]
        self.assertEqual(calls, [("https://portal.example", 5)])
        self.assertEqual(check["latency_ms"], 18)
        self.assertEqual(self.store.due(check["checked_at"] + 59), [])
        self.assertEqual(self.store.due(check["checked_at"] + 60)[0]["id"], monitor)

    def test_demo_includes_open_and_resolved_incidents(self):
        seed_demo(self.store, self.now)
        data = self.store.overview(self.now)
        self.assertEqual(sum(row["samples"] for row in data["monitors"]), 384)
        self.assertEqual(len(data["incidents"]), 3)
        self.assertIsNone(data["incidents"][0]["resolved_at"])
        self.assertTrue(all(row["resolved_at"] for row in data["incidents"][1:]))

    def test_pruning_keeps_incident_and_recent_checks(self):
        monitor = self.store.create("Portal", "https://portal.example", 60, 5)
        self.check(monitor, -31 * 86400, False)
        self.check(monitor, -31 * 86400 + 1, False)
        self.check(monitor, -20, True)
        self.store.prune(self.now)
        self.assertEqual(len(self.store.history(monitor, 10)), 1)
        self.assertEqual(len(self.store.overview(self.now)["incidents"]), 1)

    def test_configuration_validation(self):
        valid = {"name": " Portal ", "url": "https://portal.example/health"}
        self.assertEqual(validate_monitor(valid), ("Portal", valid["url"], 60, 5))
        for override in [{"url": "file:///x"}, {"url": "https://user@portal.example"},
                         {"url": "https://portal.example:99999"}, {"interval": 14},
                         {"timeout": True}, {"name": " "}]:
            with self.subTest(override=override), self.assertRaises(ValueError):
                validate_monitor({**valid, **override})
