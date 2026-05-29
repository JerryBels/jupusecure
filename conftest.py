import sys
from pathlib import Path

# Ensure the project root is importable (config.py and the packages live there).
sys.path.insert(0, str(Path(__file__).resolve().parent))
