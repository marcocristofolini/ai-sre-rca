"""Offline contract checks for optional real Kafka exporter validation."""
import unittest
from unittest.mock import patch

from validation.kafka_live import KafkaSnapshot, assert_signal, parse_exporter, run_live, _broker

BASE = """
kafka_brokers 1
kafka_topic_partition_under_replicated_partition{topic="orders-events",partition="0"} 0
kafka_topic_partition_leader{topic="orders-events",partition="0"} 1
kafka_consumergroup_lag{consumergroup="checkout-workers",partition="0",topic="orders-events"} 200
kafka_consumergroup_members{consumergroup="checkout-workers"} 0
kafka_consumergroup_lag{consumergroup="another-group",partition="0",topic="orders-events"} 50000
kafka_consumergroup_lag{consumergroup="checkout-workers",partition="0",topic="other-topic"} 50000
"""


class KafkaExportContractTests(unittest.TestCase):
    def test_failure_proven_by_real_group_lag(self):
        snap = parse_exporter(BASE)
        self.assertEqual((snap.brokers, snap.lag, snap.members), (1, 200, 0))
        self.assertEqual((snap.offline_partitions, snap.under_replicated), (0, 0))
        assert_signal(snap, stage="failed")

    def test_recovery_requires_drained_lag_and_member(self):
        snap = parse_exporter(
            BASE.replace('topic="orders-events"} 200', 'topic="orders-events"} 0')
                .replace('consumergroup="checkout-workers"} 0', 'consumergroup="checkout-workers"} 1')
        )
        assert_signal(snap, stage="recovered")
        with self.assertRaises(ValueError):
            assert_signal(snap, stage="failed")

    def test_missing_series_never_counts_as_recovered(self):
        for missing in ("kafka_brokers", "kafka_consumergroup_lag",
                        "kafka_consumergroup_members", "kafka_topic_partition_leader",
                        "kafka_topic_partition_under_replicated_partition"):
            lines = [s for s in BASE.splitlines() if not s.startswith(missing)]
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                assert_signal(parse_exporter("\n".join(lines)), stage="recovered")

    def test_unhealthy_or_ambiguous_partition_blocks_recovery(self):
        for changed in (
            BASE.replace('partition="0"} 0', 'partition="0"} 1'),
            BASE.replace('partition="0"} 1', 'partition="0"} -1'),
            BASE + '\nkafka_topic_partition_leader{topic="orders-events",partition="0"} 1',
        ):
            with self.subTest(sample=changed[-90:]), self.assertRaises(ValueError):
                assert_signal(parse_exporter(changed), stage="recovered")

    def test_non_finite_and_duplicate_members_are_unknown(self):
        samples = (
            BASE.replace('topic="orders-events"} 200', 'topic="orders-events"} NaN'),
            BASE + '\nkafka_consumergroup_members{consumergroup="checkout-workers"} 1',
        )
        for sample in samples:
            with self.subTest(sample=sample[-80:]), self.assertRaises(ValueError):
                assert_signal(parse_exporter(sample), stage="failed")

    def test_invalid_context_rejected_before_any_kubectl(self):
        for context in ("production", "kind-prod/../secret", "kind-", "kind-prod; rm -rf /"):
            with self.subTest(context=context), patch("validation.kafka_live.subprocess.run") as command:
                with self.assertRaises(ValueError):
                    run_live(context)
                command.assert_not_called()

    def test_broker_client_is_allowlisted(self):
        with patch("validation.kafka_live.kubectl") as command:
            with self.assertRaises(ValueError):
                _broker("kind-test", "bash", "-c", "echo bad")
            command.assert_not_called()

    def test_producer_forwards_stdin_through_kubectl_i(self):
        with patch("validation.kafka_live.kubectl", return_value="") as command:
            _broker("kind-test", "kafka-console-producer.sh", "--topic", "orders-events",
                    stdin="event\n")
            args, kwargs = command.call_args
            self.assertEqual(args[0:3], ("kind-test", "exec", "-i"))
            self.assertEqual(kwargs["stdin"], "event\n")

    def test_cluster_requires_true_kind_node_identity(self):
        with patch("validation.kafka_live.kubectl", return_value='{"items": [{"metadata": {"labels": {"kubernetes.io/hostname": "prod"}}}]}'):
            with patch("validation.kafka_live.subprocess.run") as command:
                with self.assertRaisesRegex(RuntimeError, "non-Kind"):
                    run_live("kind-test")
                command.assert_not_called()

    def test_duplicate_partition_never_counts_as_healthy(self):
        duplicate = BASE + '\nkafka_consumergroup_lag{consumergroup="checkout-workers",partition="0",topic="orders-events"} 0'
        self.assertIsNone(parse_exporter(duplicate).lag)

    def test_cleanup_after_partially_failed_apply(self):
        good_nodes = '{"items": [{"metadata": {"name": "test-control-plane"}, "spec": {"providerID": "kind://docker/test/test-control-plane"}}]}'
        with patch("validation.kafka_live.kubectl") as command, patch(
            "validation.kafka_live.subprocess.run"
        ) as process:
            from subprocess import CompletedProcess
            process.return_value = CompletedProcess([], 1, "", "Error from server (NotFound)")
            command.side_effect = [good_nodes, RuntimeError("partial apply"), ""]
            with self.assertRaisesRegex(RuntimeError, "partial apply"):
                run_live("kind-test")
            self.assertEqual(command.call_args_list[-1].args[1:3], ("delete", "-f"))


if __name__ == "__main__":
    unittest.main()
