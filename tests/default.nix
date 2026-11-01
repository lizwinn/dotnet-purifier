# SPDX-License-Identifier: MIT
#
# The flake's checks. Every one builds in the sandbox; the only network
# access is the fixed-output fetches of locked packages.
{
  pkgs,
  # The flake's default package overlay, which adds buildDotnetProject.
  overlay,
}:
let
  # Every fixture builds the way a consumer's package does: through
  # buildDotnetProject of a package set the overlay applies to.
  withOverlay = pkgs.extend overlay;
  inherit (withOverlay) buildDotnetProject;

  # An executable that depends on a library of the same build, a packable
  # library and an xunit test project, each with packages from nuget.org.
  multi = buildDotnetProject { src = ./fixtures/multi; };
in
{
  multi-app = multi.packages.App;
  multi-greeter = multi.packages.Greeter;
  multi-tests = multi.tests."Greeter.Tests";

  # The executable runs on the .NET runtime and reaches both its project
  # reference and its NuGet dependency.
  multi-app-runs = pkgs.runCommand "multi-app-runs" { } ''
    output="$(${pkgs.lib.getExe multi.packages.App} Ada)"
    expected='{"greeting":"Hello, Ada! You have 3 messages."}'
    if [ "$output" != "$expected" ]; then
      echo "greet printed $output, expected $expected" >&2
      exit 1
    fi
    touch "$out"
  '';

  # The packable library is a .nupkg named after its package ID and version.
  multi-greeter-nupkg = pkgs.runCommand "multi-greeter-nupkg" { } ''
    test -f ${multi.packages.Greeter}/Example.Greeter.1.0.0.nupkg
    touch "$out"
  '';
}
