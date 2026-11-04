"""One-command setup for Jarvis.

Creates .venv, installs dependencies and prepares .env without requiring the
user to edit files manually. Run with: python bootstrap.py
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQ = ROOT / "requirements.txt"
ENV_EXAMPLE = ROOT / ".env.example"
ENV_TARGET = ROOT / ".env"
VENV = ROOT / ".venv"
PYTHON = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

def run(cmd, **kw):
    print(">", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True, **kw)

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Jarvis bootstrap")
    parser.add_argument("--demo", action="store_true", help="skip first-run API prompts")
    parser.add_argument("--no-install", action="store_true", help="only prepare .env")
    args = parser.parse_args()

    if sys.version_info < (3, 11):
        raise SystemExit("Python 3.11+ is required")

    if not VENV.exists():
        run([sys.executable, "-m", "venv", str(VENV)])
    else:
        print(f".venv already exists: {VENV}")

    if not args.no_install:
        try:
            run([str(PYTHON), "-m", "pip", "install", "--upgrade", "pip"])
        except subprocess.CalledProcessError:
            pass
        run([str(PYTHON), "-m", "pip", "install", "-r", str(REQ)], cwd=ROOT)

    if not ENV_TARGET.exists() and ENV_EXAMPLE.exists():
        shutil.copyfile(ENV_EXAMPLE, ENV_TARGET)
        print(f"created {ENV_TARGET}")

    if args.demo:
        # allow running without API keys in reduced/offline mode
        with ENV_TARGET.open("a", encoding="utf-8") as f:
            f.write("\nJARVIS_DEMO_MODE=1\n")
        print("demo mode enabled")

    # optional sanity check
    subprocess.run([str(PYTHON), "main.py", "--list-devices"], cwd=ROOT)

    print("\nDone. Next steps:")
    print("  .venv\\Scripts\\Activate.ps1" if os.name == "nt" else "  source .venv/bin/activate")
    print("  python main.py --text        # try without microphone")
    print("  python gui.py               # full interface")

if __name__ == "__main__":
    main()
