import subprocess
import unittest
from unittest.mock import Mock, patch

import run_bots


class RunBotsTest(unittest.TestCase):
    def test_wait_returns_failure_when_either_bot_stops(self):
        telegram = Mock()
        telegram.poll.side_effect = [None, 7]
        vk = Mock()
        vk.poll.return_value = None
        with patch("run_bots.time.sleep"):
            self.assertEqual(run_bots.wait_for_exit([telegram, vk]), 7)

    def test_stop_terminates_running_children_and_kills_stuck_child(self):
        stopped = Mock()
        stopped.poll.return_value = 0
        running = Mock()
        running.poll.return_value = None
        running.wait.side_effect = subprocess.TimeoutExpired("bot", 10)

        run_bots.stop_processes([stopped, running])

        stopped.terminate.assert_not_called()
        running.terminate.assert_called_once()
        running.kill.assert_called_once()


if __name__ == "__main__":
    unittest.main()
