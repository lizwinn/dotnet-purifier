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
  inherit (pkgs) lib;

  # Every fixture builds the way a consumer's package does: through
  # buildDotnetProject of a package set the overlay applies to.
  withOverlay = pkgs.extend overlay;
  inherit (withOverlay) buildDotnetProject;

  # An executable that depends on a library of the same build, a packable
  # library and an xunit test project, each with packages from nuget.org.
  multi = buildDotnetProject { src = ./fixtures/multi; };

  # Packages from a second feed of nuget.config with package source
  # mapping, from a folder feed in the build's tree and from an MSBuild SDK
  # package; a build that runs a local tool; a self-contained executable
  # for linux-x64 with the runtime pack of that platform; and an ASP.NET
  # Core executable.
  sources = buildDotnetProject { src = ./fixtures/sources; };

  # Fails unless command $1 prints $2.
  prints =
    name: command: expected:
    pkgs.runCommand name { } ''
      output="$(${command})"
      if [ "$output" != ${lib.escapeShellArg expected} ]; then
        echo "printed $output, expected "${lib.escapeShellArg expected} >&2
        exit 1
      fi
      touch "$out"
    '';
in
{
  multi-app = multi.packages.App;
  multi-greeter = multi.packages.Greeter;
  multi-tests = multi.tests."Greeter.Tests";

  # The executable runs on the .NET runtime and reaches both its project
  # reference and its NuGet dependency.
  multi-app-runs = prints "multi-app-runs" "${
    multi.packages.App.artifacts."bin/greet"
  } Ada" ''{"greeting":"Hello, Ada! You have 3 messages."}'';

  # The packable library's package lies where the lock says.
  multi-greeter-nupkg = pkgs.runCommand "multi-greeter-nupkg" { } ''
    test -f ${multi.packages.Greeter.artifacts."Example.Greeter.1.0.0.nupkg"}
    touch "$out"
  '';

  # `dotnet nuget push` takes the package Nix built, and a project that
  # depends on it restores it from where it was pushed.
  multi-greeter-publishes =
    pkgs.runCommand "multi-greeter-publishes"
      {
        nativeBuildInputs = [ pkgs.dotnetCorePackages.sdk_10_0 ];
        dontConfigureNuget = true;
      }
      ''
        export HOME="$TMPDIR/home" DOTNET_CLI_HOME="$TMPDIR/home" XDG_RUNTIME_DIR="$TMPDIR/run"
        export NUGET_PACKAGES="$TMPDIR/packages" DOTNET_NOLOGO=1 DOTNET_CLI_TELEMETRY_OPTOUT=1
        export DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE=true DOTNET_NUGET_SIGNATURE_VERIFICATION=false
        mkdir -p "$HOME" "$XDG_RUNTIME_DIR" feed consumer
        chmod 700 "$XDG_RUNTIME_DIR"
        dotnet nuget push ${
          multi.packages.Greeter.artifacts."Example.Greeter.1.0.0.nupkg"
        } --source "$PWD/feed"
        cat > consumer/Consumer.csproj <<'EOF'
        <Project Sdk="Microsoft.NET.Sdk">
          <PropertyGroup>
            <OutputType>Exe</OutputType>
            <TargetFramework>net10.0</TargetFramework>
            <UseAppHost>false</UseAppHost>
            <NuGetAudit>false</NuGetAudit>
          </PropertyGroup>
          <ItemGroup>
            <PackageReference Include="Example.Greeter" Version="1.0.0" />
          </ItemGroup>
        </Project>
        EOF
        echo 'System.Console.WriteLine(Example.Greeter.Greeting.For("Grace", 1));' > consumer/Program.cs
        dotnet build consumer -p:RestoreSources="$PWD/feed;${multi.packageFolder}" -o out
        output="$(dotnet out/Consumer.dll)"
        test "$output" = "Hello, Grace! You have 1 message."
        touch "$out"
      '';

  # Lint's build ran the local tool, from an MSBuild SDK package project.
  sources-lint = sources.packages.Lint;

  sources-web = sources.packages.Web;

  # The ASP.NET Core executable runs on the ASP.NET Core runtime.
  sources-web-runs = prints "sources-web-runs" "${
    sources.packages.Web.artifacts."bin/web"
  } --describe" "application: web";
}
// lib.optionalAttrs (pkgs.stdenv.hostPlatform.system == "x86_64-linux") {
  sources-standalone = sources.packages.Standalone;

  # The self-contained executable runs on the runtime it carries, and
  # reaches the packages of the second feed and of the folder feed.
  sources-standalone-runs =
    prints "sources-standalone-runs" (sources.packages.Standalone.artifacts."bin/standalone")
      ''{"origin":"the local folder feed"}'';
}
