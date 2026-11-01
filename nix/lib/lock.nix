# SPDX-License-Identifier: MIT
#
# Reads a dotnet-purifier.lock.json at evaluation time. The lockfile is
# what `dotnet-purifier lock` writes:
#
#   sdk            the .NET SDK the lock run used, as { version, major }
#   tools          whether the build has local tools in
#                  .config/dotnet-tools.json
#   workloads      the SDK workloads the build installs, whose packs are
#                  among `artifacts`
#   projects       the projects of the build, each as { name, path, kind,
#                  assemblyName, targetFrameworks, frameworkReferences };
#                  `kind` is "exe", "package" (a packable library), "library"
#                  or "test". An executable also has { selfContained,
#                  runtimeIdentifier }, and a package { packageId,
#                  packageVersion }. `artifacts` lists the files the
#                  project's build produces, relative to its output, as the
#                  lock run produced them.
#   artifacts      every NuGet package the build downloads, as a download
#                  entry { path, url, mirrors, hash }: `path` is where the
#                  package lies in a NuGet package folder,
#                  <id>/<version>/<id>.<version>.nupkg, `url` its
#                  flat-container URL, `mirrors` an empty list (a feed
#                  serves a version at one URL for good) and `hash` the SRI
#                  SHA-512 of the .nupkg file
#   localPackages  every package that comes from a folder feed inside the
#                  build, as { path, file, hash }, where `file` is the
#                  package's file relative to the build root
#   sdkPackages    every package that comes from the folder feed the SDK
#                  carries (share/dotnet/library-packs), as { path, file,
#                  hash }, where `file` is the package's file in that feed
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
    inherit (lock)
      sdk
      tools
      workloads
      projects
      localPackages
      sdkPackages
      ;
    downloads = lock.artifacts;
  }
