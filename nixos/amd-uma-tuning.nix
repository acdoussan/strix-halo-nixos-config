{ config, lib, pkgs, ... }:

{
  # ============================================
  # AMD Strix Halo (Ryzen AI Max+ 395) UMA Tuning
  # ============================================
  # This module configures the unified memory architecture for optimal
  # LLM inference performance on the Strix Halo mini PC (128 GB LPDDR5X).
  #
  # KEY CONCEPTS:
  # - BIOS UMA carveout: Should be set to 512 MB minimum in BIOS
  # - GTT/TTM limit: Dynamic GPU memory mapping (set to 120 GB here)
  # - The GPU can then use up to 120 GB of the 128 GB pool
  #
  # VERIFICATION AFTER REBOOT:
  #   free -h                      # Should show ~124 GB system RAM
  #   cat /sys/module/ttm/parameters/pages_limit
  #                                # Should show ~31457280 (120 GB)
  #   amd-ttm                      # Shows current TTM configuration
  # ============================================

  # Kernel parameters for GPU memory mapping
  boot.kernelParams = [
    # Allow GPU to map up to 120 GB of system RAM
    # 31457280 pages × 4 KB = 120 GB
    "ttm.pages_limit=31457280"

    # Match page pool size for optimal allocation
    "ttm.page_pool_size=31457280"

    # Optional: Disable IOMMU if you encounter allocation issues
    # Uncomment if models fail to load with "out of memory" errors
    # "amd_iommu=off"
  ];

  # AMD GPU driver configuration
  # NOTE: gttsize is deprecated in favor of ttm.pages_limit, but kept for compatibility
  boot.extraModprobeConfig = ''
    # GTT size (deprecated but harmless - ttm.pages_limit takes precedence)
    options amdgpu gttsize=122880
  '';

  # Install AMD memory tuning and monitoring tools
  # Corrected package names verified in nixpkgs:
  environment.systemPackages = with pkgs; [
    # AMD TTM memory tuning utility (available in nixpkgs as amd-debug-tools)
    amd-debug-tools

    # ROCm SMI for GPU monitoring (available as rocmPackages.rocm-smi)
    rocmPackages.rocm-smi

    # nvtop for AMD GPU monitoring (like htop for GPU)
    nvtopPackages.amd

    # llama.cpp benchmarking tool (included in llama-cpp-vulkan package)
    # llama-bench is available as part of llama-cpp-vulkan
    llama-cpp-vulkan

    # Additional useful tools
    htop
    btop
  ];

  # Environment variables for ROCm (if installed later)
  environment.sessionVariables = {
    # Override GPU version for gfx1151 (Strix Halo)
    HSA_OVERRIDE_GFX_VERSION = "11.5.1";

    # Use hipBLASLt for better performance
    ROCBLAS_USE_HIPBLASLT = "1";

    # Disable SDMA if you encounter GPU hangs (optional)
    # HSA_ENABLE_SDMA = "0";
  };

  # Ensure video/render groups have access
  # (user should already be in these groups)
  users.users.example-user.extraGroups = [ "video" "render" ];
}
