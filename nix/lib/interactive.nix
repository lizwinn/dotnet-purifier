# SPDX-License-Identifier: MIT
#
# What the dotnet-purifier command builds for a .NET build at `src`, with
# the package set the command was built with: `packageFolder`, the locked
# packages that `dotnet-purifier dotnet` installs and restores from; `sdk`,
# the locked SDK with the runtime of every target framework, which it runs;
# `runtimeLibraries`, the libraries a self-contained .NET runtime loads at
# run time, which `dotnet-purifier dotnet` puts on LD_LIBRARY_PATH as the
# Nix build's wrapper does; and `nupkgs`, the packages of the build's
# packable projects that `dotnet-purifier push` uploads.
{
  pkgs,
  # The shared purifier library, `lib.purifier` of the dotnet-purifier
  # flake.
  purifier,
  src,
}:
let
  build = pkgs.callPackage ./build-dotnet-project.nix { inherit purifier; } { inherit src; };
in
{
  inherit (build) packageFolder sdk;
  runtimeLibraries = pkgs.symlinkJoin {
    name = "dotnet-purifier-runtime-libraries";
    paths = map pkgs.lib.getLib build.runtimeLibraries;
  };
  nupkgs = pkgs.symlinkJoin {
    name = "dotnet-purifier-nupkgs";
    paths = builtins.attrValues (pkgs.lib.filterAttrs (_: p: p.kind == "package") build.packages);
  };
}
