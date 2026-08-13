"""Put tests/ on sys.path so render_app is importable from every test module."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
