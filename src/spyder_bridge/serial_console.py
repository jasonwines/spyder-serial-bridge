"""Minimal line-based serial terminal that plays the control system.

Type a command and press Enter; it is sent with a CR and each CR-terminated
reply is printed. Works against the bridge's virtual port or, on the bench,
the second USB-RS232 adapter on the null modem cable::

    python -m spyder_bridge.serial_console /dev/ttys012
    python -m spyder_bridge.serial_console /dev/tty.usbserial-XXXX --baud 9600

Commands can also be piped in: ``printf 'RSC 1\\nRSC 2\\n' | python -m ...``.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time

import serial


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send CR-terminated commands over serial.")
    parser.add_argument("port")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument(
        "--wait", type=float, default=2.0,
        help="seconds to wait for outstanding replies after input ends",
    )
    args = parser.parse_args(argv)

    try:
        port = serial.Serial(args.port, args.baud, timeout=0.1)
    except serial.SerialException as e:
        print(f"cannot open {args.port}: {e}", file=sys.stderr)
        return 1

    sent = 0
    received = 0
    stop = threading.Event()

    def reader() -> None:
        nonlocal received
        buf = b""
        while not stop.is_set():
            try:
                buf += port.read(256)
            except serial.SerialException as e:
                print(f"read error: {e}", file=sys.stderr)
                return
            while b"\r" in buf:
                line, _, buf = buf.partition(b"\r")
                print(f"< {line.decode('ascii', 'backslashreplace')}", flush=True)
                received += 1

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()
    interactive = sys.stdin.isatty()
    if interactive:
        print(f"connected to {args.port} at {args.baud}; Ctrl-D to quit", flush=True)

    try:
        for line in sys.stdin:
            command = line.rstrip("\r\n")
            if not command:
                continue
            if not interactive:
                print(f"> {command}", flush=True)
            port.write(command.encode("ascii") + b"\r")
            sent += 1
    except KeyboardInterrupt:
        pass

    deadline = time.monotonic() + args.wait
    while received < sent and time.monotonic() < deadline:
        time.sleep(0.05)
    stop.set()
    reader_thread.join()  # its read() times out within 0.1s
    port.close()
    if received < sent:
        print(f"{sent - received} command(s) got no reply", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
