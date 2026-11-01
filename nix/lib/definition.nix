# SPDX-License-Identifier: MIT
#
# The hash of a build definition, as `definition_hash` in
# cli/dotnet_purifier.py computes it: the SRI SHA-256 of a text whose first
# line is the targets as JSON and whose other lines are each definition
# file's path and the hex SHA-256 of its content, separated by a tab, in
# sorted order. A definition file is a project, solution, .props or
# .targets file, a .nupkg of a folder feed, global.json, nuget.config,
# dotnet-tools.json or packages.lock.json, outside the directories builds
# write to.
{
  lib,
  # The build root.
  src,
  # The targets the lock was made for.
  targets,
}:
let
  skipped = [
    "bin"
    "obj"
    ".git"
    ".dotnet-purifier"
    ".vs"
    ".idea"
    "node_modules"
  ];
  names = [
    "global.json"
    "nuget.config"
    "dotnet-tools.json"
    "packages.lock.json"
  ];
  suffixes = [
    ".csproj"
    ".fsproj"
    ".vbproj"
    ".sln"
    ".slnx"
    ".props"
    ".targets"
    ".nupkg"
  ];
  isDefinition =
    name:
    let
      lower = lib.toLower name;
    in
    lib.elem lower names || lib.any (suffix: lib.hasSuffix suffix lower) suffixes;

  walk =
    dir: prefix:
    lib.concatLists (
      lib.mapAttrsToList (
        name: type:
        let
          relative = if prefix == "" then name else "${prefix}/${name}";
        in
        if type == "directory" then
          if lib.elem name skipped then [ ] else walk "${dir}/${name}" relative
        else if type == "regular" && isDefinition name then
          [ relative ]
        else
          [ ]
      ) (builtins.readDir dir)
    );

  files = lib.sort (a: b: a < b) (walk src "");
  text =
    lib.concatStringsSep "\n" (
      [ (builtins.toJSON targets) ]
      ++ map (relative: "${relative}\t${builtins.hashFile "sha256" "${src}/${relative}"}") files
    )
    + "\n";
in
builtins.convertHash {
  hash = builtins.hashString "sha256" text;
  hashAlgo = "sha256";
  toHashFormat = "sri";
}
