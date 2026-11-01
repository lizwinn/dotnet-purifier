# dotnet-purifier

dotnet-purifier builds .NET projects in the Nix sandbox from a lockfile of
their NuGet downloads. You keep using the `dotnet` command and your own
project files. The lockfile, `dotnet-purifier.lock.json`, records every
package a build installs, by URL and SHA-512, and every project with the
files it produces. Nix reads it at evaluation time, so the repository needs
no generated Nix files.

## Quickstart

In the root of a .NET build (the directory with your solution, or with the
project files below it), lock the build:

```zsh
nix run github:nix-purifiers/dotnet-purifier -- lock
```

Commit `dotnet-purifier.lock.json`. Then build with Nix. The flake's
`overlays.default` adds `buildDotnetProject` to a package set, and a
package written for `callPackage` takes it by name, like any other builder:

```nix
# package.nix
{ buildDotnetProject }:
(buildDotnetProject { src = ./.; }).packages.App
```

```nix
# flake.nix
{
  inputs.dotnet-purifier.url = "github:nix-purifiers/dotnet-purifier";
  inputs.nixpkgs.follows = "dotnet-purifier/nixpkgs";

  outputs = { nixpkgs, dotnet-purifier, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        overlays = [
          dotnet-purifier.overlays.default
          (final: prev: { app = final.callPackage ./package.nix { }; })
        ];
      };
    in
    {
      packages.${system}.default = pkgs.app;
      checks.${system}.tests = (pkgs.buildDotnetProject { src = ./.; }).tests;
    };
}
```

`nix build` builds the project in `App.csproj`. Each project is named after
its project file. The builder returns

- `packages.<project>`: an executable is published under `lib/<project>`
  with a command in `bin/` named after its assembly; a packable library is
  its `.nupkg`; any other library is its build output. Each has
  `artifacts."<file>"`, the files the lock records for it, known without
  building.
- `tests`: `dotnet test` of every test project, on the runtime of each
  target framework.
- `release`: the packages of the packable projects, which `publish`
  uploads.
- `fetches`: the download entries the build fetches, for SBOM, license and
  vulnerability tools.

The builder takes `src` and these optional arguments:

- `lockFile` is the lockfile. A lockfile kept outside `src` replaces any
  in it, so a packaging flake can build a project whose author does not
  lock it.
- `fetchers` goes to the fetchers of the purifier library, for a company
  mirror or credentials.
- `dotnet-sdk`, `nativeBuildInputs`, `buildInputs`, `srcFilter` and
  `allowStaleLock` are documented in `nix/lib/build-dotnet-project.nix`.

The builder takes what it needs from the package set, such as
`dotnetCorePackages` and `stdenv`, so `buildDotnetProject.override`
replaces any of it. `lib.dotnet-purifier.readLock { src, lockFile }` reads
the lock in the shape every purifier shares.

## Development loop

Run `dotnet` through the purifier, with the locked packages:

```zsh
nix run github:nix-purifiers/dotnet-purifier -- dotnet build
nix run github:nix-purifiers/dotnet-purifier -- dotnet test
nix run github:nix-purifiers/dotnet-purifier -- dotnet run --project src/App
```

Every restore reads only the locked packages, with the locked SDK and the
runtime of every target framework of the build, so `dotnet test` runs each
framework's tests. `dotnet tool restore` and `dotnet workload restore` read
the locked packages too.

To add or upgrade a package, edit the project file, or use the SDK's own
commands with the network, then relock:

```zsh
nix run github:nix-purifiers/dotnet-purifier -- update               # every package
nix run github:nix-purifiers/dotnet-purifier -- update Newtonsoft.Json
nix run github:nix-purifiers/dotnet-purifier -- lock                 # after editing a project file
```

`update` runs `dotnet package update` on each project, refreshes any
`packages.lock.json`, and relocks. `lock` keeps the versions the project
files name. Every command takes `--lockfile <path>`.

To publish the packages Nix built:

```zsh
nix run github:nix-purifiers/dotnet-purifier -- publish --source https://api.nuget.org/v3/index.json --api-key "$NUGET_API_KEY"
```

## Working offline

```zsh
nix run github:nix-purifiers/dotnet-purifier -- prefetch
```

`prefetch` builds the locked packages, the SDK with every runtime the build
targets and the libraries a self-contained runtime loads, with
garbage-collector roots under `.dotnet-purifier/`. It installs the packages
into your NuGet package folder, restores the local tools and the
workloads, and restores every project. Afterwards `dotnet-purifier dotnet`
uses those store paths without evaluating Nix, so the development loop works
with the network cut off. When the lock changes, the next
`dotnet-purifier dotnet` prefetches again.

## Editor

After a prefetch, the packages are in your NuGet package folder and every
project is restored, so a language server that reads a project through
MSBuild (the Roslyn language server, csharp-ls or OmniSharp) works with no
network. Run it with the locked SDK, which `prefetch` links at
`.dotnet-purifier/sdk`: put `.dotnet-purifier/sdk/bin` first on its `PATH`
and set `DOTNET_ROOT` to `.dotnet-purifier/sdk/share/dotnet`. Navigation into
a package's code decompiles it locally; source files that SourceLink
downloads need the network.

## Troubleshooting

| Message | Fix |
| --- | --- |
| `… does not match the build: a project, solution, .props or .targets file … changed` | `dotnet-purifier lock`, then commit the lockfile. A Nix build can pass `allowStaleLock = true` instead. |
| `… was edited after dotnet-purifier lock wrote it` | `dotnet-purifier lock --force`. |
| `… has format …` or `… lacks …, so it is not a lockfile dotnet-purifier lock wrote` | `dotnet-purifier lock --force`. |
| `the lock does not hold <package>: run dotnet-purifier lock` | A restore asked for a package the lock does not hold; relock. |
| `… dotnet publish failed. If NuGet could not find a package …` (in a Nix build) | The same, from a Nix build; relock. |
| `… has the hash …, and the lock records …: the package changed` | A package of a folder feed in the tree changed; relock. |
| `… no longer records …, whose hash … reads from it` | A `packages.lock.json` changed; relock. |

## Supported language repositories

The lock records packages from these sources, each by URL and hash:

- **NuGet V3 feeds** of the build's `nuget.config`, with package source
  mapping. Each package's URL is its feed's flat-container URL.
- **Folder feeds inside the build's tree**, recorded by file and hash.
- **MSBuild SDK packages**, such as `Microsoft.Build.NoTargets`.
- **Local tools** of `.config/dotnet-tools.json`, which a build may run.
- **Runtime, host and targeting packs.** The lock takes them from NuGet,
  so one lock serves both x86_64-linux and aarch64-linux.
- **SDK workloads**, such as `wasm-tools`. The lock records their packs for
  both machines, and a Nix build installs them offline.
- **The folder feed inside the SDK** (`library-packs`). The lock takes
  these packages from a feed when a feed serves them, so both machines get
  the same file.
- **The lock mode of NuGet.** When a `packages.lock.json` records a package
  with the hash of its file, the lockfile reads the hash from it. NuGet
  hashes a signed package without its signature, so for signed packages the
  lockfile keeps the hash of the file. Every package on nuget.org is
  signed.

A lock run stops, naming the reason, on these:

- **Feeds outside NuGet V3**, and folder feeds outside the build's tree:
  the lock records a package by URL or by a file Nix can read.
- **Feeds that need credentials**: the lock run cannot authenticate yet. A
  Nix build can fetch through `fetchers.fetchurl`.
- **Workloads that need tools outside NuGet.** The Android workload needs
  the Android SDK and a JDK, which it finds on the machine; a build would
  have to provide them through `nativeBuildInputs`. The iOS, macOS and MAUI
  workloads need Xcode and build only on macOS.
- **Windows and macOS target frameworks.** The purifier targets Linux.

Other limits: a lock is made with the nixpkgs SDK of the major version
`global.json` names (10 when there is none), and a Nix build uses the SDK of
that major version from your nixpkgs, warning when the patch version
differs. Workload tools run interactively on NixOS with `programs.nix-ld`.

## Examples

[FluentValidation](https://github.com/FluentValidation/FluentValidation) is
a .NET validation library with a solution of packable libraries, a
benchmark executable and a test project. To work on it with the locked
packages, clone it, lock it and use the development loop:

```zsh
git clone https://github.com/FluentValidation/FluentValidation
cd FluentValidation
nix run github:nix-purifiers/dotnet-purifier -- lock
nix run github:nix-purifiers/dotnet-purifier -- dotnet test
```

To package it with Nix without changing the project, keep the lockfile in a
flake of your own, next to a package that passes it as `lockFile`:

```nix
# package.nix
{ buildDotnetProject, src }:
(buildDotnetProject {
  inherit src;
  lockFile = ./dotnet-purifier.lock.json;
}).packages.FluentValidation
```

The flake takes FluentValidation as an input and gives it to the package
as `src`:

```nix
# flake.nix
{
  inputs.dotnet-purifier.url = "github:nix-purifiers/dotnet-purifier";
  inputs.nixpkgs.follows = "dotnet-purifier/nixpkgs";
  inputs.fluentvalidation = {
    url = "github:FluentValidation/FluentValidation";
    flake = false;
  };

  outputs = { nixpkgs, dotnet-purifier, fluentvalidation, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        overlays = [
          dotnet-purifier.overlays.default
          (final: prev: {
            fluentvalidation = final.callPackage ./package.nix { src = fluentvalidation; };
          })
        ];
      };
    in
    {
      packages.${system}.default = pkgs.fluentvalidation;
      checks.${system}.tests =
        (pkgs.buildDotnetProject {
          src = fluentvalidation;
          lockFile = ./dotnet-purifier.lock.json;
        }).tests;
    };
}
```

Write the lockfile from a clone at the commit the flake pins, which
`nix flake metadata` shows for the `fluentvalidation` input:

```zsh
cd FluentValidation
git checkout <revision of the fluentvalidation input>
nix run github:nix-purifiers/dotnet-purifier -- lock --lockfile ../my-flake/dotnet-purifier.lock.json
```

`nix build` then builds the package of the main library, and
`nix flake check` runs the tests on every framework the test project
targets.

## Credits

dotnet-purifier follows the design of
[sbt-nix-backend](https://github.com/clhodapp/sbt-nix-backend): the tool
plans the build, a lock run records what it downloads, and Nix builds in
the sandbox from that lockfile. The purifier idea was inspired by nixcloud's
[cargo (libnix)](https://github.com/nixcloud/cargo).
