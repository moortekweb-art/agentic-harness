"""Reset the demo bug after a run."""

import shutil
from pathlib import Path


Path("calculator.py").write_text(
    "def add(left: int, right: int) -> int:\n"
    "    return left + right + 1\n",
    encoding="utf-8",
)
shutil.rmtree("__pycache__", ignore_errors=True)
shutil.rmtree("tests/__pycache__", ignore_errors=True)
print("reset calculator.py")
