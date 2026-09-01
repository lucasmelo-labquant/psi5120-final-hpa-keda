import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from experiment.analyze import bootstrap_ratio, main as analyze_main
from experiment.run_once import integrate
import numpy as np


class ExperimentTest(unittest.TestCase):
    def test_integrate_uses_left_hand_samples(self):
        samples = [
            {"timestamp": "2026-08-31T12:00:00+00:00", "pods": 1},
            {"timestamp": "2026-08-31T12:00:05+00:00", "pods": 3},
            {"timestamp": "2026-08-31T12:00:10+00:00", "pods": 2},
        ]
        value = integrate(
            samples,
            "pods",
            "2026-08-31T12:00:00+00:00",
            "2026-08-31T12:00:10+00:00",
        )
        self.assertEqual(value, 20.0)

    def test_integrate_includes_partial_boundary_intervals(self):
        samples = [
            {"timestamp": "2026-08-31T11:59:58+00:00", "pods": 1},
            {"timestamp": "2026-08-31T12:00:03+00:00", "pods": 3},
            {"timestamp": "2026-08-31T12:00:08+00:00", "pods": 2},
        ]
        value = integrate(
            samples,
            "pods",
            "2026-08-31T12:00:00+00:00",
            "2026-08-31T12:00:10+00:00",
        )
        self.assertEqual(value, 22.0)

    def test_bootstrap_ratio_is_exact_for_constant_values(self):
        estimate, low, high = bootstrap_ratio(
            [0.8, 0.8, 0.8], np.random.default_rng(5120), iterations=100
        )
        self.assertAlmostEqual(estimate, 0.8)
        self.assertAlmostEqual(low, 0.8)
        self.assertAlmostEqual(high, 0.8)

    def test_analyzer_builds_pairs_and_stopping_decision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            output = root / "analysis"
            for pattern in ["poisson", "mmpp"]:
                for seed in range(1, 6):
                    for position, policy in enumerate(["hpa", "keda"], start=1):
                        run = results / f"{pattern}-{seed}-{policy}"
                        run.mkdir(parents=True)
                        factor = 0.8 if policy == "keda" else 1.0
                        summary = {
                            "valid": True,
                            "campaign_phase": "main",
                            "pattern": pattern,
                            "seed": seed,
                            "policy": policy,
                            "sequence_position": position,
                            "latency_ms_p95": 1000 * factor,
                            "ready_pod_seconds": 300 * factor,
                            "max_backlog": 20 * factor,
                            "drain_after_last_send_s": 30 * factor,
                        }
                        (run / "summary.json").write_text(
                            json.dumps(summary), encoding="utf-8"
                        )
            argv = [
                "analyze.py",
                "--results",
                str(results),
                "--output",
                str(output),
                "--bootstrap-iterations",
                "100",
            ]
            with patch.object(sys, "argv", argv):
                analyze_main()
            decision = json.loads(
                (output / "stopping_decision.json").read_text(encoding="utf-8")
            )
            self.assertFalse(decision["five_additional_seeds_required"])
            self.assertEqual(decision["pairs_per_pattern"]["poisson"], 5)


if __name__ == "__main__":
    unittest.main()
