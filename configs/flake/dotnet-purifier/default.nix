# SPDX-License-Identifier: MIT
{ ... }:
{
  config,
  inputs,
  lib,
  ...
}:
{

  imports = [ inputs.flake-parts.flakeModules.partitions ];

  debug = false;
  caisson = {
    libOverlays.exported = libOverlays: {
      inherit (libOverlays) default;
    };

    # The scope is the `default` entry of this flake's package overlay
    # registry (pkg-overlays/): the package set applies it by default, and
    # the flake exports it as `pkgOverlays` and as the plain
    # `overlays.default`. The scope holds the builder, a function, which
    # the packages export of the scope would reject, so the flake lists
    # the derivations of the scope in `packages` itself.
    nixpkgs = {
      packages.export.enabled = false;
    };
  };

  # `nix run github:nix-purifiers/dotnet-purifier -- lock` locks the .NET
  # build in the current directory.
  perSystem =
    { pkgs, ... }:
    {
      packages = lib.filterAttrs (_: lib.isDerivation) pkgs.dotnet-purifier // {
        default = pkgs.dotnet-purifier.dotnet-purifier;
      };
    };

  partitionedAttrs.checks = "checks";
  partitionedAttrs.formatter = "formatter";

  partitions.formatter = {
    extraInputs = (lib.caisson.pins.flake-compat ../../../tests/dependencies).sources;
    module =
      { inputs, ... }:
      {
        imports = [ inputs.treefmt-nix.flakeModule ];
        perSystem.treefmt.programs.nixfmt.enable = true;
      };
  };

  partitions.checks = {
    extraInputs = (lib.caisson.pins.flake-compat ../../../tests/dependencies).sources;
    module =
      { inputs, self, ... }:
      {
        imports = [ inputs.treefmt-nix.flakeModule ];
        perSystem =
          { pkgs, ... }:
          {
            checks = import ../../../tests {
              inherit pkgs;
              overlay = self.overlays.default;
            };
            treefmt.programs.nixfmt.enable = true;
          };
      };
  };

}
