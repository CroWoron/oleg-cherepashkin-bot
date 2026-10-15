import signal
import subprocess
import sys
import time


COMMANDS = ([sys.executable, "bot.py"], [sys.executable, "vk_bot.py"])


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
