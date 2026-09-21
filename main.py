"""Stable entry point, including on macOS volumes that mark .pth files hidden."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from qwen_harness.cli import main

if __name__ == "__main__":
    main()
