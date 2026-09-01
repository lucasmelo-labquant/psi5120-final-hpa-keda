import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
import numpy as np
import pandas as pd


PRIMARY = ["latency_ms_p95", "ready_pod_seconds"]
SECONDARY = [
    "latency_ms_mean",
    "latency_ms_p50",
    "latency_ms_p99",
    "queue_wait_ms_p50",
    "queue_wait_ms_p95",
    "queue_wait_ms_p99",
    "backlog_message_seconds",
    "max_backlog",
    "scale_out_ready_s",
    "peak_ready_replicas",
]


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


def monitor_origin(frame):
    started = frame[frame["producer_started"] == 1]
    return float(started.iloc[0]["elapsed_s"]) if not started.empty else 0.0


def first_after_origin(frame, origin, column):
    matches = frame[(frame["elapsed_s"] >= origin) & (frame[column] > 1)]
    if matches.empty:
        return np.nan
    return float(matches.iloc[0]["elapsed_s"] - origin)


def replica_occupancy(frame, origin, duration=180.0):
    samples = frame[["elapsed_s", "ready_replicas"]].copy()
    samples["elapsed_s"] -= origin
    samples = samples[(samples["elapsed_s"] >= 0) & (samples["elapsed_s"] <= duration)]
    if samples.empty:
        return {replicas: np.nan for replicas in range(1, 5)}
    times = samples["elapsed_s"].to_numpy(dtype=float)
    replicas = samples["ready_replicas"].to_numpy(dtype=int)
    ends = np.append(times[1:], duration)
    intervals = np.maximum(ends - times, 0)
    observed = intervals.sum()
    if observed <= 0:
        return {replica: np.nan for replica in range(1, 5)}
    return {
        replica: float(intervals[replicas == replica].sum() / observed)
        for replica in range(1, 5)
    }


def max_arrivals_in_window(offsets, window):
    left = 0
    maximum = 0
    for right, value in enumerate(offsets):
        while value - offsets[left] > window:
            left += 1
        maximum = max(maximum, right - left + 1)
    return maximum


def plot_architecture(output):
    fig, axis = plt.subplots(figsize=(10.5, 5.3))
    axis.set_xlim(0, 10.5)
    axis.set_ylim(0, 5.3)
    axis.axis("off")

    boxes = {
        "producer": (0.4, 2.1, 1.5, 0.8, "Trace\nreplayer"),
        "input": (2.4, 2.1, 1.5, 0.8, "SQS input\nqueue"),
        "worker": (4.7, 2.1, 1.6, 0.8, "Worker\nDeployment"),
        "result": (7.1, 2.1, 1.5, 0.8, "SQS result\nqueue"),
        "collector": (9.0, 2.1, 1.2, 0.8, "Result\ncollector"),
        "metrics": (4.5, 4.0, 2.0, 0.7, "Metrics Server / CPU"),
        "hpa": (7.2, 4.0, 1.4, 0.7, "HPA"),
        "keda": (2.2, 0.4, 1.8, 0.7, "KEDA SQS scaler"),
        "external": (4.7, 0.4, 1.8, 0.7, "External metric HPA"),
        "monitor": (7.5, 0.4, 1.8, 0.7, "Experiment monitor"),
    }
    colors = {
        "producer": "#dbeafe",
        "input": "#fde68a",
        "worker": "#dcfce7",
        "result": "#fde68a",
        "collector": "#dbeafe",
        "metrics": "#fee2e2",
        "hpa": "#fee2e2",
        "keda": "#ede9fe",
        "external": "#ede9fe",
        "monitor": "#e5e7eb",
    }
    for key, (x, y, width, height, label) in boxes.items():
        axis.add_patch(
            FancyBboxPatch(
                (x, y), width, height, boxstyle="round,pad=0.08",
                facecolor=colors[key], edgecolor="#334155", linewidth=1.2
            )
        )
        axis.text(x + width / 2, y + height / 2, label, ha="center", va="center")

    def arrow(start, end, color="#334155", style="-"):
        axis.add_patch(
            FancyArrowPatch(
                start, end, arrowstyle="-|>", mutation_scale=12,
                linewidth=1.3, color=color, linestyle=style
            )
        )

    arrow((1.9, 2.5), (2.4, 2.5))
    arrow((3.9, 2.5), (4.7, 2.5))
    arrow((6.3, 2.5), (7.1, 2.5))
    arrow((8.6, 2.5), (9.0, 2.5))
    arrow((5.5, 2.9), (5.5, 4.0), "#b91c1c")
    arrow((6.5, 4.35), (7.2, 4.35), "#b91c1c")
    arrow((7.9, 4.0), (6.1, 2.9), "#b91c1c")
    arrow((3.1, 2.1), (3.1, 1.1), "#6d28d9")
    arrow((4.0, 0.75), (4.7, 0.75), "#6d28d9")
    arrow((5.6, 1.1), (5.6, 2.1), "#6d28d9")
    arrow((7.5, 0.75), (6.5, 2.1), "#475569", "--")
    arrow((8.2, 1.1), (8.0, 2.1), "#475569", "--")
    axis.text(5.25, 5.0, "Control plane", ha="center", weight="bold")
    axis.text(5.25, 3.25, "Data plane", ha="center", weight="bold")
    axis.text(5.25, 0.05, "External control and measurement", ha="center", weight="bold")
    fig.tight_layout()
    fig.savefig(output / "architecture.png", dpi=200, bbox_inches="tight")
    plt.close(fig)


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

    secondary_estimates = []
    secondary_metrics = [metric for metric in SECONDARY if metric in valid.columns]
    secondary_paired = valid.pivot(
        index=["pattern", "seed"], columns="policy", values=secondary_metrics
    )
    secondary_paired.columns = [
        f"{metric}_{policy}" for metric, policy in secondary_paired.columns
    ]
    secondary_paired = secondary_paired.dropna().reset_index()
    for pattern in sorted(secondary_paired["pattern"].unique()):
        subset = secondary_paired[secondary_paired["pattern"] == pattern]
        for metric in secondary_metrics:
            ratios = subset[f"{metric}_keda"] / subset[f"{metric}_hpa"]
            estimate, low, high = bootstrap_ratio(
                ratios, rng, iterations=args.bootstrap_iterations
            )
            secondary_estimates.append(
                {
                    "pattern": pattern,
                    "metric": metric,
                    "pairs": len(ratios),
                    "geometric_mean_ratio": estimate,
                    "median_ratio": float(ratios.median()),
                    "ci95_low": low,
                    "ci95_high": high,
                    "keda_lower_count": int((ratios < 1).sum()),
                    "ties": int((ratios == 1).sum()),
                }
            )
    pd.DataFrame(secondary_estimates).to_csv(
        args.output / "secondary_estimates.csv", index=False
    )

    common = paired.pivot(index="seed", columns="pattern")
    interaction_rows = []
    for metric in PRIMARY + ["max_backlog"]:
        poisson_column = (f"{metric}_ratio_keda_hpa", "poisson")
        mmpp_column = (f"{metric}_ratio_keda_hpa", "mmpp")
        if poisson_column not in common.columns or mmpp_column not in common.columns:
            continue
        values = (common[mmpp_column] / common[poisson_column]).dropna()
        estimate, low, high = bootstrap_ratio(
            values, rng, iterations=args.bootstrap_iterations
        )
        interaction_rows.append(
            {
                "metric": metric,
                "common_seeds": len(values),
                "geometric_mean_ratio_of_ratios": estimate,
                "median_ratio_of_ratios": float(values.median()),
                "ci95_low": low,
                "ci95_high": high,
                "minimum": float(values.min()),
                "maximum": float(values.max()),
            }
        )
    pd.DataFrame(interaction_rows).to_csv(
        args.output / "workload_interaction.csv", index=False
    )

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

    timing_rows = []
    occupancy_rows = []
    monitor_frames = {}
    for _, row in valid.iterrows():
        monitor_path = Path(row["path"]) / "monitor.csv"
        if not monitor_path.exists():
            continue
        monitor = pd.read_csv(monitor_path)
        origin = monitor_origin(monitor)
        desired_s = first_after_origin(monitor, origin, "desired_replicas")
        ready_s = first_after_origin(monitor, origin, "ready_replicas")
        timing_rows.append(
            {
                "pattern": row["pattern"],
                "seed": int(row["seed"]),
                "policy": row["policy"],
                "first_desired_above_one_s": desired_s,
                "first_ready_above_one_s": ready_s,
                "desired_to_ready_s": ready_s - desired_s,
            }
        )
        occupancy = replica_occupancy(monitor, origin)
        occupancy_rows.append(
            {
                "pattern": row["pattern"],
                "seed": int(row["seed"]),
                "policy": row["policy"],
                **{f"fraction_at_{key}_ready": value for key, value in occupancy.items()},
            }
        )
        monitor_frames[(row["pattern"], int(row["seed"]), row["policy"])] = (
            monitor,
            origin,
        )
    timing = pd.DataFrame(timing_rows)
    timing.to_csv(args.output / "controller_timing.csv", index=False)
    if not timing.empty:
        timing.groupby(["pattern", "policy"])[
            [
                "first_desired_above_one_s",
                "first_ready_above_one_s",
                "desired_to_ready_s",
            ]
        ].median().reset_index().to_csv(
            args.output / "controller_timing_summary.csv", index=False
        )
    occupancy = pd.DataFrame(occupancy_rows)
    occupancy.to_csv(args.output / "replica_occupancy.csv", index=False)

    if not occupancy.empty:
        occupancy_summary = occupancy.groupby(["pattern", "policy"])[
            [f"fraction_at_{replica}_ready" for replica in range(1, 5)]
        ].mean().reset_index()
        occupancy_summary.to_csv(
            args.output / "replica_occupancy_summary.csv", index=False
        )
        labels = [
            f"{pattern.upper()}\n{policy.upper()}"
            for pattern, policy in occupancy_summary[["pattern", "policy"]].itertuples(index=False)
        ]
        fig, axis = plt.subplots(figsize=(8.2, 4.2))
        bottom = np.zeros(len(occupancy_summary))
        for replica in range(1, 5):
            values = occupancy_summary[f"fraction_at_{replica}_ready"].to_numpy()
            axis.bar(labels, values, bottom=bottom, label=f"{replica} ready")
            bottom += values
        axis.set_ylabel("Mean fraction of 180-s arrival window")
        axis.set_ylim(0, 1)
        axis.legend(ncol=4, loc="upper center", bbox_to_anchor=(0.5, 1.16))
        axis.grid(axis="y", alpha=0.2)
        fig.tight_layout()
        fig.savefig(args.output / "replica_occupancy.png", dpi=200, bbox_inches="tight")
        plt.close(fig)

    representative = {
        policy: monitor_frames.get(("mmpp", 707, policy))
        for policy in ["hpa", "keda"]
    }
    if all(value is not None for value in representative.values()):
        fig, axes = plt.subplots(2, 1, figsize=(9.2, 6.3), sharex=True)
        for axis, policy in zip(axes, ["hpa", "keda"]):
            monitor, origin = representative[policy]
            elapsed = monitor["elapsed_s"] - origin
            backlog = monitor["input_visible"] + monitor["input_inflight"]
            axis.step(elapsed, backlog, where="post", color="#b45309", label="SQS backlog")
            axis.set_ylabel("Messages")
            axis.set_title(f"MMPP seed 707: {policy.upper()}", loc="left")
            axis.grid(alpha=0.2)
            replica_axis = axis.twinx()
            replica_axis.step(
                elapsed, monitor["desired_replicas"], where="post",
                color="#2563eb", linestyle="--", label="Desired replicas"
            )
            replica_axis.step(
                elapsed, monitor["ready_replicas"], where="post",
                color="#15803d", label="Ready replicas"
            )
            replica_axis.set_ylabel("Replicas")
            replica_axis.set_ylim(0.8, 4.2)
            lines = axis.get_lines() + replica_axis.get_lines()
            axis.legend(lines, [line.get_label() for line in lines], loc="upper right")
            axis.axvline(180, color="#475569", linewidth=1, linestyle=":")
        axes[-1].set_xlabel("Seconds from workload start")
        axes[-1].set_xlim(0, 210)
        fig.tight_layout()
        fig.savefig(args.output / "mmpp_seed707_timeline.png", dpi=200, bbox_inches="tight")
        plt.close(fig)

    fig, axes = plt.subplots(2, 1, figsize=(8.8, 6.4), sharex=True)
    for axis, metric, label in [
        (axes[0], "latency_ms_p95", "p95 latency ratio"),
        (axes[1], "ready_pod_seconds", "Ready pod-seconds ratio"),
    ]:
        for pattern, marker in [("poisson", "o"), ("mmpp", "s")]:
            subset = pd.DataFrame(order_rows)
            subset = subset[(subset["pattern"] == pattern) & (subset["metric"] == metric)]
            colors = np.where(subset["keda_first"], "#7c3aed", "#ea580c")
            axis.scatter(subset["seed"], subset["ratio_keda_hpa"], c=colors, marker=marker,
                         label=pattern.upper(), s=55)
        axis.axhline(1, color="#475569", linestyle="--", linewidth=1)
        axis.set_ylabel(label)
        axis.grid(alpha=0.2)
    axes[0].set_yscale("log")
    axes[0].legend(ncol=2)
    axes[1].set_xlabel("Seed (campaign progression)")
    fig.text(0.99, 0.02, "Purple: KEDA first; orange: HPA first", ha="right", fontsize=9)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(args.output / "order_sensitivity.png", dpi=200, bbox_inches="tight")
    plt.close(fig)

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

    trace_directory = args.results.parent / "traces"
    trace_rows = []
    traces = {}
    if trace_directory.exists():
        for trace_path in sorted(trace_directory.glob("*.json")):
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
            offsets = np.asarray(
                [job["trace_offset_s"] for job in trace["jobs"]], dtype=float
            )
            inter_arrivals = np.diff(np.insert(offsets, 0, 0.0))
            high_fraction = (
                sum(job["arrival_state"] == "high" for job in trace["jobs"])
                / len(trace["jobs"])
                if trace["pattern"] == "mmpp"
                else np.nan
            )
            row = {
                "pattern": trace["pattern"],
                "seed": int(trace["seed"]),
                "jobs": int(trace["job_count"]),
                "empirical_rate": float(trace["empirical_rate"]),
                "inter_arrival_cv": float(np.std(inter_arrivals) / np.mean(inter_arrivals)),
                "high_state_arrival_fraction": high_fraction,
            }
            for window in [1, 5, 15, 30]:
                row[f"max_arrivals_{window}s"] = max_arrivals_in_window(offsets, window)
            trace_rows.append(row)
            traces[(trace["pattern"], int(trace["seed"]))] = offsets
    trace_frame = pd.DataFrame(trace_rows)
    trace_frame.to_csv(args.output / "trace_characteristics.csv", index=False)
    if not trace_frame.empty:
        trace_frame.groupby("pattern").agg(
            traces=("seed", "nunique"),
            jobs_min=("jobs", "min"),
            jobs_max=("jobs", "max"),
            mean_rate=("empirical_rate", "mean"),
            median_inter_arrival_cv=("inter_arrival_cv", "median"),
            median_max_arrivals_1s=("max_arrivals_1s", "median"),
            median_max_arrivals_5s=("max_arrivals_5s", "median"),
            median_max_arrivals_15s=("max_arrivals_15s", "median"),
            median_max_arrivals_30s=("max_arrivals_30s", "median"),
            median_high_state_arrival_fraction=("high_state_arrival_fraction", "median"),
        ).reset_index().to_csv(args.output / "trace_summary.csv", index=False)
        if ("poisson", 707) in traces and ("mmpp", 707) in traces:
            fig, axes = plt.subplots(2, 1, figsize=(9.0, 5.7), sharex=True)
            for axis, pattern, color in [
                (axes[0], "poisson", "#2563eb"),
                (axes[1], "mmpp", "#b45309"),
            ]:
                offsets = traces[(pattern, 707)]
                axis.step(offsets, np.arange(1, len(offsets) + 1), where="post", color=color)
                axis.set_ylabel("Cumulative arrivals")
                axis.set_title(f"{pattern.upper()} seed 707", loc="left")
                axis.grid(alpha=0.2)
            axes[-1].set_xlabel("Trace offset (s)")
            axes[-1].set_xlim(0, 180)
            fig.tight_layout()
            fig.savefig(args.output / "trace_comparison.png", dpi=200, bbox_inches="tight")
            plt.close(fig)

    plot_architecture(args.output)

    print(f"analyzed {len(frame)} runs, {len(valid)} valid, {len(paired)} pairs")


if __name__ == "__main__":
    main()
