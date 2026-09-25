from app.schemas import Hypothesis


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
    ]
