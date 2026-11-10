#!/usr/bin/env python3
"""Supplemental runs: (A) seed-pinned S1 cold->warm replay pairs,
(B) S3 conversations with REAL concurrency (AIPerf 0.7.0 defaults to
client concurrency=1 in conversation mode, which serialized nc runs).
Reuses run_series.py machinery (monitor, snapshots, summaries, gate)."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/opt/data/perf")
import run_series as rs  # noqa: E402

SEED = "--random-seed"


def seed_pair_test() -> bool:
    a = dict(scenario="S1", run_id="seedtest_x",
             args=["--request-count", "2", "--concurrency", "1"] + [SEED, "42"] + rs.synth(1024, 32),
             timeout=600)
    b = dict(scenario="S1", run_id="seedtest_y",
             args=["--request-count", "2", "--concurrency", "1"] + [SEED, "42"] + rs.synth(1024, 32),
             timeout=600)
    for r in (a, b):
        if (rs.ROOT / r["scenario"] / r["run_id"] / "DONE").exists():
            continue
        if rs.run_one(r) != "OK":
            return False
        time.sleep(65)
    da = json.loads((rs.ROOT / "S1/seedtest_x/inputs.json").read_text())["data"]
    db = json.loads((rs.ROOT / "S1/seedtest_y/inputs.json").read_text())["data"]
    same = da == db
    (rs.ROOT / "SEED_PIN_CHECK.json").write_text(json.dumps(
        {"identical": same, "n_entries": len(da),
         "note": "if identical=True, seed-pinned payloads replay byte-identical content across runs"}, indent=2))
    rs.log(f"SEED pin check: identical={same}")
    return same


def main() -> int:
    rs.log("SUPP series start")
    pinned = seed_pair_test()

    runs = []
    if pinned:
        # (A) same-content cold->warm replay pairs; skip 16k (already ~partial gain, small snapshots)
        for length in (65536, 204800):
            for kind in ("seedcold", "seedwarm"):
                runs.append(dict(scenario="S1", run_id=f"{kind}_in{length}",
                                 args=["--request-count", "5", "--concurrency", "1", SEED, "42"] + rs.synth(length, 128),
                                 timeout=4200))
    else:
        rs.log("NOTE payloads cannot be pinned with --random-seed; skipping seed-pair S1 replays")

    # (B) S3 with explicit concurrency == num_conversations
    for rep in (1, 2, 3):
        for nc in (2, 4, 8):
            runs.append(dict(scenario="S3", run_id=f"conc_nc{nc}_r{rep}",
                             args=["--num-conversations", str(nc), "--concurrency", str(nc),
                                   "--conversation-turn-mean", "8", "--conversation-turn-stddev", "2",
                                   "--conversation-turn-delay-mean", "5000",
                                   "--conversation-turn-delay-stddev", "2000"] + rs.synth(4096, 256),
                             timeout=3600))

    for i, run in enumerate(runs):
        rdir = rs.ROOT / run["scenario"] / run["run_id"]
        if (rdir / "DONE").exists():
            rs.log(f"SKIP {run['scenario']}/{run['run_id']} (DONE)")
            continue
        av = int(rs.ssh_sh('free -m | awk "/^Mem:/{print \\$7}"').stdout.strip() or 0)
        if av < rs.GATE_MIB:
            rs.log(f"ABORT pre-run gate {av} < {rs.GATE_MIB}")
            return 3
        status = rs.run_one(run)
        rs.log(f"STATUS {run['scenario']}/{run['run_id']}: {status}")
        if status != "OK":
            return 2
        if i < len(runs) - 3:
            time.sleep(65)
    rs.log("SUPP series COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
