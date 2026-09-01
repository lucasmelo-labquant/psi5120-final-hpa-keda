import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


PRIMARY = ["latency_ms_p95", "ready_pod_seconds"]


def bootstrap_ratio(values, rng, iterations=10000):
    values = np.asarray(values, dtype=float)
    estimates = np.empty(iterations)
    for index in range(iterations):
        sample = rng.choice(values, size=len(values), replace=True)
        estimates[index] = np.exp(np.mean(np.log(sample)))
    return (
        float(np.exp(np.mean(np.log(values)))),
        float(np.percentile(estimates, 2.5)),
        float(np.percentile(estimates, 97.5)),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bootstrap-iterations", type=int, default=10000)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    rows = []
    for summary_path in args.results.glob("**/summary.json"):
        row = json.loads(summary_path.read_text(encoding="utf-8"))
        if row.get("campaign_phase") != "main":
            continue
        row["path"] = str(summary_path.parent)
        rows.append(row)
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise SystemExit("no summaries found")
    frame.to_csv(args.output / "all_runs.csv", index=False)
    valid = frame[frame["valid"] == True].copy()  # noqa: E712
    valid.to_csv(args.output / "valid_runs.csv", index=False)
    descriptive_metrics = [
        "latency_ms_p95",
        "ready_pod_seconds",
        "max_backlog",
        "scale_out_ready_s",
        "peak_ready_replicas",
    ]
    available_descriptive_metrics = [
        metric for metric in descriptive_metrics if metric in valid.columns
    ]
    descriptive = (
        valid.groupby(["pattern", "policy"])[available_descriptive_metrics]
        .agg(["count", "mean", "median"])
        .reset_index()
    )
    descriptive.columns = [
        "_".join(value for value in column if value)
        if isinstance(column, tuple)
        else column
        for column in descriptive.columns
    ]
    descriptive.to_csv(args.output / "descriptive_summary.csv", index=False)
    duplicates = valid.groupby(["pattern", "seed", "policy"]).size()
    if (duplicates > 1).any():
        raise SystemExit("multiple valid attempts found for the same treatment cell")

    metrics = PRIMARY + ["max_backlog", "drain_after_last_send_s"]

    paired = valid.pivot(
        index=["pattern", "seed"],
        columns="policy",
        values=metrics,
    )
    paired.columns = [f"{metric}_{policy}" for metric, policy in paired.columns]
    paired = paired.dropna().reset_index()
    if paired.empty:
        paired.to_csv(args.output / "paired_runs.csv", index=False)
        raise SystemExit("no complete valid HPA/KEDA pairs found")
    for metric in metrics:
        paired[f"{metric}_ratio_keda_hpa"] = (
            paired[f"{metric}_keda"] / paired[f"{metric}_hpa"]
        )
    paired.to_csv(args.output / "paired_runs.csv", index=False)

    rng = np.random.default_rng(5120)
    estimates = []
    for pattern in sorted(paired["pattern"].unique()):
        subset = paired[paired["pattern"] == pattern]
        for metric in PRIMARY:
            estimate, low, high = bootstrap_ratio(
                subset[f"{metric}_ratio_keda_hpa"],
                rng,
                iterations=args.bootstrap_iterations,
            )
            estimates.append(
                {
                    "pattern": pattern,
                    "metric": metric,
                    "pairs": len(subset),
                    "geometric_mean_ratio": estimate,
                    "median_ratio": float(
                        subset[f"{metric}_ratio_keda_hpa"].median()
                    ),
                    "ci95_low": low,
                    "ci95_high": high,
                    "precision_within_10_percent": low >= 0.9 * estimate
                    and high <= 1.1 * estimate,
                }
            )
    estimates_frame = pd.DataFrame(estimates)
    estimates_frame.to_csv(args.output / "paired_estimates.csv", index=False)

    order_rows = []
    order = valid.pivot(
        index=["pattern", "seed"], columns="policy", values="sequence_position"
    ).dropna()
    for _, row in paired.iterrows():
        keda_first = order.loc[(row["pattern"], row["seed"]), "keda"] == 1
        for metric in PRIMARY:
            order_rows.append(
                {
                    "pattern": row["pattern"],
                    "seed": row["seed"],
                    "metric": metric,
                    "keda_first": bool(keda_first),
                    "ratio_keda_hpa": row[f"{metric}_ratio_keda_hpa"],
                }
            )
    pd.DataFrame(order_rows).to_csv(
        args.output / "treatment_order_sensitivity.csv", index=False
    )
    order_summary = (
        pd.DataFrame(order_rows)
        .groupby(["pattern", "metric", "keda_first"])["ratio_keda_hpa"]
        .agg(["count", "median", "mean"])
        .reset_index()
    )
    order_summary.to_csv(args.output / "treatment_order_summary.csv", index=False)

    decision = {
        "invalid_attempts_present": bool((frame["valid"] == False).any()),  # noqa: E712
        "pairs_per_pattern": {
            pattern: int((paired["pattern"] == pattern).sum())
            for pattern in ["poisson", "mmpp"]
        },
        "all_primary_intervals_within_10_percent": bool(
            estimates_frame["precision_within_10_percent"].all()
        ),
    }
    decision["five_additional_seeds_required"] = (
        min(decision["pairs_per_pattern"].values()) < 5
        or (
            min(decision["pairs_per_pattern"].values()) < 10
            and (
                decision["invalid_attempts_present"]
                or not decision["all_primary_intervals_within_10_percent"]
            )
        )
    )
    (args.output / "stopping_decision.json").write_text(
        json.dumps(decision, indent=2), encoding="utf-8"
    )

    for metric, label in [
        ("latency_ms_p95", "End-to-end latency p95 (ms)"),
        ("ready_pod_seconds", "Ready pod-seconds"),
        ("max_backlog", "Maximum backlog (messages)"),
        ("drain_after_last_send_s", "Drain time after last send (s)"),
    ]:
        fig, axes = plt.subplots(1, 2, figsize=(8.5, 3.4), sharey=True)
        for axis, pattern in zip(axes, ["poisson", "mmpp"]):
            subset = paired[paired["pattern"] == pattern]
            for _, row in subset.iterrows():
                axis.plot(
                    ["HPA", "KEDA"],
                    [row[f"{metric}_hpa"], row[f"{metric}_keda"]],
                    marker="o",
                    alpha=0.75,
                )
            axis.set_title(pattern.upper())
            axis.grid(axis="y", alpha=0.25)
        axes[0].set_ylabel(label)
        fig.tight_layout()
        fig.savefig(args.output / f"paired_{metric}.pdf", bbox_inches="tight")
        fig.savefig(args.output / f"paired_{metric}.png", dpi=180, bbox_inches="tight")
        plt.close(fig)

    print(f"analyzed {len(frame)} runs, {len(valid)} valid, {len(paired)} pairs")


if __name__ == "__main__":
    main()
