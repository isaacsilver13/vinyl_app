"""Run the Vinyl refresh and alert jobs from any working directory."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
ROOT = Path(os.environ.get("PROJECT_ROOT", str(DEFAULT_ROOT))).resolve()
PYTHON = os.environ.get("PYTHON", sys.executable)


def run(script: Path, *args: str) -> None:
    command = [PYTHON, str(script), *args]
    print("Running:", " ".join(command))
    subprocess.run(command, cwd=ROOT, check=True)


if __name__ == "__main__":
    run(ROOT / "refresh_all.py", "--force", "--no-alerts")
    run(ROOT / "send_listing_alerts.py")
    print("Daily refresh + alerts completed.")
