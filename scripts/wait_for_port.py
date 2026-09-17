"""Block until 127.0.0.1:<port> accepts a connection, or exit 1 after <timeout>s."""

import socket
import sys
import time


def main() -> int:
    port = int(sys.argv[1])
    timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 45.0
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return 0
        except OSError:
            time.sleep(0.5)
    print(f"port {port} did not become ready within {timeout}s", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
