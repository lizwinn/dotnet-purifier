# SPDX-License-Identifier: MIT
#
# The default package overlay. It adds one attribute to the package set,
# the scope `pkgs.dotnet-purifier` of pkgs/default.nix, which holds the
# command (`.dotnet-purifier`) and the builder (`.buildDotnetProject`). A
# package written for callPackage takes the scope by name, and
# `pkgs.dotnet-purifier.overrideScope` replaces what the scope shares. A
# consumer that lists this flake in `projects` holds the overlay as
# `dotnet-purifier/default`, which its package sets apply by default.
{ closure-lib, ... }:
{
  overlay = closure-lib.caisson.nixpkgs.mkPackagesOverlay ../../pkgs "dotnet-purifier";
}
