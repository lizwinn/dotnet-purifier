# SPDX-License-Identifier: MIT
#
# The default package overlay. It adds the packages of pkgs/ under
# `pkgs.dotnet-purifier`: `.dotnet-purifier`, the command. A consumer that
# lists this flake in `projects` holds it as `dotnet-purifier/default`,
# which its package sets apply by default. The builder lives in the
# library, as `lib.dotnet-purifier.buildDotnetProject`.
{ closure-lib, ... }:
{
  overlay = closure-lib.caisson.nixpkgs.mkPackagesOverlay ../../pkgs "dotnet-purifier";
}
