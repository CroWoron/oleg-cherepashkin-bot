import os
import signal
import subprocess
import sys
import time
from pathlib import Path


COMMANDS = ([sys.executable, "bot.py"], [sys.executable, "vk_bot.py"])
PID_FILES = ("telegram.pid", "vk.pid")


def clear_stale_pid_files(data_dir=None):
    data_dir = Path(data_dir or os.getenv("DATA_DIR", "data"))
    for name in PID_FILES:
        (data_dir / name).unlink(missing_ok=True)


def wait_for_exit(processes):
    while True:
        for process in processes:
            return_code = process.poll()
            if return_code is not None:
                return return_code or 1
        time.sleep(1)


def stop_processes(processes):
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def main():
    clear_stale_pid_files()
    processes = [subprocess.Popen(command) for command in COMMANDS]

    def stop(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        return wait_for_exit(processes)
    except KeyboardInterrupt:
        return 0
    finally:
        stop_processes(processes)


if __name__ == "__main__":
    raise SystemExit(main())
