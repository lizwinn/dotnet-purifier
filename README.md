# dotnet-purifier

dotnet-purifier builds .NET projects in the Nix sandbox from a lockfile of
their NuGet downloads. You keep using the `dotnet` command and your own
project files. The lockfile, `dotnet-purifier.lock.json`, records every
package a build installs, by URL and SHA-512, and every project with the
files it produces. Nix reads that lockfile at evaluation time, so the
repository needs no generated Nix files.

## Quickstart

In the root of a .NET build (the directory with your solution, or with the
project files below it), lock the build:

```zsh
nix run github:nix-purifiers/dotnet-purifier -- lock
```

Commit `dotnet-purifier.lock.json`. Then build any project with Nix. The
flake's `overlays.default` adds `buildDotnetProject` to a package set:

```nix
{
  inputs.dotnet-purifier.url = "github:nix-purifiers/dotnet-purifier";
  inputs.nixpkgs.follows = "dotnet-purifier/nixpkgs";

  outputs = { nixpkgs, dotnet-purifier, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        overlays = [ dotnet-purifier.overlays.default ];
      };
      build = pkgs.buildDotnetProject { src = ./.; };
    in
    {
      packages.${system} = build.packages;
      checks.${system} = build.tests;
    };
}
```

`nix build '.#App'` builds the project in `App.csproj`. A project's package
is named after its project file.

To work offline with the locked packages, run `dotnet` through the
purifier:

```zsh
nix run github:nix-purifiers/dotnet-purifier -- dotnet build
nix run github:nix-purifiers/dotnet-purifier -- dotnet test
```

## Commands

`dotnet-purifier lock [--force] [PROJECT_OR_SOLUTION...]` copies the build
to a temporary directory and runs it with an empty NuGet package folder and
the network on. It installs the local tools and the SDK workloads the build
needs, builds each project with the command its Nix build runs, and records
everything NuGet installed. The default target is the one solution at the
build root, or else every project file below it. `lock` renews the
lockfile only when the build definition changed: a project, solution,
`.props` or `.targets` file, `global.json`, a NuGet configuration, the tool
manifest or a package of a folder feed in the tree. `--force` renews it
regardless.

`dotnet-purifier dotnet [ARG...]` runs `dotnet` with the locked packages.
It builds the locked packages into a NuGet package folder with Nix, installs
any your package folder lacks, and makes every restore read only them. It
runs the locked SDK with the runtime of every target framework of the
build, so `dotnet test` runs each framework's tests. `dotnet tool restore`
and `dotnet workload restore` read the locked packages too. A restore that
asks for a package the lock does not hold fails, and NuGet's error names
the package; run `dotnet-purifier lock` to add it. We recommend running
`dotnet-purifier dotnet restore` once after each lock: the packages are
then in your NuGet package folder, and an editor or language server that
restores through MSBuild finds them without the network.

`dotnet-purifier push [ARG...]` builds the packable projects with Nix and
runs `dotnet nuget push` on each package Nix built, with your arguments:

```zsh
nix run github:nix-purifiers/dotnet-purifier -- push --source https://api.nuget.org/v3/index.json --api-key "$NUGET_API_KEY"
```

## What a Nix build produces

`buildDotnetProject { src = ./.; }` returns

- `packages.<project>` for each executable, packable library and other
  library. An executable is published under `lib/<project>`, with a command
  in `bin/` named after its assembly: a framework-dependent executable runs
  on the nixpkgs .NET runtime of its framework (the ASP.NET Core runtime
  when it references that framework), and a self-contained one on the
  runtime it carries. A WebAssembly executable is its `wwwroot`. A packable
  library is its `.nupkg`, the file `dotnet nuget push` uploads. Any other
  library is its build output.
- `packages.<project>.artifacts."<file>"` for each file the lock records for
  the project, a path into its output that is known without building.
- `tests.<project>` for each test project, which runs `dotnet test`.
- `packageFolder`, the locked packages installed in a NuGet package folder,
  and `dependencies`, the downloads as fetched.

The arguments are documented in `nix/lib/build-dotnet-project.nix`: `src`,
`lockFile`, `dotnet-sdk`, `nativeBuildInputs`, `buildInputs`, `fetchurl`,
`srcFilter` and `allowStaleLock`.

## Where packages come from

Every source a .NET build installs packages from is locked:

- **NuGet V3 feeds** of the build's `nuget.config`, with package source
  mapping. A package's URL is its feed's flat-container URL.
- **Folder feeds inside the build's tree**, recorded by file and hash.
- **MSBuild SDK packages**, such as `Microsoft.Build.NoTargets`.
- **Local tools** of `.config/dotnet-tools.json`, which a build may run.
- **Runtime, host and targeting packs**. The SDK takes every runtime and
  host pack from NuGet, not from the packs a given SDK ships for the
  machine it runs on, so one lock serves both x86_64-linux and
  aarch64-linux. Packs that depend on the machine that builds, such as
  Crossgen2, are locked for both.
- **SDK workloads**, such as `wasm-tools`. Their packs are NuGet packages,
  locked for both machines from the workload manifests the SDK ships with.
  A Nix build installs them offline and patches their native tools.
- **The SDK's own folder feed** (`library-packs`), recorded by file and
  hash and taken from the SDK.

## How it works

The lock run and the Nix build run the same `dotnet` command for each kind
of project: `publish` for an executable, `pack` for a packable library and
`build` for anything else, with `-p:Configuration=Release -p:NuGetAudit=false`
and, for a framework-dependent build, `-p:UseAppHost=false`. A Nix build
starts with every locked package installed in its NuGet package folder,
because the parts of the SDK that resolve MSBuild SDKs and install tools
look there before any feed. Its restores read only the locked packages, and
it keeps the build's own `nuget.config`, so the build in the sandbox is the
build that was locked.

The lock records the build definition's hash, and Nix recomputes it at
evaluation time. A stale lock fails evaluation with the command to run,
unless the build passes `allowStaleLock = true`. A package from a folder
feed or from the SDK is checked against its locked hash, as Nix checks every
download.

## Limitations

- **The SDK.** A lock is made with the nixpkgs SDK of the major version
  that `global.json` names (10 when there is none), and Nix builds with the
  SDK of the same major version from your nixpkgs. When the patch version
  differs, evaluation warns, and a restore that asks for a pack of the other
  version fails until you relock with the same nixpkgs.
- **Linux only.** The purifier targets x86_64-linux and aarch64-linux.
  Projects that target Windows or macOS frameworks do not build.
- **Workloads that need tools outside NuGet.** The Android workload needs
  the Android SDK and a JDK, which it finds on the machine rather than on a
  NuGet feed, so the lock cannot hold them; a build would have to provide
  them through `nativeBuildInputs`. The iOS, macOS and MAUI workloads need
  Xcode and build only on macOS.
- **Interactive workload tools on NixOS.** Workload packs carry native tools
  built for a conventional Linux. A Nix build patches them; interactive use
  on NixOS needs `programs.nix-ld`.
- **Feeds.** Packages come only from NuGet V3 feeds and from folder feeds
  inside the build. Feeds that need credentials are not supported yet.

## Projects tried

- The fixtures in `tests/fixtures`: `multi` (an executable, a
  multi-targeted packable library and an xunit project), `sources` (every
  package source, a self-contained executable, an ASP.NET Core executable
  and a build that runs a local tool) and `workloads` (a WebAssembly
  executable relinked with Emscripten).

## Development

`nix flake check` runs the unit tests of the lock tool, builds every
fixture, and checks that each common mistake fails with an error that says
what to do: a stale lock, a lockfile of another format or without a field,
a package missing from the lock, and a package of a folder feed that
changed. CI also relocks every fixture and checks that the lockfile comes
out byte for byte the same.

## Credits

dotnet-purifier follows the design of
[sbt-nix-backend](https://github.com/clhodapp/sbt-nix-backend): the tool
plans the build, a lock run records what it downloads, and Nix builds in
the sandbox from that lockfile. The purifier idea was inspired by nixcloud's
[cargo (libnix)](https://github.com/nixcloud/cargo).
