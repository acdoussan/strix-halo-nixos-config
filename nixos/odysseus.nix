{ config, pkgs, ... }:

{
  environment.systemPackages = with pkgs; [
    git
    tmux
    wget
    llama-cpp-vulkan
    openssl
    xxd
  ];

  networking.firewall.allowedTCPPorts = [
    7000
    8081
  ];

  # systemd service for llama-server (optimized for Strix Halo)
  systemd.services.llama-server = {
    enable = false;
    description = "LLaMA Server (Optimized for Ryzen AI Max+ 395)";
    after = ["network.target"];
    wantedBy = [ "multi-user.target" ];

    serviceConfig = {
      Type = "exec";
      User = "example-user";
      Environment = "HOME=/home/example-user";
      
      # Optimized flags for AMD Strix Halo (gfx1151):
      # --batch-size 32768: Large batch for long-context prompts
      # --ubatch-size 8192: Micro-batch for memory efficiency
      # --flash-attn: Flash Attention for speed (Vulkan backend)
      # --no-mmap: Prevent memory mapping overhead
      ExecStart = ''
        /run/current-system/sw/bin/llama-server \
          --models-dir /home/example-user/models \
          --models-max 2 \
          --ctx-size 262144 \
	  --batch-size 2048 \
          --ubatch-size 2048 \
          --flash-attn on \
          --timeout 3600 \
          --no-mmap \
          --host 0.0.0.0 \
          --port 8081
      '';
      
      Restart = "always";
      
      # Resource limits for 128 GB system
      LimitNOFILE = 65536;
    };
  };
}
