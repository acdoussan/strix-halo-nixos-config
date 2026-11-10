#!/usr/bin/env python3
"""S4b arm (MTP OFF leg) — mirrors S2/S3 shapes exactly for paired A/B vs MTP-on data.
Config state: user switched /etc/nixos/gufo.nix to /tmp/gufo.nix.mtpoff (--speculative off).
Same §3 discipline via run_series.py machinery (snapshots, monitor, kill criteria, UVLOOP=1)."""
import sys, time
sys.path.insert(0, "/opt/data/perf")
import run_series as rs

def main() -> int:
    rs.log("S4b (mtp-off) series start")
    runs = []
    # solo decode-rate probe without spec-decode (baseline on-config rate: 41.6 tok/s/user)
    for rep in (1, 2):
        runs.append(dict(scenario="S4", run_id=f"off_c1_r{rep}",
                         args=["--request-count", "12", "--concurrency", "1"] + rs.synth(2048, 256),
                         timeout=1800))
    # width-2 decode A/B vs S2/c2 (on: active 28.2 tok/s/agent, sys 33.6, queue p50 0.1s)
    for rep in (1, 2, 3):
        runs.append(dict(scenario="S4", run_id=f"off_c2_r{rep}",
                         args=["--request-count", "24", "--concurrency", "2"] + rs.synth(2048, 256),
                         timeout=2400))
    # the operating point: paired with S3/conc_nc2_* (on: active 28, effective 11.7, queue p50 0.1s)
    for rep in (1, 2, 3):
        runs.append(dict(scenario="S4", run_id=f"off_nc2_r{rep}",
                         args=["--num-conversations", "2", "--concurrency", "2",
                               "--conversation-turn-mean", "8", "--conversation-turn-stddev", "2",
                               "--conversation-turn-delay-mean", "5000",
                               "--conversation-turn-delay-stddev", "2000"] + rs.synth(4096, 256),
                         timeout=3600))
    # shape check: paired with S3/conc_nc4_* (on: effective 7.2, queue p50 12.8s)
    for rep in (1, 2):
        runs.append(dict(scenario="S4", run_id=f"off_nc4_r{rep}",
                         args=["--num-conversations", "4", "--concurrency", "4",
                               "--conversation-turn-mean", "8", "--conversation-turn-stddev", "2",
                               "--conversation-turn-delay-mean", "5000",
                               "--conversation-turn-delay-stddev", "2000"] + rs.synth(4096, 256),
                         timeout=3600))

    for i, run in enumerate(runs):
        rdir = rs.ROOT / run["scenario"] / run["run_id"]
        if (rdir / "DONE").exists():
            rs.log(f"SKIP S4/{run['run_id']} (DONE)")
            continue
        av = int(rs.ssh_sh('free -m | awk "/^Mem:/{print \\$7}"').stdout.strip() or 0)
        if av < rs.GATE_MIB:
            rs.log(f"ABORT pre-run gate {av} < {rs.GATE_MIB}")
            return 3
        status = rs.run_one(run)
        rs.log(f"STATUS S4/{run['run_id']}: {status}")
        if status != "OK":
            return 2
        if i < len(runs) - 1:
            time.sleep(65)
    rs.log("S4b (mtp-off) series COMPLETE")
    return 0

if __name__ == "__main__":
    sys.exit(main())
