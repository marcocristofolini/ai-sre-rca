from io import BytesIO
import unittest
from unittest.mock import patch

from validation.workload import redis_scenario as redis


class RedisValidationSafetyTests(unittest.TestCase):
    def test_bulk_reply_preserves_binary_values(self):
        self.assertEqual(redis.read_response(BytesIO(b"$4\r\na\x00bc\r\n")), b"a\x00bc")
        self.assertIsNone(redis.read_response(BytesIO(b"$-1\r\n")))
        self.assertEqual(redis.read_response(BytesIO(b":7\r\n")), 7)

    def test_error_reply_cannot_be_success(self):
        with self.assertRaisesRegex(redis.RedisCommandError, "OOM"):
            redis.read_response(BytesIO(b"-OOM command denied\r\n"))

    def test_malformed_or_oversized_reply_fails_closed(self):
        for raw in (b"$5\r\nabc\r\n", b"$-2\r\n", b"$4194305\r\n", b"+OK", b"*1\r\n"):
            with self.subTest(raw=raw), self.assertRaises(RuntimeError):
                redis.read_response(BytesIO(raw))

    def test_failure_probe_requires_actual_oom_rejection(self):
        with patch.object(redis, "command", return_value="OK"):
            with self.assertRaisesRegex(RuntimeError, "unexpectedly accepted"):
                redis.fail_write()
        with patch.object(redis, "command", side_effect=redis.RedisCommandError("NOAUTH permission denied")):
            with self.assertRaisesRegex(redis.RedisCommandError, "NOAUTH"):
                redis.fail_write()
        with patch.object(redis, "command", side_effect=redis.RedisCommandError("OOM command denied")):
            with self.assertRaisesRegex(redis.RedisCommandError, "OOM: validation write"):
                redis.fail_write()

    def test_loader_has_a_hard_key_budget(self):
        with patch.object(redis, "memory_state", return_value={"utilization": 0.1}), patch.object(redis, "command") as command:
            with self.assertRaisesRegex(RuntimeError, "did not reach"):
                redis.load_memory()
        self.assertEqual(command.call_count, redis.MAX_KEYS)

    def test_recovery_only_deletes_owned_bounded_keys(self):
        with patch.object(redis, "command") as command, patch.object(redis, "memory_state", return_value={"utilization": 0.1}):
            redis.recover_memory()
        args = command.call_args.args
        self.assertEqual(args[0], "DEL")
        self.assertEqual(len(args[1:]), redis.MAX_KEYS + 1)
        self.assertTrue(all(k.startswith(redis.KEY_PREFIX) for k in args[1:]))

    def test_recovery_request_requires_a_successful_read_after_write(self):
        with patch.object(redis, "command", side_effect=["OK", None]):
            with self.assertRaisesRegex(RuntimeError, "did not return"):
                redis.healthy_request()


if __name__ == "__main__":
    unittest.main()
