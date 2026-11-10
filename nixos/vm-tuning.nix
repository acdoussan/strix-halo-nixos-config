{ config, lib, pkgs, ... }:

# ============================================
# VM / memory tuning for LLM inference workload
# (Qwen3.8-Flash-Next on Gufo — see optimization plan 2026-11-07)
# ============================================
{
  boot.kernel.sysctl = {
    # Keep model weights and KV pages resident; discourage swap-out
    "vm.swappiness" = 10;

    # Protect the mmap'd ~104 GiB weight page cache from inode/dentry reclaim
    "vm.vfs_cache_pressure" = 10;

    # Headroom for multi-shard GGUF mmap + GPU buffer mappings
    "vm.max_map_count" = 262144;

    # Smoother, more frequent writeback for the gufo disk cache checkpoints
    "vm.dirty_background_ratio" = 3;
    "vm.dirty_ratio" = 10;
  };

  # Compressed RAM swap as OOM insurance (~15 GiB on this 124 GiB box).
  # Not a performance tier — it absorbs transient spikes so the OOM killer
  # never fires while the 104 GiB model is resident.
  zramSwap = {
    enable = true;
    memoryPercent = 12;
  };
}
