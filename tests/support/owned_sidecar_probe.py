from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    for raw_line in sys.stdin.buffer:
        command, _, argument = raw_line.rstrip(b"\n").partition(b" ")
        if command == b"ECHO":
            sys.stdout.buffer.write(base64.b64decode(argument))
            sys.stdout.buffer.flush()
        elif command == b"STDERR":
            sys.stderr.buffer.write(base64.b64decode(argument))
            sys.stderr.buffer.flush()
        elif command == b"FLOOD":
            size = int(argument)
            sys.stdout.buffer.write(b"O" * size)
            sys.stdout.buffer.flush()
            sys.stderr.buffer.write(b"E" * size)
            sys.stderr.buffer.flush()
        elif command == b"FD":
            try:
                os.fstat(int(argument))
            except OSError:
                result = b"CLOSED\n"
            else:
                result = b"INHERITED\n"
            sys.stdout.buffer.write(result)
            sys.stdout.buffer.flush()
        elif command == b"IDENTITY":
            identity = {
                "argv": sys.argv,
                "env": dict(os.environ),
                "parent_pid": os.getppid(),
                "pid": os.getpid(),
                "process_group": os.getpgrp() if hasattr(os, "getpgrp") else None,
            }
            sys.stdout.buffer.write(json.dumps(identity, sort_keys=True).encode() + b"\n")
            sys.stdout.buffer.flush()
        elif command == b"GRANDCHILD":
            marker = Path(base64.b64decode(argument).decode())
            child_code = (
                "import signal,sys,time; from pathlib import Path; marker=Path(sys.argv[1]); "
                "signal.signal(signal.SIGTERM, lambda *_: (marker.write_text('terminated'), sys.exit(0))); "
                "marker.write_text('ready'); time.sleep(60)"
            )
            child = subprocess.Popen([sys.executable, "-c", child_code, str(marker)])
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            sys.stdout.buffer.write(f"{child.pid}\n".encode())
            sys.stdout.buffer.flush()
        elif command == b"ORPHAN":
            marker = Path(base64.b64decode(argument).decode())
            child_code = (
                "import os,sys,time; from pathlib import Path; parent=int(sys.argv[1]); marker=Path(sys.argv[2]); "
                "\nwhile os.getppid() == parent: time.sleep(0.01)"
                "\nmarker.write_text('parent-exited'); time.sleep(60)"
            )
            child = subprocess.Popen(
                [sys.executable, "-c", child_code, str(os.getpid()), str(marker)]
            )
            sys.stdout.buffer.write(f"{child.pid}\n".encode())
            sys.stdout.buffer.flush()
            return 0
        elif command == b"SLEEP":
            time.sleep(float(argument))
        elif command == b"EXIT":
            return int(argument)
        else:
            return 64
    sys.stdout.buffer.write(b"EOF\n")
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
