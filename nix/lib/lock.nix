# SPDX-License-Identifier: MIT
#
# Reads a dotnet-purifier.lock.json at evaluation time. The lockfile is
# what `dotnet-purifier lock` writes:
#
#   sdk         the .NET SDK the lock run used, as { version, major }
#   projects    the projects of the build, each as { name, path, kind,
#               assemblyName, targetFrameworks, frameworkReferences } and,
#               for kind "package", { packageId, packageVersion }. `kind` is
#               "exe", "package" (a packable library), "library" or "test".
#   artifacts   every NuGet package the build downloads, as a download
#               entry { path, url, hash }: `path` is the package's file name
#               in a flat local feed, `url` its flat-container URL and
#               `hash` the SRI SHA-512 of the .nupkg file
{
  lib,
  lockFile,
}:
let
  lock = lib.importJSON lockFile;
  formatVersion = 1;
in
if (lock.version or null) != formatVersion then
  throw "dotnet-purifier: ${toString lockFile} has format ${
    toString (lock.version or "unknown")
  }; this dotnet-purifier reads format ${toString formatVersion}. Run `dotnet-purifier lock --force` to write it."
else
  {
    inherit (lock) sdk projects;
    downloads = lock.artifacts;
  }
