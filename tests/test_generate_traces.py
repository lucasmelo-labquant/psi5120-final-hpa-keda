import argparse
import math
import random
import unittest

from experiment.generate_traces import generate, mmpp_arrivals, poisson_arrivals


class GenerateTracesTest(unittest.TestCase):
    def setUp(self):
        self.args = argparse.Namespace(
            duration=180.0,
            mean_rate=1.6,
            service_rate=5.0,
            low_rate=0.4,
            high_rate=4.0,
            low_to_high=0.05,
            high_to_low=0.10,
            max_service_seconds=1.0,
            min_service_seconds=0.005,
            count_tolerance=0.10,
        )

    def test_generation_is_deterministic(self):
        self.assertEqual(
            generate(self.args, "mmpp", 101), generate(self.args, "mmpp", 101)
        )

    def test_jobs_are_ordered_and_bounded(self):
        trace = generate(self.args, "poisson", 202)
        offsets = [job["trace_offset_s"] for job in trace["jobs"]]
        self.assertEqual(offsets, sorted(offsets))
        self.assertTrue(all(0 < offset <= self.args.duration for offset in offsets))
        self.assertTrue(
            all(
                self.args.min_service_seconds
                <= job["service_cpu_seconds"]
                <= self.args.max_service_seconds
                for job in trace["jobs"]
            )
        )

    def test_stationary_mmpp_mean_matches_poisson_rate(self):
        high_probability = self.args.low_to_high / (
            self.args.low_to_high + self.args.high_to_low
        )
        mean = (1 - high_probability) * self.args.low_rate + (
            high_probability * self.args.high_rate
        )
        self.assertTrue(math.isclose(mean, self.args.mean_rate))

    def test_mmpp_empirical_count_is_conditioned(self):
        trace = generate(self.args, "mmpp", 303)
        expected = self.args.mean_rate * self.args.duration
        self.assertLessEqual(
            abs(trace["job_count"] - expected) / expected,
            self.args.count_tolerance,
        )
        self.assertGreater(trace["generation_attempt"], 1)

    def test_arrival_generators_stop_at_duration(self):
        poisson = list(poisson_arrivals(random.Random(1), 2.0, 10.0))
        mmpp = list(mmpp_arrivals(random.Random(1), 0.5, 5.0, 0.05, 0.1, 10.0))
        self.assertTrue(all(offset <= 10.0 for offset, _ in poisson + mmpp))


if __name__ == "__main__":
    unittest.main()
