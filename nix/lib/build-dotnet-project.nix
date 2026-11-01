# SPDX-License-Identifier: MIT
#
# buildDotnetProject: sandboxed builds of a .NET build, from the
# dotnet-purifier.lock.json at its root. The build needs no Nix files of
# its own: the lock names every NuGet package the build downloads, with
# its hash, and every project of the build with its kind.
#
# The file is a package for callPackage: the first argument set is what it
# takes from the package set, and the result is the builder. The
# dotnet-purifier overlay (`overlays.default`) adds it to the package set
# as `buildDotnetProject`, so a package written for callPackage takes it
# like any other dependency:
#
#   { buildDotnetProject }: (buildDotnetProject { src = ./.; }).packages.App
#
# and `buildDotnetProject.override` replaces what it takes from the set.
# The builder returns
#
#   dependencies     the local NuGet feed of every locked package
#   packages.<name>  project <name>, named after its project file:
#                    an executable project is published under lib/<name>,
#                    with a command in bin/ named after its assembly that
#                    runs it on the .NET runtime; a packable library is its
#                    .nupkg, the file `dotnet nuget push` uploads; any other
#                    library is its build output under lib/<name>
#   tests.<name>     test project <name>, run with `dotnet test`
#
# Every build runs the command that `dotnet-purifier lock` ran for the
# project's kind (`build_commands` in cli/dotnet_purifier.py), with the
# same MSBuild properties, so its restore asks for what the lock run
# downloaded.
{
  lib,
  stdenv,
  callPackage,
  dotnetCorePackages,
  makeWrapper,
}:
{
  # The root of the .NET build.
  src,
  # The lockfile; the default is the one dotnet-purifier writes at the
  # build root.
  lockFile ? src + "/dotnet-purifier.lock.json",
  # The .NET SDK. The default is the nixpkgs SDK of the major version the
  # lock was made with.
  dotnet-sdk ? null,
  # Tools the build needs beyond the SDK.
  nativeBuildInputs ? [ ],
  # Libraries the build links against.
  buildInputs ? [ ],
  # Replaces the fetcher of the locked packages.
  fetchurl ? null,
  # Filters the build root before it is copied into the store, as the
  # `filter` of builtins.path. The default leaves out build outputs,
  # version control, the purifier's state and Nix result links.
  srcFilter ? null,
}:
let
  lock = import ./lock.nix { inherit lib lockFile; };

  lockedSdk = dotnetCorePackages."sdk_${toString lock.sdk.major}_0";
  sdk =
    if dotnet-sdk != null then
      dotnet-sdk
    else if lockedSdk.version != lock.sdk.version then
      lib.warn "dotnet-purifier: ${toString lockFile} was made with the .NET SDK ${lock.sdk.version}, and the build uses ${lockedSdk.version}. A restore that asks for packages the lock does not hold means the lock needs renewing with the same SDK." lockedSdk
    else
      lockedSdk;

  defaultSrcFilter =
    path: type:
    let
      name = baseNameOf path;
    in
    !(
      (
        type == "directory"
        && lib.elem name [
          "bin"
          "obj"
          ".git"
          ".dotnet-purifier"
          ".vs"
        ]
      )
      || lib.hasPrefix "result" name
    );

  filteredSrc = builtins.path {
    path = src;
    name = "dotnet-source";
    filter = if srcFilter != null then srcFilter else defaultSrcFilter;
  };

  dependencies = callPackage ./dependencies.nix (
    {
      inherit (lock) downloads;
    }
    // lib.optionalAttrs (fetchurl != null) { inherit fetchurl; }
  );

  # The MSBuild properties of `BUILD_PROPERTIES` in cli/dotnet_purifier.py.
  buildProperties = [
    "-p:Configuration=Release"
    "-p:UseAppHost=false"
    "-p:NuGetAudit=false"
  ];

  # The setup shared by every build. MSBuild reads environment variables
  # as properties, so `RestoreSources` makes every restore read only the
  # feed of locked packages. Nix checked each package against the SHA-512
  # that the lock run recorded, after NuGet had verified the package's
  # signature in that run; verifying it again needs the network, for
  # revocation checks.
  dotnetSetup = ''
    export HOME="$TMPDIR/home"
    export DOTNET_CLI_HOME="$HOME"
    export XDG_RUNTIME_DIR="$TMPDIR/run"
    export NUGET_PACKAGES="$TMPDIR/nuget-packages"
    export NUGET_HTTP_CACHE_PATH="$TMPDIR/nuget-http-cache"
    export DOTNET_CLI_TELEMETRY_OPTOUT=1
    export DOTNET_NOLOGO=1
    export DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1
    export DOTNET_SKIP_WORKLOAD_INTEGRITY_CHECK=1
    export DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE=true
    export DOTNET_NUGET_SIGNATURE_VERIFICATION=false
    export MSBUILDDISABLENODEREUSE=1
    export RestoreSources=${dependencies}
    mkdir -p "$HOME" "$XDG_RUNTIME_DIR"
    chmod 700 "$XDG_RUNTIME_DIR"
  '';

  # The major version of a target framework such as net10.0 or
  # net8.0-windows.
  frameworkMajor =
    tfm:
    let
      m = builtins.match "net([0-9]+)\\.[0-9]+.*" tfm;
    in
    if m == null then
      throw "dotnet-purifier: an executable project targets ${tfm}, and dotnet-purifier runs executables only on .NET 5 and newer."
    else
      builtins.head m;

  # The runtime an executable project runs on: ASP.NET Core's when the
  # project references its shared framework, the plain .NET runtime
  # otherwise.
  runtimeOf =
    p:
    let
      major = frameworkMajor (builtins.head p.targetFrameworks);
    in
    if lib.elem "Microsoft.AspNetCore.App" p.frameworkReferences then
      dotnetCorePackages."aspnetcore_${major}_0"
    else
      dotnetCorePackages."runtime_${major}_0";

  mkBuild =
    p: attrs:
    stdenv.mkDerivation (
      {
        pname = p.name;
        version = p.packageVersion or "0";
        src = filteredSrc;
        nativeBuildInputs = [ sdk ] ++ nativeBuildInputs ++ (attrs.nativeBuildInputs or [ ]);
        inherit buildInputs;
        dontConfigure = true;
        dontBuild = true;
        passthru = {
          inherit (p) path kind assemblyName;
        };
      }
      // removeAttrs attrs [ "nativeBuildInputs" ]
    );

  mkExe =
    p:
    let
      runtime = runtimeOf p;
    in
    mkBuild p {
      nativeBuildInputs = [ makeWrapper ];
      installPhase = ''
        runHook preInstall
        ${dotnetSetup}
        dotnet publish ${lib.escapeShellArg p.path} -f ${lib.escapeShellArg (builtins.head p.targetFrameworks)} \
          ${lib.escapeShellArgs buildProperties} -o "$out/lib/${p.name}"
        makeWrapper ${lib.getExe' runtime "dotnet"} "$out/bin/${p.assemblyName}" \
          --add-flags "$out/lib/${p.name}/${p.assemblyName}.dll"
        runHook postInstall
      '';
      meta.mainProgram = p.assemblyName;
    };

  mkPackage =
    p:
    mkBuild p {
      installPhase = ''
        runHook preInstall
        ${dotnetSetup}
        dotnet pack ${lib.escapeShellArg p.path} ${lib.escapeShellArgs buildProperties} -o "$out"
        runHook postInstall
      '';
      # The packages are what NuGet publishes, byte for byte.
      dontFixup = true;
    };

  mkLibrary =
    p:
    mkBuild p {
      installPhase = ''
        runHook preInstall
        ${dotnetSetup}
        dotnet build ${lib.escapeShellArg p.path} ${lib.escapeShellArgs buildProperties} -o "$out/lib/${p.name}"
        runHook postInstall
      '';
    };

  mkTest =
    p:
    mkBuild p {
      installPhase = ''
        runHook preInstall
        ${dotnetSetup}
        dotnet test ${lib.escapeShellArg p.path} ${lib.escapeShellArgs buildProperties}
        touch "$out"
        runHook postInstall
      '';
    };

  byName = projects: lib.listToAttrs (map (p: lib.nameValuePair p.name p) projects);
  ofKind = kinds: byName (lib.filter (p: lib.elem p.kind kinds) lock.projects);
in
{
  inherit dependencies;
  packages =
    lib.mapAttrs
      (
        _: p:
        if p.kind == "exe" then
          mkExe p
        else if p.kind == "package" then
          mkPackage p
        else
          mkLibrary p
      )
      (ofKind [
        "exe"
        "package"
        "library"
      ]);
  tests = lib.mapAttrs (_: mkTest) (ofKind [ "test" ]);
}
