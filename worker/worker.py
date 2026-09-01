import hashlib
import json
import logging
import os
import signal
import socket
import time
from datetime import datetime, timezone

import boto3
from botocore.config import Config


logging.basicConfig(level=logging.INFO, format="%(message)s")
LOG = logging.getLogger("worker")
STOP = False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def request_stop(signum, frame):
    del signum, frame
    global STOP
    STOP = True


def cpu_work(cpu_seconds: float, seed: str) -> str:
    if not 0.005 <= cpu_seconds <= 5.0:
        raise ValueError("service_cpu_seconds must be between 0.005 and 5.0")
    digest = seed.encode("utf-8")
    deadline = time.process_time() + cpu_seconds
    while time.process_time() < deadline:
        digest = hashlib.sha256(digest).digest()
    return digest.hex()


def validate(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("message body must be a JSON object")
    required = {
        "schema_version",
        "experiment_id",
        "job_id",
        "trace_offset_s",
        "producer_sent_at",
        "service_cpu_seconds",
        "work_seed",
    }
    missing = sorted(required - payload.keys())
    if missing:
        raise ValueError(f"missing fields: {missing}")
    if payload["schema_version"] != 1:
        raise ValueError("unsupported schema_version")
    if not isinstance(payload["experiment_id"], str) or not payload["experiment_id"]:
        raise ValueError("invalid experiment_id")
    if not isinstance(payload["job_id"], str) or not payload["job_id"]:
        raise ValueError("invalid job_id")
    if isinstance(payload["service_cpu_seconds"], bool) or not isinstance(
        payload["service_cpu_seconds"], (int, float)
    ):
        raise ValueError("service_cpu_seconds must be numeric")
    return payload


def main() -> None:
    region = os.environ.get("AWS_REGION", "us-east-1")
    input_url = os.environ["INPUT_QUEUE_URL"]
    result_url = os.environ["RESULT_QUEUE_URL"]
    wait_seconds = int(os.environ.get("WAIT_TIME_SECONDS", "20"))
    visibility_timeout = int(os.environ.get("VISIBILITY_TIMEOUT_SECONDS", "120"))
    hostname = socket.gethostname()
    node_name = os.environ.get("NODE_NAME", "unknown")
    sqs = boto3.client(
        "sqs",
        region_name=region,
        config=Config(retries={"max_attempts": 8, "mode": "standard"}),
    )

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    LOG.info(json.dumps({"event": "worker_started", "pod": hostname, "node": node_name}))

    while not STOP:
        response = sqs.receive_message(
            QueueUrl=input_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=wait_seconds,
            VisibilityTimeout=visibility_timeout,
            AttributeNames=["ApproximateReceiveCount", "SentTimestamp"],
        )
        for message in response.get("Messages", []):
            received_at = utc_now()
            wall_start = time.perf_counter()
            try:
                payload = validate(json.loads(message["Body"]))
                sqs_sent_at = datetime.fromtimestamp(
                    int(message["Attributes"]["SentTimestamp"]) / 1000,
                    tz=timezone.utc,
                ).isoformat(timespec="milliseconds")
                process_started_at = utc_now()
                digest = cpu_work(
                    float(payload["service_cpu_seconds"]), payload["work_seed"]
                )
                process_finished_at = utc_now()
                result = {
                    "schema_version": 1,
                    "experiment_id": payload["experiment_id"],
                    "job_id": payload["job_id"],
                    "trace_offset_s": payload["trace_offset_s"],
                    "producer_sent_at": payload["producer_sent_at"],
                    "sqs_sent_at": sqs_sent_at,
                    "worker_received_at": received_at,
                    "processing_started_at": process_started_at,
                    "processing_finished_at": process_finished_at,
                    "result_sent_at": utc_now(),
                    "service_cpu_seconds": payload["service_cpu_seconds"],
                    "processing_wall_seconds": time.perf_counter() - wall_start,
                    "pod_name": hostname,
                    "node_name": node_name,
                    "digest": digest,
                    "receive_count": int(
                        message.get("Attributes", {}).get("ApproximateReceiveCount", "1")
                    ),
                }
                sqs.send_message(QueueUrl=result_url, MessageBody=json.dumps(result))
                sqs.delete_message(
                    QueueUrl=input_url, ReceiptHandle=message["ReceiptHandle"]
                )
                LOG.info(
                    json.dumps(
                        {
                            "event": "job_completed",
                            "experiment_id": payload["experiment_id"],
                            "job_id": payload["job_id"],
                            "pod": hostname,
                            "wall_seconds": result["processing_wall_seconds"],
                        }
                    )
                )
            except Exception as exc:
                LOG.exception(
                    json.dumps(
                        {
                            "event": "job_failed",
                            "pod": hostname,
                            "error": type(exc).__name__,
                        }
                    )
                )

    LOG.info(json.dumps({"event": "worker_stopped", "pod": hostname}))


if __name__ == "__main__":
    main()
