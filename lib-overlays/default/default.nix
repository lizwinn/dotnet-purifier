# SPDX-License-Identifier: MIT
#
# The `dotnet-purifier` library namespace, which holds the builder and the
# lock reader of the purifier contract:
#
#   lib.dotnet-purifier.buildDotnetProject { pkgs, src, fetchers ? { }, ... }
#   lib.dotnet-purifier.readLock { src, lockFile ? src + "/dotnet-purifier.lock.json" }
#
# It calls nix/lib/build-dotnet-project.nix with `pkgs.callPackage` and the
# shared purifier library, `lib.purifier`, and passes it the other
# arguments.
{ ... }:
{

  overlay = final: prev: {
    dotnet-purifier = (prev.dotnet-purifier or { }) // {
      # The lock in memory of the purifier contract, from a build root and
      # its dotnet-purifier.lock.json (nix/lib/lock.nix).
      readLock = args: import ../../nix/lib/lock.nix ({ lib = final; } // args);
      buildDotnetProject =
        args:
        args.pkgs.callPackage ../../nix/lib/build-dotnet-project.nix { inherit (final) purifier; } (
          removeAttrs args [ "pkgs" ]
        );
    };
  };

}
