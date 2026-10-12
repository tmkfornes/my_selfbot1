from datetime import datetime

from colorama import Fore, Style, init

init(autoreset=True)


def _stamp():
    return datetime.now().strftime("%H:%M:%S")


class log:
    @staticmethod
    def info(msg):
        print(f"{Fore.CYAN}[{_stamp()}] ℹ {msg}{Style.RESET_ALL}")

    @staticmethod
    def ok(msg):
        print(f"{Fore.GREEN}[{_stamp()}] ✅ {msg}{Style.RESET_ALL}")

    @staticmethod
    def warn(msg):
        print(f"{Fore.YELLOW}[{_stamp()}] ⚠ {msg}{Style.RESET_ALL}")

    @staticmethod
    def error(msg):
        print(f"{Fore.RED}[{_stamp()}] ✖ {msg}{Style.RESET_ALL}")
