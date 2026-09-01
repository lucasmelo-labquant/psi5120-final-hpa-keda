import argparse
import copy
import csv
import json
import math
import statistics
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import numpy as np
from botocore.config import Config


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def percentile(values, q):
    return float(np.percentile(np.asarray(values, dtype=float), q))


def kubectl_json(*args):
    result = subprocess.run(
        ["kubectl", *args, "-o", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def kubectl_optional_json(*args):
    result = subprocess.run(
        ["kubectl", *args, "-o", "json"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)


def policy_snapshot(args, require_healthy=True):
    deployment = kubectl_json(
        "get", "deployment", args.deployment, "-n", args.namespace
    )
    study_hpa = kubectl_optional_json(
        "get", "hpa", "sqs-worker-hpa", "-n", args.namespace
    )
    scaled_object = kubectl_optional_json(
        "get", "scaledobject", "sqs-worker-keda", "-n", args.namespace
    )
    keda_hpa = kubectl_optional_json(
        "get", "hpa", "keda-hpa-sqs-worker-keda", "-n", args.namespace
    )
    if args.policy == "hpa":
        if study_hpa is None or scaled_object is not None or keda_hpa is not None:
            raise RuntimeError("HPA policy is not the only active autoscaler")
        if require_healthy:
            conditions = {
                item["type"]: item["status"]
                for item in study_hpa.get("status", {}).get("conditions", [])
            }
            if conditions.get("AbleToScale") != "True" or conditions.get(
                "ScalingActive"
            ) != "True":
                raise RuntimeError(f"HPA is not healthy: {conditions}")
        autoscaler_spec = study_hpa["spec"]
    else:
        if scaled_object is None or study_hpa is not None or keda_hpa is None:
            raise RuntimeError("KEDA policy is not the only active autoscaler")
        if require_healthy:
            conditions = {
                item["type"]: item["status"]
                for item in scaled_object.get("status", {}).get("conditions", [])
            }
            if conditions.get("Ready") != "True":
                raise RuntimeError(f"ScaledObject is not healthy: {conditions}")
        autoscaler_spec = scaled_object["spec"]
    deployment_spec = copy.deepcopy(deployment["spec"])
    deployment_spec.pop("replicas", None)
    return {
        "deployment_spec": deployment_spec,
        "autoscaler_spec": autoscaler_spec,
        "worker_image": deployment["spec"]["template"]["spec"]["containers"][0][
            "image"
        ],
    }


def queue_counts(sqs, url):
    response = sqs.get_queue_attributes(
        QueueUrl=url,
        AttributeNames=[
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
        ],
    )["Attributes"]
    return int(response["ApproximateNumberOfMessages"]), int(
        response["ApproximateNumberOfMessagesNotVisible"]
    )


def ensure_empty(sqs, input_url, result_url):
    deadline = time.monotonic() + 90
    consecutive_empty = 0
    input_counts = (-1, -1)
    result_counts = (-1, -1)
    while time.monotonic() < deadline:
        input_counts = queue_counts(sqs, input_url)
        result_counts = queue_counts(sqs, result_url)
        if input_counts == (0, 0) and result_counts == (0, 0):
            consecutive_empty += 1
            if consecutive_empty == 3:
                return
            time.sleep(2)
            continue
        consecutive_empty = 0
        time.sleep(2)
    raise RuntimeError(
        f"queues are not empty: input={input_counts}, result={result_counts}"
    )


def monitor_loop(args, sqs, started, stop, samples, errors):
    origin = time.monotonic()
    all_nodes_ready = True
    next_node_check = 0.0
    while not stop.is_set():
        sample_start = time.monotonic()
        try:
            workloads = kubectl_json(
                "get",
                "deployments,pods",
                "-n",
                args.namespace,
                "-l",
                f"app={args.deployment}",
            )
            deployment = next(
                item for item in workloads["items"] if item["kind"] == "Deployment"
            )
            pods = [item for item in workloads["items"] if item["kind"] == "Pod"]
            if time.monotonic() >= next_node_check:
                nodes = kubectl_json("get", "nodes")
                all_nodes_ready = bool(nodes["items"]) and all(
                    any(
                        condition.get("type") == "Ready"
                        and condition.get("status") == "True"
                        for condition in node.get("status", {}).get("conditions", [])
                    )
                    for node in nodes["items"]
                )
                next_node_check = time.monotonic() + 30
            input_visible, input_inflight = queue_counts(sqs, args.input_queue_url)
            result_visible, result_inflight = queue_counts(sqs, args.result_queue_url)
            ready_pods = 0
            pending_pods = 0
            unschedulable_pods = 0
            restart_count = 0
            for pod in pods:
                phase = pod.get("status", {}).get("phase")
                if phase == "Pending":
                    pending_pods += 1
                conditions = pod.get("status", {}).get("conditions", [])
                if any(
                    condition.get("type") == "PodScheduled"
                    and condition.get("status") == "False"
                    and condition.get("reason") == "Unschedulable"
                    for condition in conditions
                ):
                    unschedulable_pods += 1
                if any(
                    condition.get("type") == "Ready"
                    and condition.get("status") == "True"
                    for condition in conditions
                ):
                    ready_pods += 1
                restart_count += sum(
                    status.get("restartCount", 0)
                    for status in pod.get("status", {}).get("containerStatuses", [])
                )
            status = deployment.get("status", {})
            samples.append(
                {
                    "timestamp": utc_now(),
                    "elapsed_s": time.monotonic() - origin,
                    "producer_started": int(started.is_set()),
                    "desired_replicas": status.get("replicas", 0),
                    "ready_replicas": ready_pods,
                    "available_replicas": status.get("availableReplicas", 0),
                    "pending_pods": pending_pods,
                    "unschedulable_pods": unschedulable_pods,
                    "pod_restarts": restart_count,
                    "all_nodes_ready": int(all_nodes_ready),
                    "input_visible": input_visible,
                    "input_inflight": input_inflight,
                    "result_visible": result_visible,
                    "result_inflight": result_inflight,
                }
            )
        except Exception as exc:
            errors.append(f"{utc_now()} {type(exc).__name__}: {exc}")
        remaining = args.sample_interval - (time.monotonic() - sample_start)
        stop.wait(max(0.1, remaining))


def collector_loop(
    args,
    sqs,
    experiment_id,
    expected,
    trace_duration,
    records,
    duplicates,
    errors,
    stop,
):
    deadline = time.monotonic() + trace_duration + args.drain_timeout + 30
    seen = set()
    quiet_since = None
    while time.monotonic() < deadline:
        if (
            quiet_since is not None
            and time.monotonic() - quiet_since >= args.result_quiet_seconds
        ):
            break
        wait_seconds = 10
        if quiet_since is not None:
            remaining = args.result_quiet_seconds - (time.monotonic() - quiet_since)
            wait_seconds = max(1, min(10, math.ceil(remaining)))
        try:
            response = sqs.receive_message(
                QueueUrl=args.result_queue_url,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=wait_seconds,
            )
        except Exception as exc:
            errors.append(f"{utc_now()} {type(exc).__name__}: {exc}")
            time.sleep(1)
            continue
        if response.get("Messages", []) and quiet_since is not None:
            quiet_since = time.monotonic()
        for message in response.get("Messages", []):
            try:
                payload = json.loads(message["Body"])
                if payload.get("experiment_id") != experiment_id:
                    payload["collector_note"] = "unexpected_experiment_id"
                    duplicates.append(payload)
                elif payload["job_id"] in seen:
                    payload["collector_note"] = "duplicate"
                    duplicates.append(payload)
                else:
                    seen.add(payload["job_id"])
                    records.append(payload)
                sqs.delete_message(
                    QueueUrl=args.result_queue_url,
                    ReceiptHandle=message["ReceiptHandle"],
                )
            except Exception as exc:
                errors.append(f"{utc_now()} {type(exc).__name__}: {exc}")
        if len(seen) >= expected and quiet_since is None:
            quiet_since = time.monotonic()
    stop.set()


def integrate(samples, field, start_iso, end_iso):
    start = parse_time(start_iso)
    end = parse_time(end_iso)
    ordered = sorted(samples, key=lambda sample: parse_time(sample["timestamp"]))
    total = 0.0
    current_value = None
    cursor = start
    for sample in ordered:
        sample_time = parse_time(sample["timestamp"])
        if sample_time <= start:
            current_value = float(sample[field])
            continue
        if sample_time >= end:
            break
        if current_value is not None:
            total += current_value * (sample_time - cursor).total_seconds()
        current_value = float(sample[field])
        cursor = sample_time
    if current_value is not None and cursor < end:
        total += current_value * (end - cursor).total_seconds()
    return total


def longest_positive_duration(samples, field, start_iso, end_iso):
    start = parse_time(start_iso)
    end = parse_time(end_iso)
    active_since = None
    longest = 0.0
    for sample in sorted(samples, key=lambda item: parse_time(item["timestamp"])):
        sample_time = parse_time(sample["timestamp"])
        if sample_time < start:
            continue
        if sample_time > end:
            break
        if sample[field] > 0 and active_since is None:
            active_since = sample_time
        elif sample[field] == 0 and active_since is not None:
            longest = max(longest, (sample_time - active_since).total_seconds())
            active_since = None
    if active_since is not None:
        longest = max(longest, (end - active_since).total_seconds())
    return longest


def write_csv(path, rows):
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def send_job(sqs, queue_url, experiment_id, job, target):
    send_started = time.monotonic()
    sent_at = utc_now()
    payload = {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "job_id": job["job_id"],
        "trace_offset_s": job["trace_offset_s"],
        "producer_sent_at": sent_at,
        "service_cpu_seconds": job["service_cpu_seconds"],
        "work_seed": job["work_seed"],
    }
    response = sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps(payload))
    return {
        "job_id": job["job_id"],
        "trace_offset_s": job["trace_offset_s"],
        "producer_sent_at": sent_at,
        "schedule_lag_s": send_started - target,
        "api_seconds": time.monotonic() - send_started,
        "sqs_message_id": response["MessageId"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--policy", choices=["hpa", "keda"], required=True)
    parser.add_argument(
        "--campaign-phase", choices=["pilot", "main"], required=True
    )
    parser.add_argument("--sequence-position", type=int, choices=[1, 2], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--input-queue-url", required=True)
    parser.add_argument("--result-queue-url", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--aws-profile")
    parser.add_argument("--namespace", default="scaling-study")
    parser.add_argument("--deployment", default="sqs-worker")
    parser.add_argument("--sample-interval", type=float, default=5.0)
    parser.add_argument("--drain-timeout", type=float, default=300.0)
    parser.add_argument("--scale-down-timeout", type=float, default=120.0)
    parser.add_argument("--result-quiet-seconds", type=float, default=15.0)
    parser.add_argument("--producer-workers", type=int, default=64)
    args = parser.parse_args()
    if args.region != "us-east-1":
        parser.error("this frozen experiment only supports us-east-1")

    trace = json.loads(args.trace.read_text(encoding="utf-8"))
    experiment_id = (
        f"{args.policy}-{trace['pattern']}-{trace['seed']}-"
        f"{uuid.uuid4().hex[:8]}"
    )
    aws_session = boto3.Session(profile_name=args.aws_profile)
    sqs = aws_session.client(
        "sqs",
        region_name=args.region,
        config=Config(
            retries={"max_attempts": 8, "mode": "standard"},
            max_pool_connections=128,
            connect_timeout=5,
            read_timeout=25,
        ),
    )
    ensure_empty(sqs, args.input_queue_url, args.result_queue_url)

    deployment = kubectl_json(
        "get", "deployment", args.deployment, "-n", args.namespace
    )
    if deployment.get("status", {}).get("readyReplicas") != 1:
        raise RuntimeError("exactly one worker replica must be Ready before the run")
    initial_policy_snapshot = policy_snapshot(args)
    args.output.mkdir(parents=True, exist_ok=False)

    samples = []
    monitor_errors = []
    records = []
    duplicates = []
    collector_errors = []
    producer_rows = []
    monitor_started = threading.Event()
    monitor_stop = threading.Event()
    collector_stop = threading.Event()
    monitor = threading.Thread(
        target=monitor_loop,
        args=(args, sqs, monitor_started, monitor_stop, samples, monitor_errors),
        daemon=True,
    )
    collector = threading.Thread(
        target=collector_loop,
        args=(
            args,
            sqs,
            experiment_id,
            trace["job_count"],
            trace["duration_s"],
            records,
            duplicates,
            collector_errors,
            collector_stop,
        ),
        daemon=True,
    )
    monitor.start()
    collector.start()
    first_sample_deadline = time.monotonic() + 30
    while not samples and time.monotonic() < first_sample_deadline:
        time.sleep(0.25)
    if not samples:
        raise RuntimeError(f"monitor produced no initial sample: {monitor_errors}")

    run_start_mono = time.monotonic() + 1.0
    run_start_iso = (
        datetime.now(timezone.utc) + timedelta(seconds=1)
    ).isoformat(timespec="milliseconds")
    monitor_started.set()
    futures = []
    with ThreadPoolExecutor(max_workers=args.producer_workers) as executor:
        for job in trace["jobs"]:
            target = run_start_mono + float(job["trace_offset_s"])
            remaining = target - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            futures.append(
                executor.submit(
                    send_job,
                    sqs,
                    args.input_queue_url,
                    experiment_id,
                    job,
                    target,
                )
            )
        for future in as_completed(futures):
            producer_rows.append(future.result())
    producer_rows.sort(key=lambda row: row["trace_offset_s"])
    last_send_iso = utc_now()

    collector.join(args.drain_timeout + 45)
    results_complete_iso = utc_now()
    input_counts = queue_counts(sqs, args.input_queue_url)
    result_counts = queue_counts(sqs, args.result_queue_url)

    scale_down_deadline = time.monotonic() + args.scale_down_timeout
    while time.monotonic() < scale_down_deadline:
        deployment = kubectl_json(
            "get", "deployment", args.deployment, "-n", args.namespace
        )
        if deployment.get("status", {}).get("readyReplicas", 0) <= 1:
            break
        time.sleep(5)
    scale_down_complete_iso = utc_now()
    monitor_stop.set()
    monitor.join(15)
    final_policy_snapshot = policy_snapshot(args, require_healthy=False)

    for record in records:
        sent = parse_time(record["sqs_sent_at"])
        received = parse_time(record["worker_received_at"])
        finished = parse_time(record["processing_finished_at"])
        record["queue_wait_ms"] = (received - sent).total_seconds() * 1000
        record["end_to_end_ms"] = (finished - sent).total_seconds() * 1000

    latencies = [record["end_to_end_ms"] for record in records]
    waits = [record["queue_wait_ms"] for record in records]
    lags = [row["schedule_lag_s"] * 1000 for row in producer_rows]
    workload_complete_iso = None
    if records:
        workload_complete_iso = max(
            records, key=lambda record: parse_time(record["processing_finished_at"])
        )["processing_finished_at"]
    last_input_accepted_iso = None
    if records:
        last_input_accepted_iso = max(
            records, key=lambda record: parse_time(record["sqs_sent_at"])
        )["sqs_sent_at"]
    measurement_end_iso = workload_complete_iso or results_complete_iso
    restart_baseline = max(
        (
            sample["pod_restarts"]
            for sample in samples
            if parse_time(sample["timestamp"]) <= parse_time(run_start_iso)
        ),
        default=0,
    )
    ready_scale_samples = [
        sample
        for sample in samples
        if sample["ready_replicas"] > 1
        and parse_time(sample["timestamp"]) >= parse_time(run_start_iso)
    ]
    scale_out_s = None
    if ready_scale_samples:
        scale_out_s = (
            parse_time(ready_scale_samples[0]["timestamp"]) - parse_time(run_start_iso)
        ).total_seconds()

    summary = {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "policy": args.policy,
        "campaign_phase": args.campaign_phase,
        "sequence_position": args.sequence_position,
        "pattern": trace["pattern"],
        "seed": trace["seed"],
        "trace_jobs": trace["job_count"],
        "unique_results": len(records),
        "duplicates": len(duplicates),
        "unexpected_results": sum(
            item.get("collector_note") == "unexpected_experiment_id"
            for item in duplicates
        ),
        "missing_results": trace["job_count"] - len(records),
        "run_start": run_start_iso,
        "last_send": last_send_iso,
        "last_input_accepted": last_input_accepted_iso,
        "workload_complete": workload_complete_iso,
        "results_collected": results_complete_iso,
        "scale_down_complete": scale_down_complete_iso,
        "scale_out_ready_s": scale_out_s,
        "drain_after_last_send_s": (
            parse_time(workload_complete_iso) - parse_time(last_input_accepted_iso)
        ).total_seconds()
        if workload_complete_iso and last_input_accepted_iso
        else None,
        "collection_after_workload_s": (
            parse_time(results_complete_iso) - parse_time(workload_complete_iso)
        ).total_seconds()
        if workload_complete_iso
        else None,
        "latency_ms_mean": statistics.fmean(latencies) if latencies else None,
        "latency_ms_p50": percentile(latencies, 50) if latencies else None,
        "latency_ms_p95": percentile(latencies, 95) if latencies else None,
        "latency_ms_p99": percentile(latencies, 99) if latencies else None,
        "queue_wait_ms_p50": percentile(waits, 50) if waits else None,
        "queue_wait_ms_p95": percentile(waits, 95) if waits else None,
        "queue_wait_ms_p99": percentile(waits, 99) if waits else None,
        "minimum_queue_wait_ms": min(waits) if waits else None,
        "minimum_end_to_end_ms": min(latencies) if latencies else None,
        "producer_lag_ms_p95": percentile(lags, 95) if lags else None,
        "producer_api_ms_p95": percentile(
            [row["api_seconds"] * 1000 for row in producer_rows], 95
        )
        if producer_rows
        else None,
        "peak_ready_replicas": max(
            (sample["ready_replicas"] for sample in samples), default=0
        ),
        "max_backlog": max(
            (
                sample["input_visible"] + sample["input_inflight"]
                for sample in samples
            ),
            default=0,
        ),
        "ready_pod_seconds": integrate(
            samples,
            "ready_replicas",
            run_start_iso,
            workload_complete_iso or results_complete_iso,
        ),
        "backlog_message_seconds": integrate(
            [
                {
                    **sample,
                    "backlog": sample["input_visible"] + sample["input_inflight"],
                }
                for sample in samples
            ],
            "backlog",
            run_start_iso,
            workload_complete_iso or results_complete_iso,
        ),
        "input_visible_after_run": input_counts[0],
        "input_inflight_after_run": input_counts[1],
        "result_visible_after_run": result_counts[0],
        "result_inflight_after_run": result_counts[1],
        "monitor_errors": monitor_errors,
        "collector_errors": collector_errors,
        "worker_image": initial_policy_snapshot["worker_image"],
        "configuration_unchanged": initial_policy_snapshot == final_policy_snapshot,
        "max_pending_pods": max(
            (sample["pending_pods"] for sample in samples), default=0
        ),
        "max_pod_restarts": max(
            (sample["pod_restarts"] for sample in samples), default=0
        ),
        "pod_restart_increase": max(
            0,
            max((sample["pod_restarts"] for sample in samples), default=0)
            - restart_baseline,
        ),
        "max_unschedulable_seconds": longest_positive_duration(
            samples,
            "unschedulable_pods",
            run_start_iso,
            measurement_end_iso,
        ),
        "all_nodes_remained_ready": bool(samples)
        and all(sample["all_nodes_ready"] for sample in samples),
        "max_monitor_gap_s": max(
            (
                (
                    parse_time(following["timestamp"])
                    - parse_time(current["timestamp"])
                ).total_seconds()
                for current, following in zip(samples, samples[1:])
            ),
            default=math.inf,
        ),
    }
    summary["valid"] = all(
        [
            summary["missing_results"] == 0,
            summary["unexpected_results"] == 0,
            summary["producer_lag_ms_p95"] is not None
            and summary["producer_lag_ms_p95"] <= 250,
            summary["minimum_queue_wait_ms"] is not None
            and summary["minimum_queue_wait_ms"] >= -100,
            summary["minimum_end_to_end_ms"] is not None
            and summary["minimum_end_to_end_ms"] >= -100,
            summary["max_monitor_gap_s"] <= 15,
            summary["all_nodes_remained_ready"],
            not collector.is_alive(),
            not collector_errors,
            summary["max_unschedulable_seconds"] < 15,
            summary["pod_restart_increase"] == 0,
            summary["configuration_unchanged"],
        ]
    )

    write_csv(args.output / "producer.csv", producer_rows)
    write_csv(args.output / "monitor.csv", samples)
    write_csv(args.output / "results.csv", records)
    (args.output / "duplicates.json").write_text(
        json.dumps(duplicates, indent=2), encoding="utf-8"
    )
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    if not summary["valid"]:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
