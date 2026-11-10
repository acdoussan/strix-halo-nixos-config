{ config, pkgs, ... }:

let
  gufo = (builtins.getFlake "github:gufo-org/gufo/3373114").packages.${pkgs.system}.default;
  m = "/home/example-user/models/qwen3.8-flash-next";
in {
  users.users.example-user.extraGroups = [ "render" "video" ];

  # Port 8081 = gufo API (inherited from the retired odysseus.nix module;
  # its unused 7000 Odysseus-UI opening was dropped)
  networking.firewall.allowedTCPPorts = [ 8081 ];

  systemd.services.gufo = {
    enable = true;
    description = "Gufo - Qwen3.8-Flash-Next (2 x 256k sessions, multi-agent cache)";
    wantedBy = [ "multi-user.target" ];
    after = [ "network.target" ];
    serviceConfig = {
      User = "example-user";

      # A: parallelism + context — 2 sessions at the model's native 262144 (256 Ki)
      #     tokens; YaRN intentionally NOT enabled (only needed above 262144).
      # B: cache — explicit 10 GiB RAM checkpoint budget (the auto default is
      #    half-of-free-at-startup, nondeterministic on this box) plus a 256 GiB
      #    disk continuation cache on NVMe with a 2 GiB pinned staging buffer
      #    (default 1 MiB is far too small for 256k-token checkpoints).
      ExecStart = "${gufo}/bin/gufo serve --host 0.0.0.0 --port 8081 llm"
        + " --model ${m}/UD-Q4_K_XL/Qwen3.8-Flash-Next-UD-Q4_K_XL-00001-of-00004.gguf"
        + " --speculative mtp --mtp-model ${m}/MTP/mtp-Qwen3.8-Flash-Next-shared-Q8_0.gguf"
        + " --sessions 2 --context 262144"
        + " --max-connections 64 --max-pending 64"
        + " --cache-ram-bytes 10737418240"
        + " --cache-disk /var/lib/gufo-cache"
        + " --cache-disk-bytes 274877906944"
        + " --cache-disk-staging-bytes 2147483648";

      # C: unit hardening
      LimitMEMLOCK = "infinity";        # required by gufo (GPU memory locking)
      LimitNOFILE = 65536;              # 4-shard mmap + 64 connections
      ManagedOOMPreference = "omit";    # never let systemd-oomd kill the model server
      Restart = "on-failure";
      RestartSec = "10s";
      StateDirectory = "gufo-cache";    # creates /var/lib/gufo-cache owned by User
      # environment.sessionVariables never reach systemd services; set ROCm vars explicitly
      Environment = [
        "HSA_OVERRIDE_GFX_VERSION=11.5.1"
        "ROCBLAS_USE_HIPBLASLT=1"
      ];
    };
  };
}
