from pathlib import Path
import tempfile
from log_normalizer import normalize_log_file
from run_worker import build_flight_report

def smoke_test(source):
    out = Path(tempfile.mkdtemp(prefix="cansat_v049_"))
    norm = normalize_log_file(Path(source), out)
    report = build_flight_report(norm, out, Path(source), "all")
    print("OK", out)
    print(report["summary"].get("stage1_average_descent_rate_mps"), report["summary"].get("stage2_average_descent_rate_mps"))

if __name__ == "__main__":
    import sys
    smoke_test(sys.argv[1])
