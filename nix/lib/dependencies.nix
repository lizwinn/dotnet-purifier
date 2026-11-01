# SPDX-License-Identifier: MIT
#
# The fetched downloads of a lock, laid out as a tree: each download's
# file lies at its `path`, as a link to its fixed-output fetch. For NuGet
# the tree is a flat local feed, one .nupkg file per package, which a
# restore reads as a package source.
#
# This file knows nothing of NuGet: it takes file download entries of the
# shape { path, url, hash } and nothing else.
{
  lib,
  runCommand,
  fetchurl,
  # The download entries.
  downloads,
  # The name of the tree in the store.
  name ? "dotnet-purifier-feed",
}:
let
  # A store name allows fewer characters than a URL; the last segment of
  # the URL, with the rest replaced, keeps the store listing readable.
  storeName =
    url:
    let
      base = lib.last (lib.splitString "/" (builtins.head (lib.splitString "?" url)));
      clean = builtins.replaceStrings [ "%" "@" "~" "!" ] [ "_" "_" "_" "_" ] base;
    in
    if clean == "" then "download" else clean;

  fetch =
    d:
    fetchurl {
      inherit (d) url hash;
      name = storeName d.url;
    };
in
runCommand name
  {
    # The tree holds only links.
    preferLocalBuild = true;
    allowSubstitutes = false;
    passAsFile = [ "links" ];
    links = lib.concatMapStrings (d: "${d.path}\t${fetch d}\n") downloads;
  }
  ''
    mkdir -p "$out"
    while IFS=$'\t' read -r path target; do
      mkdir -p "$out/$(dirname "$path")"
      ln -s "$target" "$out/$path"
    done < "$linksPath"
  ''
