# SPDX-License-Identifier: MIT
{ callPackage, ... }:
{
  # The command that locks a .NET build's NuGet downloads and runs dotnet
  # from them.
  dotnet-purifier = callPackage ./dotnet-purifier { };
}
