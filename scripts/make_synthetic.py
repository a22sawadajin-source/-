"""Generate SYNTHETIC random-walk data to smoke-test the pipeline. Results are meaningless."""
import csv, random, sys, os
out = sys.argv[1]
os.makedirs(out, exist_ok=True)
random.seed(1)
t0 = 1_640_995_200  # 2022-01-01
p = 40000.0
with open(f"{out}/bars_1h.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["time", "open", "high", "low", "close", "volume"])
    for i in range(24 * 730):
        o = p; c = o * (1 + random.gauss(0.00005, 0.006))
        h = max(o, c) * (1 + abs(random.gauss(0, 0.002))); l = min(o, c) * (1 - abs(random.gauss(0, 0.002)))
        w.writerow([t0 + i * 3600, o, h, l, c, 1]); p = c
with open(f"{out}/funding.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["time", "rate"])
    for k in range(3 * 730):
        w.writerow([t0 + k * 28800, 0.0001])
