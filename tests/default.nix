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

  # A WebAssembly executable relinked with Emscripten, from the packs of
  # the wasm-tools workload.
  workloads = buildDotnetProject { src = ./fixtures/workloads; };

  # Variants of the multi fixture's lockfile, each with one common mistake.
  multiLock = lib.importJSON ./fixtures/multi/dotnet-purifier.lock.json;
  sourcesLock = lib.importJSON ./fixtures/sources/dotnet-purifier.lock.json;
  lockWith =
    name: lock: changes:
    builtins.toFile "${name}.lock.json" (builtins.toJSON (lock // changes));
  multiWith =
    name: changes: args:
    buildDotnetProject (
      {
        src = ./fixtures/multi;
        lockFile = lockWith name multiLock changes;
      }
      // args
    );

  # The build definition changed since the lock was made.
  stale = multiWith "stale" {
    definitionHash = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
  } { };
  staleAllowed = multiWith "stale" {
    definitionHash = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
  } { allowStaleLock = true; };
  # A lockfile of another format, and one that lacks a field.
  otherFormat = multiWith "other-format" { version = 2; } { };
  malformed = buildDotnetProject {
    src = ./fixtures/multi;
    lockFile = builtins.toFile "malformed.lock.json" (
      builtins.toJSON (removeAttrs multiLock [ "artifacts" ])
    );
  };
  # The lock lacks a package the build needs: Humanizer.Core, which the
  # Greeter library references.
  missing = multiWith "missing" {
    artifacts = lib.filter (a: !(lib.hasPrefix "humanizer.core/" a.path)) multiLock.artifacts;
  } { };
  # The package of the folder feed in the build differs from the one the
  # lock records.
  tampered = buildDotnetProject {
    src = ./fixtures/sources;
    lockFile = lockWith "tampered" sourcesLock {
      localPackages = map (
        p: p // { hash = (builtins.head multiLock.artifacts).hash; }
      ) sourcesLock.localPackages;
    };
  };

  # True when evaluating the package names of a build fails, as a throw.
  evaluationFails =
    build: !(builtins.tryEval (builtins.deepSeq (builtins.attrNames build.packages) true)).success;

  # A check that passes when `condition` holds and fails evaluation with
  # `message` otherwise.
  expect =
    name: condition: message:
    if condition then pkgs.runCommand name { } "touch $out" else throw "${name}: ${message}";

  # A check that the build of `drv` fails, with `text` in its log.
  failsWith =
    name: drv: text:
    pkgs.runCommand name { failure = pkgs.testers.testBuildFailure drv; } ''
      grep -F ${lib.escapeShellArg text} "$failure/testBuildFailure.log"
      touch "$out"
    '';

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
  # The lock tool's parsing and path logic.
  unit-tests =
    pkgs.runCommand "unit-tests"
      {
        nativeBuildInputs = [ pkgs.python3 ];
        DOTNET_PURIFIER_CLI = ../cli/dotnet_purifier.py;
      }
      ''
        python3 -m unittest discover -s ${./unit} -v
        touch "$out"
      '';

  # A stale lock fails evaluation, unless the build allows it.
  stale-lock-fails = expect "stale-lock-fails" (evaluationFails stale) "a stale lock evaluated";
  stale-lock-allowed = staleAllowed.packages.App;

  # A lockfile of another format or without a field fails evaluation.
  other-format-fails =
    expect "other-format-fails" (evaluationFails otherFormat)
      "a lockfile of format 2 evaluated";
  malformed-lock-fails =
    expect "malformed-lock-fails" (evaluationFails malformed)
      "a lockfile without artifacts evaluated";

  # A package missing from the lock fails the build, which says what to run.
  missing-package-fails =
    failsWith "missing-package-fails" missing.packages.Greeter
      "does not hold: run `dotnet-purifier lock`";

  # A package of a folder feed that changed since the lock fails the build.
  tampered-package-fails =
    failsWith "tampered-package-fails" tampered.packageFolder
      "the package changed since `dotnet-purifier lock` ran";

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

  workloads-wasm = workloads.packages.Wasm;

  # The workload's Emscripten relinked the native runtime: the build
  # produced its own dotnet.native.wasm rather than taking the one in the
  # runtime pack.
  workloads-wasm-relinked = pkgs.runCommand "workloads-wasm-relinked" { } ''
    framework=${workloads.packages.Wasm.artifacts."lib/Wasm/wwwroot"}/_framework
    ls "$framework"/dotnet.native*.wasm
    touch "$out"
  '';

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
