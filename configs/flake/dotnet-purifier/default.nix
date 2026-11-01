# SPDX-License-Identifier: MIT
{ closure-lib, ... }:
{
  config,
  inputs,
  lib,
  ...
}:
{

  imports = [
    inputs.flake-parts.flakeModules.partitions
    closure-lib.caisson.modules.flake."purifier/purifier-packages"
  ];

  debug = false;
  caisson.libOverlays.exported = libOverlays: {
    inherit (libOverlays) default;
  };

  # The flake publishes the derivations of the scope `pkgs.dotnet-purifier`,
  # which the default package overlay (pkg-overlays/) adds. The scope also
  # holds the builder, a function.
  purifier.scope = "dotnet-purifier";

  # `nix run github:nix-purifiers/dotnet-purifier -- lock` locks the .NET
  # build in the current directory.
  perSystem =
    { pkgs, ... }:
    {
      packages.default = pkgs.dotnet-purifier.dotnet-purifier;
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
