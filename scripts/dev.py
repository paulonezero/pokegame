#!/usr/bin/env python3
"""Run the FastAPI backend and Vite frontend as one local development command."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    commands = (
        [sys.executable, "-m", "uvicorn", "server.app:app", "--reload"],
        ["npm", "--prefix", "frontend", "run", "dev"],
    )
    processes = [subprocess.Popen(command, cwd=PROJECT_ROOT) for command in commands]
    try:
        while True:
            for process in processes:
                return_code = process.poll()
                if return_code is not None:
                    return return_code
            time.sleep(0.2)
    except KeyboardInterrupt:
        return 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
