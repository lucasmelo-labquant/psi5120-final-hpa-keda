# Experimental Protocol

## Design

The experiment uses a paired 2 x 2 design:

| Factor | Levels |
|---|---|
| Autoscaler | HPA CPU, KEDA SQS |
| Arrival process | Poisson, two-state MMPP |
| Seed | Five initially, ten if precision is insufficient |

Each trace stores all arrival offsets and CPU service demands before execution.
The same trace is replayed once with HPA and once with KEDA. Treatment order is
deterministically shuffled within each seed block.

## Primary outcomes

- per-run end-to-end latency p95;
- ready pod-seconds during arrival and drain.

## Secondary outcomes

- queue waiting p50, p95, and p99;
- maximum and time-integrated backlog;
- time to first additional ready replica;
- drain time after the last arrival;
- peak replicas and replica changes;
- producer scheduling lag;
- duplicates and missing results.

## Fairness controls

- same EKS cluster, node, Kubernetes version, worker image, and queues;
- same 1-4 replica bounds;
- same Deployment resources and SQS client configuration;
- same pre-generated trace for each HPA/KEDA pair;
- one worker replica ready before each run;
- no residual input or result messages;
- image pre-pulled before measured runs;
- producer and collector run outside the measured worker node;
- HPA and KEDA are never active simultaneously.

HPA and KEDA use different demand signals. The study compares complete,
representative policies, not the controller software in isolation.

## Queueing reference

For arrival rate lambda, one-pod service rate mu, and c replicas, the reference
utilization is rho = lambda / (c * mu). The frozen workload has a mean arrival
rate of 1.6 messages/s and an exponential CPU demand with mean 0.2 CPU-s, clipped
to the interval from 0.005 to 1 CPU-s. A Pod is limited to 0.4 CPU, so one
continuously busy replica has an approximate wall-time capacity of 2 messages/s
before overhead. Little's Law, L = lambda W, is checked
only as a consistency reference because autoscaling and MMPP bursts make the
system transient and non-stationary.

## Workloads

Poisson inter-arrival times are exponential. The MMPP alternates between low and
high Poisson rates with exponential state residence times. Parameters enforce
the same stationary mean arrival rate in both workloads. The chain starts in
its stationary distribution.

Each trace lasts 180 seconds. Poisson uses a rate of 1.6 messages/s. MMPP uses
rates of 0.4 and 4 messages/s, with low-to-high and high-to-low transition rates
of 0.05 and 0.10/s. Its stationary high-state probability is one third, giving
the same 1.6 messages/s mean. No parameter changes after the main experiment
starts.

Because a short MMPP realization can deviate substantially from its stationary
mean, trace generation uses deterministic rejection sampling until its total
arrival count is within 10% of the expected count. The base seed, accepted
arrival seed, attempt number, empirical rate, and every offset remain recorded.
This conditioning uses only offered-load counts, never autoscaling outcomes.

## Sequential replication rule

Five paired seeds are executed first. The experiment stops at five only if the
95% bootstrap confidence intervals for both primary metric ratios are within
plus or minus 10% and no run is operationally invalid. Otherwise, five more
paired seeds are executed. All valid pilot-independent main runs are retained.

## Invalid run criteria

- producer cannot sustain the trace and p95 scheduling lag exceeds 250 ms;
- missing monitoring samples for more than 15 consecutive seconds;
- node becomes NotReady or a worker Pod restarts unexpectedly;
- worker Pods remain Pending because of insufficient cluster capacity;
- residual messages from a previous run are detected;
- any experiment configuration changes during a run;
- fewer than 100% of expected unique results arrive before the drain timeout.

SQS approximate counters observed immediately after a run are retained for
diagnosis but do not alone invalidate it. Queue emptiness is enforced before the
next run, after eventual counters have converged; late results or an unexpected
experiment identifier invalidate the reset.

Invalid runs are preserved with their reason and repeated using the same trace.

## Reset

Between runs, the active autoscaler is removed and the Deployment is reset. The
queues must have zero visible and in-flight messages. The next policy is applied,
exactly one replica becomes Ready, the image is already cached, and the system
remains idle for at least 30 seconds before arrivals begin.

The producer dispatches scheduled sends through a bounded thread pool so one
SQS network round trip does not delay later arrivals. Scheduling lag is measured
when each sender starts the API call. SQS service timestamps, rather than local
pre-request timestamps, define accepted arrival time for latency metrics.

## Analysis

Messages are not treated as independent experimental replicates. Statistics use
one aggregate per run. HPA/KEDA ratios are paired by workload and seed. Results
include individual paired points, median ratios, bootstrap confidence intervals,
and sensitivity to treatment order. Claims remain descriptive if sample size or
variance does not support inference.
