"""Bounded CI progress output while retaining the complete native build log."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def tail(path, lines):
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        stream.seek(max(0, stream.tell() - 65536))
        return "\n".join(stream.read().decode(errors="replace").replace("\r", "\n").splitlines()[-lines:])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--objects", type=Path)
    parser.add_argument("--interval", type=float, default=30)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or args.interval <= 0:
        parser.error("A command and positive progress interval are required")
    args.log.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    print(f"[{args.label}] starting; progress every {args.interval:g}s; full log: {args.log}", flush=True)
    with args.log.open("wb") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)

        def stop(signum, _frame):
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise SystemExit(128 + signum)

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        while True:
            try:
                code = process.wait(timeout=args.interval)
                break
            except subprocess.TimeoutExpired:
                counts = ""
                if args.objects:
                    count = sum(name.endswith(".o") for _, _, files in os.walk(args.objects) for name in files)
                    counts = f"; compiled object files: {count}"
                print(f"[{args.label}] running; elapsed {time.monotonic() - started:.0f}s{counts}", flush=True)
                print(tail(args.log, 8), flush=True)
    print(f"[{args.label}] finished; exit={code}; elapsed {time.monotonic() - started:.1f}s", flush=True)
    print(tail(args.log, 100 if code else 12), flush=True)
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    sys.exit(main())
