import argparse
import json
import math
import random
from pathlib import Path


def poisson_arrivals(rng: random.Random, rate: float, duration: float):
    current = 0.0
    while True:
        current += rng.expovariate(rate)
        if current > duration:
            break
        yield current, "poisson"


def mmpp_arrivals(
    rng: random.Random,
    low_rate: float,
    high_rate: float,
    low_to_high: float,
    high_to_low: float,
    duration: float,
):
    high_probability = low_to_high / (low_to_high + high_to_low)
    state = "high" if rng.random() < high_probability else "low"
    current = 0.0
    while current < duration:
        transition_rate = high_to_low if state == "high" else low_to_high
        state_end = min(duration, current + rng.expovariate(transition_rate))
        arrival_rate = high_rate if state == "high" else low_rate
        arrival = current
        while True:
            arrival += rng.expovariate(arrival_rate)
            if arrival >= state_end:
                break
            yield arrival, state
        current = state_end
        state = "low" if state == "high" else "high"


def generate(args, pattern: str, seed: int):
    service_rng = random.Random(seed + 1_000_000)
    if pattern == "poisson":
        arrival_seed = seed
        generation_attempt = 1
        arrivals = list(
            poisson_arrivals(random.Random(arrival_seed), args.mean_rate, args.duration)
        )
        parameters = {"mean_rate": args.mean_rate}
    else:
        high_probability = args.low_to_high / (args.low_to_high + args.high_to_low)
        stationary_mean = (
            (1 - high_probability) * args.low_rate
            + high_probability * args.high_rate
        )
        if not math.isclose(stationary_mean, args.mean_rate, rel_tol=0.01):
            raise ValueError(
                f"MMPP stationary mean {stationary_mean:.4f} differs from "
                f"requested mean {args.mean_rate:.4f}"
            )
        expected_count = args.mean_rate * args.duration
        arrivals = []
        for generation_attempt in range(1, 101):
            arrival_seed = seed + (generation_attempt - 1) * 10_000_019
            arrivals = list(
                mmpp_arrivals(
                    random.Random(arrival_seed),
                    args.low_rate,
                    args.high_rate,
                    args.low_to_high,
                    args.high_to_low,
                    args.duration,
                )
            )
            relative_difference = abs(len(arrivals) - expected_count) / expected_count
            if relative_difference <= args.count_tolerance:
                break
        else:
            raise RuntimeError("unable to generate an MMPP trace within count tolerance")
        parameters = {
            "mean_rate": stationary_mean,
            "low_rate": args.low_rate,
            "high_rate": args.high_rate,
            "low_to_high": args.low_to_high,
            "high_to_low": args.high_to_low,
        }

    jobs = []
    for index, (offset, state) in enumerate(arrivals):
        service = max(
            args.min_service_seconds,
            min(
                args.max_service_seconds,
                service_rng.expovariate(args.service_rate),
            ),
        )
        jobs.append(
            {
                "job_id": f"{index:06d}",
                "trace_offset_s": round(offset, 6),
                "arrival_state": state,
                "service_cpu_seconds": round(service, 6),
                "work_seed": f"{pattern}-{seed}-{index:06d}",
            }
        )
    return {
        "schema_version": 1,
        "pattern": pattern,
        "seed": seed,
        "arrival_seed": arrival_seed,
        "generation_attempt": generation_attempt,
        "duration_s": args.duration,
        "service_rate": args.service_rate,
        "parameters": parameters,
        "job_count": len(jobs),
        "empirical_rate": len(jobs) / args.duration,
        "jobs": jobs,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", default="101,102,103,104,105")
    parser.add_argument("--duration", type=float, default=180.0)
    parser.add_argument("--mean-rate", type=float, required=True)
    parser.add_argument("--service-rate", type=float, required=True)
    parser.add_argument("--low-rate", type=float, required=True)
    parser.add_argument("--high-rate", type=float, required=True)
    parser.add_argument("--low-to-high", type=float, default=0.05)
    parser.add_argument("--high-to-low", type=float, default=0.10)
    parser.add_argument("--max-service-seconds", type=float, default=1.0)
    parser.add_argument("--min-service-seconds", type=float, default=0.005)
    parser.add_argument("--count-tolerance", type=float, default=0.10)
    args = parser.parse_args()
    if not 0.005 <= args.min_service_seconds <= args.max_service_seconds <= 5.0:
        parser.error("service bounds must satisfy 0.005 <= min <= max <= 5.0")
    if not 0 < args.count_tolerance <= 0.25:
        parser.error("count tolerance must be in (0, 0.25]")
    args.output.mkdir(parents=True, exist_ok=True)
    seeds = [int(value) for value in args.seeds.split(",")]
    for seed in seeds:
        for pattern in ("poisson", "mmpp"):
            trace = generate(args, pattern, seed)
            path = args.output / f"{pattern}_seed_{seed}.json"
            path.write_text(json.dumps(trace, indent=2), encoding="utf-8")
            print(f"{path}: {trace['job_count']} jobs")


if __name__ == "__main__":
    main()
