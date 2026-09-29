from app.schemas import Hypothesis


KUBERNETES_SCENARIO_ROOT_PRIORITY: dict[str, tuple[str, ...]] = {
    "oomkilled": (
        "container-memory-limit",
        "node-memory-pressure",
        "container-oom",
        "missing-memory-limit",
    ),
    "crashloop": (
        "oom-restart",
        "probe-failure",
        "application-exit",
        "container-crash-loop",
        "recent-change-regression",
    ),
    "failed-scheduling": (
        "karpenter-capacity",
        "volume-binding",
        "taint-toleration-mismatch",
        "affinity-mismatch",
        "insufficient-resources",
    ),
    "hpa": (
        "hpa-max-saturation",
        "hpa-metrics-unavailable",
        "hpa-scale-blocked",
    ),
    "keda": (
        "keda-scaler-error",
        "keda-paused",
        "keda-fallback",
        "keda-trigger-inactive",
    ),
    "node-not-ready": (
        "node-unreachable",
        "node-kubelet-unhealthy",
        "node-resource-pressure",
        "karpenter-node-disruption",
    ),
}


def kubernetes_root_priority(scenario: str) -> tuple[str, ...]:
    return KUBERNETES_SCENARIO_ROOT_PRIORITY.get(scenario, ())


def kubernetes_hypothesis_priority(hypothesis_id: str) -> int:
    """Return the classic Kubernetes playbook specificity rank.

    Eviction intentionally has no fixed priority here because its classic path
    selects among confirmed candidates by score. Unknown IDs therefore receive
    the fallback rank and preserve score-based selection.
    """
    ranks = [
        priority.index(hypothesis_id)
        for priority in KUBERNETES_SCENARIO_ROOT_PRIORITY.values()
        if hypothesis_id in priority
    ]
    return min(ranks) if ranks else 1000


def pod_eviction_hypotheses() -> list[Hypothesis]:
    return [
        Hypothesis(
            id="app-crash",
            statement="The application crashed independently of node resource pressure.",
            required_checks=["pod terminal reason", "restart count", "application logs"],
        ),
        Hypothesis(
            id="memory-pressure",
            statement="The pod was evicted because the node was under memory pressure.",
            required_checks=["Node MemoryPressure condition", "pod OOMKilled state"],
        ),
        Hypothesis(
            id="pid-pressure",
            statement="The pod was evicted because the node exhausted process IDs.",
            required_checks=["Node PIDPressure condition"],
        ),
        Hypothesis(
            id="disk-pressure",
            statement="Node ephemeral-storage exhaustion triggered DiskPressure and pod eviction.",
            required_checks=["Node DiskPressure", "eviction message", "filesystem availability trend"],
        ),
        Hypothesis(
            id="missing-storage-reservations",
            statement="Missing ephemeral-storage requests/limits allowed storage overcommit and contributed to the eviction.",
            required_checks=["container resource requests", "container resource limits"],
        ),
        Hypothesis(
            id="recent-change-regression",
            statement="A recent deployment or source change caused the incident.",
            required_checks=["Argo CD sync time", "Git revision", "incident start time", "direct failure signal"],
        ),
        Hypothesis(
            id="node-capacity-churn",
            statement="Karpenter node lifecycle or capacity churn materially contributed to the incident.",
            required_checks=["NodeClaim state", "NodePool", "Karpenter conditions", "node lifecycle events"],
        ),
    ]


def pod_oom_hypotheses() -> list[Hypothesis]:
    return [
        Hypothesis(
            id="container-oom",
            statement="A container was terminated by the OOM killer.",
            required_checks=["container terminated reason", "last container state", "restart count"],
        ),
        Hypothesis(
            id="container-memory-limit",
            statement="The workload exhausted its configured container memory limit.",
            required_checks=["memory limit", "memory working set", "OOMKilled state"],
        ),
        Hypothesis(
            id="node-memory-pressure",
            statement="Node-wide memory pressure materially contributed to the OOM event.",
            required_checks=["Node MemoryPressure condition", "node memory availability"],
        ),
        Hypothesis(
            id="missing-memory-limit",
            statement="Missing memory limits increased the workload's exposure to node-level OOM selection.",
            required_checks=["container memory limits", "QoS-relevant resource configuration"],
        ),
        Hypothesis(
            id="recent-change-regression",
            statement="A recent deployment or source change introduced the memory regression.",
            required_checks=["Argo CD sync time", "Git revision", "incident start time"],
        ),
    ]


def crashloop_hypotheses() -> list[Hypothesis]:
    return [
        Hypothesis(
            id="container-crash-loop",
            statement="The container is repeatedly failing and Kubernetes restart backoff is active.",
            required_checks=["waiting reason", "restart count", "last terminated state"],
        ),
        Hypothesis(
            id="oom-restart",
            statement="The crash loop is caused by repeated OOMKilled terminations.",
            required_checks=["last terminated reason", "memory limits", "node memory pressure"],
        ),
        Hypothesis(
            id="probe-failure",
            statement="Liveness or startup probe failures are repeatedly restarting the container.",
            required_checks=["Unhealthy events", "probe failure messages"],
        ),
        Hypothesis(
            id="application-exit",
            statement="The application exits with a non-zero status independently of probes or OOM.",
            required_checks=["last exit code", "termination reason", "application logs"],
        ),
        Hypothesis(
            id="recent-change-regression",
            statement="A recent deployment or source change introduced the crash loop.",
            required_checks=["Argo CD sync time", "Git revision", "incident start time"],
        ),
    ]


def failed_scheduling_hypotheses() -> list[Hypothesis]:
    return [
        Hypothesis(
            id="insufficient-resources",
            statement="The scheduler cannot place the pod because candidate nodes lack requested CPU, memory, or pod capacity.",
            required_checks=["FailedScheduling event", "scheduler message"],
        ),
        Hypothesis(
            id="taint-toleration-mismatch",
            statement="Node taints reject the pod because required tolerations are missing.",
            required_checks=["FailedScheduling event", "untolerated taint message"],
        ),
        Hypothesis(
            id="affinity-mismatch",
            statement="Node selector, affinity, or topology constraints exclude all candidate nodes.",
            required_checks=["FailedScheduling event", "affinity or selector message"],
        ),
        Hypothesis(
            id="volume-binding",
            statement="Persistent volume binding or attachment constraints prevent scheduling.",
            required_checks=["FailedScheduling event", "PVC or volume message"],
        ),
        Hypothesis(
            id="karpenter-capacity",
            statement="Karpenter or the cloud provider could not provision compatible capacity for the pending pod.",
            required_checks=["Karpenter events", "InsufficientCapacity or FailedProvisioning signal"],
        ),
    ]


def hpa_hypotheses() -> list[Hypothesis]:
    return [
        Hypothesis(
            id="hpa-max-saturation",
            statement="The HPA is capped at maxReplicas while demand still requires additional replicas.",
            required_checks=["ScalingLimited condition", "current replicas", "desired replicas", "maxReplicas"],
        ),
        Hypothesis(
            id="hpa-metrics-unavailable",
            statement="The HPA cannot calculate a desired replica count because required metrics are unavailable.",
            required_checks=["ScalingActive condition", "HPA condition reason"],
        ),
        Hypothesis(
            id="hpa-scale-blocked",
            statement="The HPA cannot apply a new scale due to scale-target or controller limitations.",
            required_checks=["AbleToScale condition", "HPA condition reason"],
        ),
    ]


def keda_hypotheses() -> list[Hypothesis]:
    return [
        Hypothesis(
            id="keda-scaler-error",
            statement="KEDA cannot evaluate one or more triggers or update the ScaledObject successfully.",
            required_checks=["ScaledObject Ready condition", "KEDA warning events"],
        ),
        Hypothesis(
            id="keda-paused",
            statement="Autoscaling is explicitly paused for the KEDA ScaledObject.",
            required_checks=["paused annotations", "ScaledObjectPaused event"],
        ),
        Hypothesis(
            id="keda-fallback",
            statement="KEDA fallback mode is active because the primary scaler source is failing.",
            required_checks=["fallback configuration", "Fallback condition or event"],
        ),
        Hypothesis(
            id="keda-trigger-inactive",
            statement="KEDA reports no active trigger demand for the target workload.",
            required_checks=["ScaledObject Active condition", "trigger state"],
        ),
    ]


def node_not_ready_hypotheses() -> list[Hypothesis]:
    return [
        Hypothesis(
            id="node-unreachable",
            statement="The control plane has lost contact with the node and Ready is Unknown.",
            required_checks=["Node Ready condition", "unreachable taint", "heartbeat timestamps"],
        ),
        Hypothesis(
            id="node-kubelet-unhealthy",
            statement="The node is reachable but kubelet reports Ready=False.",
            required_checks=["Node Ready condition", "condition reason and message"],
        ),
        Hypothesis(
            id="node-resource-pressure",
            statement="Disk, memory, or PID pressure contributed to the node becoming unhealthy.",
            required_checks=["DiskPressure", "MemoryPressure", "PIDPressure"],
        ),
        Hypothesis(
            id="karpenter-node-disruption",
            statement="Karpenter lifecycle or disruption activity contributed to node unavailability.",
            required_checks=["NodeClaim Ready condition", "Karpenter disruption events"],
        ),
    ]


def hypotheses_for_scenario(scenario: str) -> list[Hypothesis]:
    factories = {
        "eviction": pod_eviction_hypotheses,
        "oomkilled": pod_oom_hypotheses,
        "crashloop": crashloop_hypotheses,
        "failed-scheduling": failed_scheduling_hypotheses,
        "hpa": hpa_hypotheses,
        "keda": keda_hypotheses,
        "node-not-ready": node_not_ready_hypotheses,
    }
    factory = factories.get(scenario)
    return factory() if factory else []
