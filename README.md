# PSI5120 Final Project: HPA versus KEDA

Reproducible comparison of CPU-based Kubernetes HPA and SQS backlog-based KEDA
on Amazon EKS under Poisson and Markov-modulated Poisson process workloads.

The project is self-contained. It includes the worker, trace generator,
experiment runner, Kubernetes manifests, AWS infrastructure, analysis scripts,
automated tests, and the IEEE paper source. The repository retains raw main-run
data, generated figures, and measured paired estimates.

## Research question

How do CPU-based HPA and SQS-based KEDA differ in scaling responsiveness,
message latency, backlog control, and pod consumption when workloads have the
same mean arrival rate but different burstiness?

## Scope

- Amazon EKS with one managed worker node;
- one SQS input queue and one SQS result queue;
- identical worker image and resource limits for both policies;
- homogeneous Poisson and two-state MMPP arrivals;
- paired traces with reproducible seeds;
- HPA and KEDA both constrained to 1-4 replicas;
- no node autoscaling, load balancer, NAT Gateway, or poison messages.

## Safety

AWS creation scripts require `ALLOW_AWS_CHARGES=YES`. Cleanup requires
`ALLOW_AWS_DELETE=YES`. The initial cost ceiling is USD 20, while the expected
cost is below USD 2 for a continuous experimental session.

Detailed methodology is documented in `PROTOCOL.md`.

## Layout

- `worker/`: SQS worker and container image;
- `experiment/`: deterministic traces, runner, monitoring, and analysis;
- `manifests/`: Deployment, HPA, and KEDA resources;
- `aws/`: EKS and CloudFormation definitions;
- `scripts/`: guarded lifecycle and campaign automation;
- `tests/`: local unit tests.
- `paper/`: IEEE paper source and verified bibliography.
- `paper/paper.pdf`: compiled 10-page IEEE paper.
- `results/main-v2/`: raw observations from 35 valid main runs.
- `results/analysis/`: paired estimates, figures, and campaign status.
- `traces/`: frozen arrival and service-demand traces.

## Local validation

From the project root:

```bash
python3 -m venv .venv-linux
.venv-linux/bin/pip install -r experiment/requirements.txt
.venv-linux/bin/python -m unittest discover -s tests -v
bash -n scripts/*.sh
```

## AWS lifecycle

Run the following commands from `TF/` in a Linux shell. The preflight is
read-only. Creation commands stop unless the cost flag is explicit.

```bash
bash scripts/01-install-tools.sh
export PATH="$HOME/.local/bin:$PATH"
aws login --remote --region us-east-1
bash scripts/02-configure-login-profile.sh
bash scripts/00-preflight.sh
ALLOW_AWS_CHARGES=YES bash scripts/10-create-foundation.sh
ALLOW_AWS_CHARGES=YES bash scripts/11-create-cluster.sh
ALLOW_AWS_CHARGES=YES bash scripts/12-install-platform.sh
bash scripts/14-run-campaign.sh
ALLOW_AWS_DELETE=YES bash scripts/90-cleanup.sh
```

The initial campaign uses seeds `101,202,303,404,505`. If the stopping rule in
`PROTOCOL.md` requires five additional pairs, execute:

```bash
SEEDS=606,707,808,909,1010 bash scripts/14-run-campaign.sh
```

The runner preserves invalid attempts, purges both queues, and retries a trace
once. `scripts/91-audit-cleanup.sh` performs read-only checks after deletion.
The campaign loads short-lived SDK credentials into its process environment
before each run; it does not print or persist access keys in the repository.
