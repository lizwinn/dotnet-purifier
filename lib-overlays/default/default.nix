# SPDX-License-Identifier: MIT
#
# The `dotnet-purifier` library namespace. It holds nothing: the builder
# needs a package set, so it is the package `buildDotnetProject` that the
# default package overlay adds.
{ ... }:
{

  overlay = final: prev: {
    dotnet-purifier = prev.dotnet-purifier or { };
  };

}
