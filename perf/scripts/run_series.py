#!/usr/bin/env python3
"""Gufo/Qwen3.8-Flash-Next perf series orchestrator (plan S1->S2->S3->S5).

Runs the scenario matrix strictly sequentially using the verified AIPerf
template (plan §2). Enforces plan §3 discipline:
  - pre-run gate: host available >= 6 GiB
  - >= 60 s idle between runs
  - per-run snapshots: date, aiperf exports, /slots, /metrics before/after,
    journal since run start (grep retention/persistence/host_available_mib),
    free -h, swapon --show, du -sh /var/lib/gufo-cache
  - kill criteria: host_available_mib < 4096, sustained swap-in (si nonzero
    > 10 s), HTTP 5xx burst, gufo not active -> abort series, capture tail.
Resume: runs with a DONE marker are skipped.
"""
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

HOST = "example-user@198.51.100.56"
KEY = os.path.expanduser("~/.ssh/id_deploy")  # renamed placeholder
URL = "http://198.51.100.56:8081"
AIPERF = "/opt/data/aiperf-venv/bin/aiperf"
ROOT = Path("/opt/data/perf/results")
SSH = ["ssh", "-i", KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", HOST]

ABORT_FLAG = ROOT / "ABORT.flag"
ABORT_INFO = ROOT / "ABORT.json"
GATE_MIB = 4608  # amended gate: kill line 4096 + 512 margin (user-approved, see GATE_DEVIATION.json)
SERIES_LOG = ROOT / "SERIES.log"
SERVER_METRICS_FLAG = ROOT / ".SERVER_METRICS_EXPLICIT"

MODEL = "Qwen3.8 Flash Next"
TOKENIZER = "unsloth/Qwen3.8-Flash-Next"


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}"
    print(line, flush=True)
    with open(SERIES_LOG, "a") as f:
        f.write(line + "\n")


def ssh_sh(script: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(SSH + [script], capture_output=True, text=True, timeout=timeout)


# ---------------------------------------------------------------- kill monitor
class Monitor(threading.Thread):
    """Polls host every ~25 s during a run; writes ABORT.flag on violation."""

    def __init__(self, rdir: Path):
        super().__init__(daemon=True)
        self.rdir = rdir
        self.stop_evt = threading.Event()
        self.logf = open(rdir / "monitor.log", "a")
        self.consecutive_ssh_fail = 0

    def _emit(self, kind: str, detail: str) -> None:
        info = {"kind": kind, "detail": detail, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "run": str(self.rdir)}
        ABORT_INFO.write_text(json.dumps(info, indent=2))
        ABORT_FLAG.write_text(f"{kind}: {detail}\n")

    def run(self) -> None:
        while not self.stop_evt.is_set():
            try:
                p = ssh_sh(
                    'avail=$(free -m | awk "/^Mem:/{print \\$7}"); '
                    "act=$(systemctl is-active gufo); "
                    'ham=$(journalctl -u gufo -n 60 --no-pager 2>/dev/null | grep -o "host_available_mib=[0-9]*" | tail -1 | cut -d= -f2); '
                    'e5=$(journalctl -u gufo --since "30 seconds ago" --no-pager 2>/dev/null | grep -cE "status=5[0-9][0-9]"); '
                    'echo "avail=$avail act=$act ham=$ham e5=$e5"; '
                    "vmstat 2 6",
                    timeout=60,
                )
                if p.returncode != 0:
                    self.consecutive_ssh_fail += 1
                    if self.consecutive_ssh_fail >= 3:
                        self._emit("ssh_unreachable", f"ssh failed 3x consecutively: {p.stderr[:300]}")
                        return
                else:
                    self.consecutive_ssh_fail = 0
                    out = p.stdout
                    self.logf.write(out + "\n")
                    self.logf.flush()
                    kv = {}
                    for tok in out.splitlines()[0].split():
                        if "=" in tok:
                            k, v = tok.split("=", 1)
                            kv[k] = v
                    vm_lines = [l for l in out.splitlines()[1:] if l.strip()]
                    avail = int(kv.get("avail", "99999"))
                    ham = int(kv.get("ham", "99999"))
                    act = kv.get("act", "?")
                    e5 = int(kv.get("e5", "0"))
                    si_viol = False
                    if len(vm_lines) >= 5:
                        tok_i, hdr_i = None, None
                        for cand in ("si", "swpi"):
                            for hi in range(min(3, len(vm_lines))):
                                toks = vm_lines[hi].split()
                                if cand in toks:
                                    tok_i, hdr_i = toks.index(cand), hi
                                    break
                            if tok_i is not None:
                                break
                        if tok_i is not None:
                            rows = [r.split() for r in vm_lines[hdr_i + 1:]]
                            try:
                                sis = [int(r[tok_i]) for r in rows if len(r) > tok_i]
                                # interval rows only (drop since-boot avg), all nonzero -> sustained >10 s
                                if len(sis) >= 5 and all(s > 0 for s in sis[1:]):
                                    si_viol = True
                            except ValueError:
                                pass
                    if act != "active":
                        self._emit("service_inactive", f"systemctl is-active gufo = {act}")
                        return
                    if avail < 4096:
                        self._emit("host_available_low", f"free available {avail} MiB < 4096")
                        return
                    if ham < 4096:
                        self._emit("host_available_mib_low", f"journal host_available_mib={ham} < 4096")
                        return
                    if e5 >= 3:
                        self._emit("http_5xx_burst", f"{e5} status=5xx journal lines in 30 s window")
                        return
                    if si_viol:
                        self._emit("sustained_swap_in", "vmstat si nonzero for all >10 s interval rows")
                        return
            except subprocess.TimeoutExpired:
                self.consecutive_ssh_fail += 1
                if self.consecutive_ssh_fail >= 3:
                    self._emit("ssh_timeout", "ssh monitor timed out 3x consecutively")
                    return
            self.stop_evt.wait(25)


# ---------------------------------------------------------------- snapshots
def snapshot(rdir: Path, phase: str, start_epoch: int = None) -> None:
    tag = "before" if phase == "pre" else "after"
    (rdir / f"free_{tag}.txt").write_text(ssh_sh("free -h")  .stdout)
    (rdir / f"swap_{tag}.txt").write_text(ssh_sh("swapon --show").stdout)
    (rdir / f"du_{tag}.txt").write_text(ssh_sh("du -sh /var/lib/gufo-cache").stdout)
    (rdir / f"slots_{tag}.json").write_text(
        subprocess.run(["curl", "-sS", "-m", "10", f"{URL}/slots"], capture_output=True, text=True).stdout)
    (rdir / f"metrics_{tag}.txt").write_text(
        subprocess.run(["curl", "-sS", "-m", "10", f"{URL}/metrics"], capture_output=True, text=True).stdout)
    gen = ssh_sh("readlink /run/current-system; stat -c %y /run/current-system; date -Is")
    (rdir / f"server_gen_{tag}.txt").write_text(gen.stdout)
    if phase == "post":
        since = f"@{start_epoch}"
        until = f"@{int(time.time()) + 5}"
        j = ssh_sh(f"journalctl -u gufo --since '{since}' --until '{until}' --no-pager", timeout=120)
        (rdir / "journal_run.txt").write_text(j.stdout)
        keep = [l for l in j.stdout.splitlines()
                if ("event=retention" in l or "persistence" in l or "host_available_mib" in l)]
        (rdir / "journal_key_events.txt").write_text("\n".join(keep) + "\n")


def capture_abort_finals(rdir: Path) -> None:
    cap = rdir / "abort_capture"
    cap.mkdir(exist_ok=True)
    (cap / "journal_tail.txt").write_text(
        ssh_sh("journalctl -u gufo -n 400 --no-pager", timeout=60).stdout)
    (cap / "free.txt").write_text(ssh_sh("free -h").stdout)
    (cap / "vmstat.txt").write_text(ssh_sh("vmstat 2 6", timeout=30).stdout)
    (cap / "service.txt").write_text(
        ssh_sh("systemctl is-active gufo; systemctl status gufo --no-pager -n 25", timeout=30).stdout)
    (cap / "slots.json").write_text(
        subprocess.run(["curl", "-sS", "-m", "10", f"{URL}/slots"], capture_output=True, text=True).stdout)


# ---------------------------------------------------------------- matrix
def synth(in_tok: int, out_tok: int) -> list:
    return ["--synthetic-input-tokens-mean", str(in_tok), "--synthetic-input-tokens-stddev", "0",
            "--output-tokens-mean", str(out_tok), "--output-tokens-stddev", "0"]


def build_matrix() -> list:
    runs = []
    lengths = [512, 4096, 16384, 65536, 131072, 204800]
    # S1: cold pass (r1, the only truly-cold pass possible; RAM+disk cache persist),
    #     then immediate warm pass + 2 more warm repeats -> warm medians over r1..r3.
    for in_tok in lengths:
        runs.append(dict(scenario="S1", run_id=f"cold_r1_in{in_tok}",
                         args=["--request-count", "5", "--concurrency", "1"] + synth(in_tok, 128),
                         timeout=4200))
    for rep in (1, 2, 3):
        for in_tok in lengths:
            runs.append(dict(scenario="S1", run_id=f"warm_r{rep}_in{in_tok}",
                             args=["--request-count", "5", "--concurrency", "1"] + synth(in_tok, 128),
                             timeout=3600))
    # S2: concurrency curve, 3 reps (rep1 = series warmup)
    for rep in (1, 2, 3):
        for c in (1, 2, 4, 8, 16):
            runs.append(dict(scenario="S2", run_id=f"c{c}_r{rep}",
                             args=["--request-count", "24", "--concurrency", str(c)] + synth(2048, 256),
                             timeout=2400))
    # S3: multi-agent conversations. Turn delay 5000+-2000 ms (plan: "idle gaps", value chosen, documented).
    for rep in (1, 2, 3):
        for nc in (2, 4, 8):
            runs.append(dict(scenario="S3", run_id=f"nc{nc}_r{rep}",
                             args=["--num-conversations", str(nc),
                                   "--conversation-turn-mean", "8", "--conversation-turn-stddev", "2",
                                   "--conversation-turn-delay-mean", "5000",
                                   "--conversation-turn-delay-stddev", "2000"] + synth(4096, 256),
                             timeout=3600))
    # S5: long-context capacity (synthetic = server target - 60 per §2 template-overhead note)
    for rep in (1, 2, 3):
        runs.append(dict(scenario="S5", run_id=f"s5a_r{rep}",
                         args=["--request-count", "1", "--concurrency", "1"] + synth(249940, 128),
                         timeout=2400))
        runs.append(dict(scenario="S5", run_id=f"s5b_r{rep}",
                         args=["--request-count", "2", "--concurrency", "2"] + synth(149940, 128),
                         timeout=2400))
    return runs


# ---------------------------------------------------------------- run engine
def run_one(run: dict) -> str:
    rdir = ROOT / run["scenario"] / run["run_id"]
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "date.txt").write_text(
        f"local_utc={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
        f"host_local={ssh_sh('date -Is').stdout.strip()}\n"
        f"epoch_start_pending\n")

    log(f"PRE  {run['scenario']}/{run['run_id']}: snapshots")
    snapshot(rdir, "pre")
    start = int(time.time())
    (rdir / "date.txt").write_text(
        (rdir / "date.txt").read_text().replace("epoch_start_pending", f"epoch_start={start}"))

    cmd = [AIPERF, "profile",
           "--url", URL,
           "--endpoint-type", "chat", "--endpoint", "/v1/chat/completions",
           "--model", MODEL,
           "--tokenizer", TOKENIZER, "--tokenizer-trust-remote-code",
           "--streaming", "--use-server-token-count"]
    if SERVER_METRICS_FLAG.exists():
        cmd.append("--server-metrics")
    cmd += run["args"] + ["--warmup-request-count", "1", "--export-level", "records",
                          "--output-artifact-dir", str(rdir)]
    env = dict(os.environ, AIPERF_SERVICE_DISABLE_UVLOOP="1")
    (rdir / "cmd.txt").write_text("AIPERF_SERVICE_DISABLE_UVLOOP=1 " + " ".join(cmd) + "\n")
    log(f"RUN  {run['scenario']}/{run['run_id']}: {' '.join(run['args'])}")

    mon = Monitor(rdir)
    mon.start()
    aborted = False
    with open(rdir / "aiperf_console.log", "w") as console:
        proc = subprocess.Popen(cmd, env=env, stdout=console, stderr=subprocess.STDOUT,
                                start_new_session=True)
        t0 = time.time()
        while proc.poll() is None:
            if ABORT_FLAG.exists():
                aborted = True
                log(f"ABORT kill-criteria hit during {run['run_id']} — killing aiperf (pgid={proc.pid})")
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    proc.wait(timeout=15)
                except Exception:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except Exception:
                        pass
                break
            if time.time() - t0 > run["timeout"]:
                log(f"ABORT timeout {run['timeout']}s in {run['run_id']}")
                ABORT_INFO.write_text(json.dumps({"kind": "run_timeout", "run": str(rdir),
                                                  "timeout_s": run["timeout"]}))
                ABORT_FLAG.write_text("run_timeout\n")
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                aborted = True
                break
            time.sleep(5)
    mon.stop_evt.set()
    mon.join(timeout=40)
    rc = proc.returncode

    log(f"POST {run['scenario']}/{run['run_id']}: snapshots (rc={rc})")
    snapshot(rdir, "post", start)

    # per-run summary: aiperf stats + metrics deltas + error count
    summ = {}
    aj = rdir / "profile_export_aiperf.json"
    if aj.exists():
        try:
            d = json.loads(aj.read_text())
            for k in ("time_to_first_token", "inter_token_latency", "request_latency",
                      "output_token_throughput_per_user", "output_token_throughput",
                      "input_sequence_length", "output_sequence_length"):
                if k in d:
                    summ[k] = {s: d[k].get(s) for s in ("avg", "p50", "p95", "p99", "min", "max")}
            summ["error_summary"] = d.get("error_summary", [])
            summ["start_time"] = d.get("start_time")
            summ["end_time"] = d.get("end_time")
        except Exception as e:  # noqa: BLE001
            summ["parse_error"] = str(e)
    try:
        def ctr(path, name):
            for l in path.read_text().splitlines():
                if l.startswith(name + " "):
                    return float(l.split()[1])
            return None
        mb, ma = rdir / "metrics_before.txt", rdir / "metrics_after.txt"
        if mb.exists() and ma.exists():
            deltas = {}
            for name in ("llamacpp:prompt_tokens_total", "llamacpp:prompt_tokens_cached_total",
                         "llamacpp:prompt_seconds_total", "llamacpp:tokens_predicted_total",
                         "llamacpp:tokens_predicted_seconds_total",
                         "llamacpp:spec_decode_num_drafts_total",
                         "llamacpp:spec_decode_num_draft_tokens_total",
                         "llamacpp:spec_decode_num_accepted_tokens_total"):
                b, a = ctr(mb, name), ctr(ma, name)
                if b is not None and a is not None:
                    deltas[name] = a - b
            summ["metrics_deltas"] = deltas
            if deltas.get("llamacpp:prompt_tokens_total") is not None:
                tot = deltas["llamacpp:prompt_tokens_total"] + deltas.get("llamacpp:prompt_tokens_cached_total", 0)
                if tot > 0:
                    summ["cache_hit_ratio_metrics"] = deltas.get("llamacpp:prompt_tokens_cached_total", 0) / tot
    except Exception as e:  # noqa: BLE001
        summ["metrics_delta_error"] = str(e)
    summ["aiperf_rc"] = rc
    summ["duration_s"] = round(time.time() - start, 1)

    # server-metrics contingency (§2): on runs >= 30 s, require populated CSV rows
    dur = summ["duration_s"]
    smc = rdir / "server_metrics_export.csv"
    if not SERVER_METRICS_FLAG.exists() and dur >= 30:
        rows = len(smc.read_text().splitlines()) if smc.exists() else 0
        if rows <= 1:
            SERVER_METRICS_FLAG.write_text("enabled after run " + run["run_id"] + "\n")
            log(f"NOTE server_metrics_export.csv empty on {dur}s run -> enabling --server-metrics for later runs")
        else:
            (ROOT / ".SERVER_METRICS_OK").write_text("verified populated on " + run["run_id"] + "\n")

    # §2: verify server_metrics rows populated (also when flag already enabled)
    if SERVER_METRICS_FLAG.exists():
        rows = len(smc.read_text().splitlines()) if smc.exists() else 0
        summ["server_metrics_rows"] = rows

    (rdir / "summary.json").write_text(json.dumps(summ, indent=2))

    if aborted:
        capture_abort_finals(rdir)
        return "ABORT"
    if rc != 0:
        # unexplained aiperf failure: capture journal tail too, but let caller decide
        capture = rdir / "failure_capture"
        capture.mkdir(exist_ok=True)
        (capture / "journal_tail.txt").write_text(ssh_sh("journalctl -u gufo -n 200 --no-pager", timeout=60).stdout)
        return f"FAIL_rc{rc}"
    (rdir / "DONE").write_text(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} rc=0\n")
    return "OK"


def main() -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    runs = build_matrix()
    only = sys.argv[1] if len(sys.argv) > 1 else None
    (ROOT / "series_spec.json").write_text(json.dumps(runs, indent=2))
    log(f"SERIES start: {len(runs)} runs planned")

    for i, run in enumerate(runs):
        if only and not run["run_id"].startswith(only):
            continue
        rdir = ROOT / run["scenario"] / run["run_id"]
        if (rdir / "DONE").exists():
            log(f"SKIP {run['scenario']}/{run['run_id']} (DONE)")
            continue
        # pre-run gate: available >= 6 GiB (plan §3). AMENDED 2026-11-08 per user
        # decision to kill-line+margin (4608 MiB): RAM cache is pinned at its
        # 10 GiB budget (by design, §3), available stable ~6.0 GiB, so the
        # original gate blocks the remaining matrix indefinitely. See
        # GATE_DEVIATION.json; in-run monitor (4 GiB kill line) unchanged.
        ok = False
        for attempt in range(8):
            av = int(ssh_sh('free -m | awk "/^Mem:/{print \\$7}"').stdout.strip() or 0)
            if av >= GATE_MIB:
                ok = True
                break
            log(f"GATE {run['run_id']}: available {av} MiB < {GATE_MIB}, waiting 90 s ({attempt+1}/8)")
            time.sleep(90)
        if not ok:
            ABORT_INFO.write_text(json.dumps({"kind": "pre_run_gate", "run": str(rdir),
                                              "detail": "available never reached 6 GiB in 12 min"}))
            ABORT_FLAG.write_text("pre_run_gate\n")
            log("ABORT pre-run gate not met")
            return 3
        status = run_one(run)
        log(f"STATUS {run['scenario']}/{run['run_id']}: {status}")
        if status == "ABORT":
            log("SERIES ABORTED — see ABORT.json / abort_capture/")
            return 2
        if status.startswith("FAIL"):
            log("SERIES HALTED on unexplained aiperf failure — report, do not push through")
            return 4
        if i < len(runs) - 1:
            log("IDLE 65 s (plan §3: >= 60 s between runs)")
            time.sleep(65)
    log("SERIES COMPLETE (S1,S2,S3,S5)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
