# strix-halo-nixos-config

NixOS configuration + inference performance-test evidence for the Strix Halo
machine serving **Qwen3.8 Flash Next** via [gufo](https://github.com) (`gufo serve llm`,
2 × 256k sessions, RAM+disk prefix cache, MTP speculative decode).

## Layout
```
nixos/          live system config (only files imported by configuration.nix)
perf/
  REPORT.html   self-contained performance report (all charts embedded, opens offline)
  scripts/      test orchestrators (AIPerf) — methodology, kill criteria, snapshots
  results/      per-run summary.json (79 runs), series logs, deviation records
```

## Applying the config
These files live in `/etc/nixos/` on the machine:
```bash
sudo nixos-rebuild switch
```
`hardware-configuration.nix` is machine-generated (`nixos-generate-config`) and
device-specific. `gufo.nix` pins the model path, session/context sizing,
`--speculative mtp`, and the 256 GiB disk cache budgets.

## Sanitization notes
- The machine's LAN IP is replaced with RFC 5737 documentation space
  (`198.51.100.56`); hostnames are not hardcoded
  (`networking.hostName` is left at the default).
- `hardware-configuration.nix` disk UUIDs are masked to `PLACEHOLDER-regenerate`
  (run `nixos-generate-config` on the target and merge).
- `perf/` contains no host identifiers; the AIPerf endpoint constants in
  `scripts/` were rewritten to the placeholder — restore the real LAN IP
  before re-running.
- The system username is replaced throughout with `example-user`; rename it
  back (user, home paths, service `User=`) before deploying.

## Before deploying
- `gufo.nix` binds the inference API to `0.0.0.0:8081` **unauthenticated** (with
  `odysseus.nix` opening 7000/8081 in the firewall) — intentional LAN-only design;
  don't expose this box.
- `configuration.nix` seeds `initialPassword` for `example-user`; set a real
  password/hash immediately on any non-LAN deployment.

## Test series at a glance (2026-11-08)
Full scenario matrix S1→S2→S3→S5 + S4b MTP-off A/B; 0 failed requests, 0 kill-criteria
breaches. Headlines: cold prefill ≈0.9–1.5 k tok/s (TTFT 150 s @200 k), cache-hit
restores 0.1–5 s, system decode plateaus ≈34 tok/s (2 sessions), MTP ON wins every
regime (≈2× solo decode). See `perf/REPORT.html` §7/§10 for verdicts and tuning recs.
