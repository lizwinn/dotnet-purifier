# SPDX-License-Identifier: MIT
#
# The dotnet-purifier scope, `pkgs.dotnet-purifier`, which the default
# package overlay adds to the package set.
{ callPackage, lib, ... }:
{
  # The command that locks a .NET build's NuGet downloads and runs dotnet
  # from them.
  dotnet-purifier = callPackage ./dotnet-purifier { };

  # The builder (nix/lib/build-dotnet-project.nix), with the shared purifier
  # library of this flake, `lib.purifier`.
  buildDotnetProject = callPackage ../nix/lib/build-dotnet-project.nix {
    inherit (lib) purifier;
  };
}
