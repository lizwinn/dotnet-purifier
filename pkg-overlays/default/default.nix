# SPDX-License-Identifier: MIT
#
# The default package overlay. It adds the packages of pkgs/ under
# `pkgs.dotnet-purifier` (`.dotnet-purifier`, the command), and the builder
# `pkgs.buildDotnetProject` (nix/lib/build-dotnet-project.nix) beside them,
# where nixpkgs keeps its own builders, so a package written for
# callPackage takes it by name. A consumer that lists this flake in
# `projects` holds it as `dotnet-purifier/default`, which its package sets
# apply by default.
{ closure-lib, ... }:
let
  packages = closure-lib.caisson.nixpkgs.mkPackagesOverlay ../../pkgs "dotnet-purifier";
in
{
  overlay =
    final: prev:
    packages final prev
    // {
      buildDotnetProject = final.callPackage ../../nix/lib/build-dotnet-project.nix { };
    };
}
