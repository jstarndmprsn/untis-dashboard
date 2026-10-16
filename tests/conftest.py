import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "app"), os.path.join(ROOT, "system")]
# Tests dürfen nie eine echte config.env lesen oder nach /run schreiben
os.environ.setdefault("RASPY_CONFIG", os.path.join(ROOT, "tests", "no-config.env"))
