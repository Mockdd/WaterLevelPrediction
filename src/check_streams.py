"""Quick inspection of korStream_x unique values."""
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))
from utils import read_obs, OBS_PATH

rows = read_obs(OBS_PATH)
streams = sorted(set(r.get("korStream_x", "").strip() for r in rows))
print(f"Total rows: {len(rows)}")
print(f"Unique korStream_x values ({len(streams)}):")
for s in streams:
    print(repr(s))
