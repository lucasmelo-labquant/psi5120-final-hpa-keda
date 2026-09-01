import unittest

from worker.worker import cpu_work, validate


class WorkerTest(unittest.TestCase):
    def payload(self):
        return {
            "schema_version": 1,
            "experiment_id": "test",
            "job_id": "000001",
            "trace_offset_s": 1.0,
            "producer_sent_at": "2026-08-31T12:00:00+00:00",
            "service_cpu_seconds": 0.01,
            "work_seed": "seed",
        }

    def test_validate_accepts_protocol_message(self):
        payload = self.payload()
        self.assertIs(validate(payload), payload)

    def test_validate_rejects_missing_field(self):
        payload = self.payload()
        del payload["job_id"]
        with self.assertRaisesRegex(ValueError, "missing fields"):
            validate(payload)

    def test_validate_rejects_boolean_service_time(self):
        payload = self.payload()
        payload["service_cpu_seconds"] = True
        with self.assertRaisesRegex(ValueError, "must be numeric"):
            validate(payload)

    def test_cpu_work_returns_sha256_digest(self):
        digest = cpu_work(0.005, "seed")
        self.assertEqual(len(digest), 64)
        int(digest, 16)


if __name__ == "__main__":
    unittest.main()
