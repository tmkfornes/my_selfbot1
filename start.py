import os
import subprocess
import sys
import time

from logger import log

python = sys.executable
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def spawn(script):
    return subprocess.Popen([python, os.path.join(BASE_DIR, script)])


def main():
    # main.py starts the Control Bot itself, in-process — no need to spawn
    # control_bot.py separately (it has no __main__ block anyway, so doing
    # so used to just burn RAM on an idle interpreter for nothing).
    log.info("Starting SelfBot (includes Control Bot)...")
    selfbot = spawn("main.py")

    log.ok("Everything started")

    try:
        while True:
            time.sleep(10)

            if selfbot.poll() is not None:
                log.error("SelfBot stopped! Restarting...")
                selfbot = spawn("main.py")

    except KeyboardInterrupt:
        log.info("Stopping...")
        selfbot.terminate()


if __name__ == "__main__":
    main()
