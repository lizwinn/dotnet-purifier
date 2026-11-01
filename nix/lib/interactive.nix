# SPDX-License-Identifier: MIT
#
# The local feed that `dotnet-purifier dotnet` restores from: the locked
# packages of a lockfile, built with the package set the command was built
# with.
{
  pkgs,
  lockFile,
}:
let
  lock = import ./lock.nix {
    inherit (pkgs) lib;
    inherit lockFile;
  };
in
pkgs.callPackage ./dependencies.nix { inherit (lock) downloads; }
