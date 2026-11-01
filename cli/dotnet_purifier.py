# SPDX-License-Identifier: MIT
"""dotnet-purifier: lock a .NET build's NuGet downloads, and run dotnet from them.

  dotnet-purifier lock [--force] [PROJECT_OR_SOLUTION...]
      Runs the build once with an empty NuGet package folder and records
      every package NuGet installs in dotnet-purifier.lock.json at the build
      root, with the projects the build has. The default is the one
      solution at the build root, or else every project file below it. The
      lock is renewed only when the build definition changed, unless
      --force is given.

  dotnet-purifier dotnet [ARG...]
      Runs dotnet with the locked packages installed in the NuGet package
      folder, and with every restore reading only them.

  dotnet-purifier push [ARG...]
      Builds the build's packable projects with Nix and runs
      `dotnet nuget push` on each package Nix built, with the given
      arguments (a --source and an --api-key, for example).

The package that installs this script sets the environment variables
DOTNET_PURIFIER_NIX_LIB (the Nix library directory), DOTNET_PURIFIER_NIXPKGS
(the nixpkgs the SDKs come from), DOTNET_PURIFIER_SYSTEM (the Nix system)
and DOTNET_PURIFIER_DEFAULT_SDK (the SDK major version used when the build
has no global.json).
"""

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

LOCK_NAME = "dotnet-purifier.lock.json"
FORMAT_VERSION = 1
STATE_DIR = ".dotnet-purifier"
PROJECT_SUFFIXES = (".csproj", ".fsproj", ".vbproj")
SOLUTION_SUFFIXES = (".sln", ".slnx")

# Directories that never belong to the build definition or the build input.
SKIPPED_DIRS = {"bin", "obj", ".git", STATE_DIR, ".vs", ".idea", "node_modules"}

# The files whose content decides what a restore installs. A .nupkg in the
# tree is a package of a folder feed.
DEFINITION_NAMES = {"global.json", "nuget.config", "dotnet-tools.json", "packages.lock.json"}
DEFINITION_SUFFIXES = PROJECT_SUFFIXES + SOLUTION_SUFFIXES + (".props", ".targets", ".nupkg")

# MSBuild properties every build of the purifier passes, in the lock run
# and in the Nix sandbox alike. NuGet's vulnerability audit downloads
# advisories that a sandboxed build cannot reach.
BUILD_PROPERTIES = ["-p:Configuration=Release", "-p:NuGetAudit=false"]

# A framework-dependent build that the `dotnet` command runs needs no
# apphost. A self-contained build needs one, since nothing else starts it.
NO_APPHOST = "-p:UseAppHost=false"

# The runtime identifiers of the machines a Nix build of the lockfile may
# run on. Some packs are chosen for the machine that builds, such as the
# Crossgen2 compiler of ReadyToRun builds; the lock run restores each
# project once more for each of these machines, so the lock holds their
# packs too.
BUILD_HOSTS = ["linux-x64", "linux-arm64"]


def reference_packs_only(env, directory, dotnet):
    """Points the SDK's packs at a directory with only the targeting packs
    (*.Ref) the SDK ships with. The SDK then installs every runtime and
    host pack it needs from NuGet, where the lock records it, rather than
    taking the ones a given SDK ships for the machine it runs on. The
    targeting packs hold reference assemblies, which are the same on every
    machine, and the package pruning data a restore needs. MSBuild reads
    environment variables as properties."""
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    packs = Path(dotnet).parent.parent / "share" / "dotnet" / "packs"
    for pack in sorted(packs.glob("*.Ref")):
        (directory / pack.name).symlink_to(pack)
    env["NETCoreTargetingPackRoot"] = str(directory)
    return env


def fail(message):
    print(f"dotnet-purifier: {message}", file=sys.stderr)
    sys.exit(1)


def info(message):
    print(f"dotnet-purifier: {message}", file=sys.stderr)


def build_root():
    root = Path.cwd()
    if not any(p.suffix in PROJECT_SUFFIXES + SOLUTION_SUFFIXES for p in walk(root)):
        fail(f"{root} has no project or solution files; run dotnet-purifier from the root of a .NET build")
    return root


def walk(root):
    """Every file below root, outside the skipped directories."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIPPED_DIRS)
        for name in sorted(filenames):
            yield Path(dirpath) / name


def sha512_sri(path):
    digest = hashlib.sha512()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return "sha512-" + base64.b64encode(digest.digest()).decode()


def definition_hash(root, targets):
    digest = hashlib.sha256()
    digest.update(json.dumps(targets).encode())
    for path in walk(root):
        if path.name.lower() in DEFINITION_NAMES or path.suffix.lower() in DEFINITION_SUFFIXES:
            digest.update(str(path.relative_to(root)).encode() + b"\0")
            digest.update(path.read_bytes() + b"\0")
    return "sha256-" + base64.b64encode(digest.digest()).decode()


def sdk_major(root):
    """The SDK major version that global.json names, or else the default
    that the package sets."""
    global_json = root / "global.json"
    if global_json.is_file():
        version = json.loads(global_json.read_text()).get("sdk", {}).get("version")
        if version:
            return int(version.split(".")[0])
    return int(os.environ["DOTNET_PURIFIER_DEFAULT_SDK"])


def nix_build(expr, out_link=None):
    command = ["nix", "build", "--impure", "--print-out-paths", "--expr", expr]
    command += ["--out-link", str(out_link)] if out_link else ["--no-link"]
    result = subprocess.run(command, stdout=subprocess.PIPE, text=True, check=False)
    if result.returncode != 0:
        fail("nix build failed")
    return result.stdout.split()


def nixpkgs_expr():
    nixpkgs = os.environ["DOTNET_PURIFIER_NIXPKGS"]
    system = os.environ["DOTNET_PURIFIER_SYSTEM"]
    return f'(import {nixpkgs} {{ system = "{system}"; }})'


def sdk(major):
    """The nixpkgs .NET SDK of a major version, which a Nix build of the
    lockfile uses too."""
    return nix_build(f"{nixpkgs_expr()}.dotnetCorePackages.sdk_{major}_0.out")[0]


def with_sdk_on_path(env, dotnet):
    """The environment with the SDK first on PATH, as a build that runs
    `dotnet` itself expects, and as the Nix build has it. DOTNET_ROOT names
    the SDK too: MSBuild starts task hosts through the SDK's MSBuild
    apphost, which finds the runtime only through it."""
    env["PATH"] = f"{Path(dotnet).parent}{os.pathsep}{env.get('PATH', '')}"
    env["DOTNET_ROOT"] = str(Path(dotnet).parent.parent / "share" / "dotnet")
    return env


def dotnet_env(home, dotnet):
    # .NET keeps its named mutexes under XDG_RUNTIME_DIR.
    (home / "run").mkdir(mode=0o700)
    env = with_sdk_on_path(dict(os.environ), dotnet)
    env.update(
        HOME=str(home),
        XDG_RUNTIME_DIR=str(home / "run"),
        DOTNET_CLI_HOME=str(home),
        NUGET_PACKAGES=str(home / "packages"),
        NUGET_HTTP_CACHE_PATH=str(home / "http-cache"),
        NUGET_PLUGINS_CACHE_PATH=str(home / "plugins-cache"),
        NUGET_SCRATCH=str(home / "scratch"),
        DOTNET_CLI_TELEMETRY_OPTOUT="1",
        DOTNET_NOLOGO="1",
        DOTNET_SKIP_FIRST_TIME_EXPERIENCE="1",
        DOTNET_SKIP_WORKLOAD_INTEGRITY_CHECK="1",
        # The SDK looks for workload updates on nuget.org in the background.
        # It reads this variable as true only when it says "true".
        DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE="true",
    )
    return reference_packs_only(env, home / "packs", dotnet)


def run(dotnet, args, cwd, env, capture=False):
    result = subprocess.run(
        [dotnet, *args],
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE if capture else None,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        fail(f"`dotnet {' '.join(args)}` failed")
    return result.stdout


def default_targets(root):
    solutions = sorted(p for p in root.iterdir() if p.suffix in SOLUTION_SUFFIXES)
    if len(solutions) == 1:
        return [solutions[0].name]
    return [str(p.relative_to(root)) for p in walk(root) if p.suffix in PROJECT_SUFFIXES]


def project_files(dotnet, root, targets, env):
    """The project files the targets name, relative to root."""
    projects = []
    for target in targets:
        path = root / target
        if path.suffix in SOLUTION_SUFFIXES:
            out = run(dotnet, ["sln", str(path), "list"], root, env, capture=True)
            for line in out.splitlines():
                line = line.strip().replace("\\", "/")
                if line.endswith(PROJECT_SUFFIXES):
                    projects.append(str((path.parent / line).resolve().relative_to(root.resolve())))
        elif path.suffix in PROJECT_SUFFIXES:
            projects.append(target)
        else:
            fail(f"{target} is neither a project nor a solution")
    return sorted(set(projects))


def describe(dotnet, root, project, env):
    """A project's lock record, from its evaluated MSBuild properties."""
    names = [
        "AssemblyName",
        "OutputType",
        "IsPackable",
        "IsTestProject",
        "TargetFramework",
        "TargetFrameworks",
        "PackageId",
        "PackageVersion",
        "SelfContained",
        "RuntimeIdentifier",
    ]
    out = run(
        dotnet,
        ["msbuild", project, *BUILD_PROPERTIES, *(f"-getProperty:{n}" for n in names), "-getItem:FrameworkReference"],
        root,
        env,
        capture=True,
    )
    data = json.loads(out)
    props = data["Properties"]
    if props["TargetFramework"]:
        frameworks = [props["TargetFramework"]]
    else:
        frameworks = [f for f in props["TargetFrameworks"].split(";") if f]
    if props["IsTestProject"].lower() == "true":
        kind = "test"
    elif props["OutputType"].lower() in ("exe", "winexe"):
        kind = "exe"
    elif props["IsPackable"].lower() != "false":
        kind = "package"
    else:
        kind = "library"
    references = sorted({i["Identity"] for i in data.get("Items", {}).get("FrameworkReference", [])})
    record = {
        "name": Path(project).stem,
        "path": project,
        "kind": kind,
        "assemblyName": props["AssemblyName"],
        "targetFrameworks": frameworks,
        "frameworkReferences": references,
    }
    if kind == "exe":
        record["selfContained"] = props["SelfContained"].lower() == "true"
        record["runtimeIdentifier"] = props["RuntimeIdentifier"]
    if kind == "package":
        record["packageId"] = props["PackageId"]
        record["packageVersion"] = props["PackageVersion"]
    return record


def build_commands(project):
    """The dotnet commands that build a project, in the lock run and in the
    Nix sandbox alike. nix/lib/build-dotnet-project.nix runs the same
    commands for each kind."""
    path = project["path"]
    if project["kind"] == "exe":
        framework = ["-f", project["targetFrameworks"][0]]
        apphost = [] if project["selfContained"] else [NO_APPHOST]
        return [["publish", path, *framework, *BUILD_PROPERTIES, *apphost, "-o", "{out}"]]
    if project["kind"] == "package":
        return [["pack", path, *BUILD_PROPERTIES, NO_APPHOST, "-o", "{out}"]]
    return [["build", path, *BUILD_PROPERTIES, NO_APPHOST]]


def artifacts_of(project, out):
    """The files a project's Nix build produces, relative to the build's
    output, as the lock run produced them: the packages a packable library
    packs to, the command of an executable and the assembly of any other
    library. A test project produces none."""
    name, assembly = project["name"], project["assemblyName"]
    if project["kind"] == "package":
        return sorted(p.name for p in out.iterdir() if p.suffix in (".nupkg", ".snupkg"))
    if project["kind"] == "exe" and project["runtimeIdentifier"].startswith(("browser-", "wasi-")):
        return [f"lib/{name}/wwwroot"]
    if project["kind"] == "exe":
        return [f"bin/{assembly}"]
    if project["kind"] == "library":
        return [f"lib/{name}/{assembly}.dll"]
    return []


def service_index_base(source, cache):
    """The flat-container base URL of a NuGet V3 feed."""
    if source not in cache:
        if not source.endswith("/index.json"):
            fail(f"package source {source} is not a NuGet V3 feed; dotnet-purifier fetches only from V3 feeds")
        with urllib.request.urlopen(source) as response:
            index = json.load(response)
        bases = [r["@id"] for r in index["resources"] if r["@type"].startswith("PackageBaseAddress/3.0.0")]
        if not bases:
            fail(f"{source} has no PackageBaseAddress resource")
        cache[source] = bases[0].rstrip("/") + "/"
    return cache[source]


def local_package_file(feed, package_id, version):
    """The .nupkg of a package in a folder feed, in a flat or a
    hierarchical layout."""
    name = f"{package_id}.{version}.nupkg"
    for path in sorted(feed.rglob("*.nupkg")):
        if path.name.lower() == name:
            return path
    return None


def configured_sources(dotnet, build, env):
    """The enabled package sources of the build's NuGet configuration."""
    out = run(dotnet, ["nuget", "list", "source", "--format", "Short"], build, env, capture=True)
    sources = []
    for line in out.splitlines():
        flag, _, source = line.strip().partition(" ")
        if flag.startswith("E") and source:
            sources.append(source.strip())
    return sources


def fetch_from_feeds(package_id, version, sources, bases, expected=None):
    """The URL and SRI SHA-512 of a package on the first of the http(s)
    sources that serves it, and, when `expected` is given, serves a file
    with that hash. Returns None when no source does."""
    name = f"{package_id}.{version}.nupkg"
    for source in sources:
        if not source.startswith(("https://", "http://")):
            continue
        url = f"{service_index_base(source, bases)}{package_id}/{version}/{name}"
        found = prefetch(url)
        if found is not None and (expected is None or found == expected):
            return url, found
    return None


def prefetch(url):
    """The SRI SHA-512 of the file at a URL, fetched into the store with
    the fetcher a Nix build of the lockfile uses, or None when it cannot be
    fetched."""
    result = subprocess.run(
        ["nix", "store", "prefetch-file", "--json", "--hash-type", "sha512", url],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)["hash"]


def find_on_feeds(package_id, version, nupkg, sources, bases):
    """The URL of a package on the first of the sources that serves the
    same file, for a package NuGet installed without recording its feed.
    `dotnet tool restore` installs packages that way."""
    found = fetch_from_feeds(package_id, version, sources, bases, expected=sha512_sri(nupkg))
    if found is None:
        fail(f"{package_id} {version} was installed without its feed, and no configured feed serves the same file")
    return found[0]


def installed_workloads(dotnet, build, env):
    """The workloads installed in the lock run's .NET home."""
    out = run(dotnet, ["workload", "list", "--machine-readable"], build, env, capture=True)
    for line in reversed(out.splitlines()):
        if line.startswith("{"):
            return sorted(json.loads(line)["installed"])
    fail("`dotnet workload list --machine-readable` printed no JSON")


def read_manifest_json(text):
    """Parses a workload manifest. The SDK reads manifests as JSON with
    comments and trailing commas, so they are removed first, outside
    strings."""
    tokens = re.finditer(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/|,(?=\s*[\]}])', text, re.DOTALL)
    kept, last = [], 0
    for token in tokens:
        if not token.group().startswith('"'):
            kept.append(text[last : token.start()])
            last = token.end()
    kept.append(text[last:])
    return json.loads("".join(kept))


def workload_packs(dotnet, workloads):
    """The packs that installing the workloads installs, on every machine
    of BUILD_HOSTS, as (id, version) pairs in lower case. They come from
    the workload manifests the SDK ships with, which a workload install
    with --skip-manifest-update reads: a workload names its packs and the
    workloads it extends, and a pack names its version and, when it
    differs by machine, the package for each machine (`alias-to`)."""
    manifests = Path(dotnet).parent.parent / "share" / "dotnet" / "sdk-manifests"
    definitions, packs = {}, {}
    for path in sorted(manifests.glob("*/*/*/WorkloadManifest.json")) + sorted(manifests.glob("*/*/WorkloadManifest.json")):
        manifest = read_manifest_json(path.read_text())
        definitions.update(manifest.get("workloads", {}))
        packs.update(manifest.get("packs", {}))
    names, seen, pending = set(), set(), list(workloads)
    while pending:
        workload = pending.pop()
        if workload in seen:
            continue
        seen.add(workload)
        definition = definitions[workload]
        names.update(definition.get("packs", []))
        pending.extend(definition.get("extends", []))
    result = set()
    for name in names:
        pack = packs[name]
        alias = pack.get("alias-to")
        for host in BUILD_HOSTS:
            package_id = alias.get(host, alias.get("any")) if alias else name
            # A pack with no package for a machine does not apply there,
            # as the Python of Emscripten does not on Linux.
            if package_id:
                result.add((package_id.lower(), pack["version"].lower()))
    return sorted(result)


def workload_artifacts(dotnet, workloads, sources, bases, known):
    """The download entries of the workloads' packs that are not among the
    known paths."""
    artifacts = []
    for package_id, version in workload_packs(dotnet, workloads):
        path = f"{package_id}/{version}/{package_id}.{version}.nupkg"
        if path in known:
            continue
        found = fetch_from_feeds(package_id, version, sources, bases)
        if found is None:
            fail(f"no configured feed serves the workload pack {package_id} {version}")
        artifacts.append({"path": path, "url": found[0], "mirrors": [], "hash": found[1]})
    return artifacts


def installed_packages(packages_dir, build, sources, dotnet):
    """Every package of a NuGet package folder: the downloaded ones as
    download entries { path, url, mirrors, hash }, those from folder feeds
    inside the build as { path, file, hash }, where `file` is the package's
    file in the build, and those from the folder feed the SDK carries
    (library-packs) as { path, file, hash }, where `file` is the package's
    file in that feed. `path` is where the package lies in a NuGet package
    folder."""
    bases = {}
    artifacts = []
    local = []
    sdk_packages = []
    library_packs = (Path(dotnet).parent.parent / "share" / "dotnet" / "library-packs").resolve()
    for metadata in sorted(packages_dir.glob("*/*/.nupkg.metadata")):
        version_dir = metadata.parent
        package_id = version_dir.parent.name
        version = version_dir.name
        nupkg = version_dir / f"{package_id}.{version}.nupkg"
        path = f"{package_id}/{version}/{nupkg.name}"
        source = json.loads(metadata.read_text()).get("source")
        if not source or source.startswith(("https://", "http://")):
            if source:
                url = f"{service_index_base(source, bases)}{package_id}/{version}/{nupkg.name}"
            else:
                url = find_on_feeds(package_id, version, nupkg, sources, bases)
            # A NuGet feed serves a package version at one URL for good, so
            # the entry lists no mirrors.
            artifacts.append({"path": path, "url": url, "mirrors": [], "hash": sha512_sri(nupkg)})
            continue
        feed = Path(source).resolve()
        if feed == library_packs or feed.name == "library-packs" and (feed.parent / "sdk").is_dir():
            file = local_package_file(feed, package_id, version)
            if file is None:
                fail(f"{package_id} {version} is not in the SDK's feed {source}")
            sdk_packages.append({"path": path, "file": file.name, "hash": sha512_sri(nupkg)})
            continue
        if not feed.is_relative_to(build.resolve()):
            fail(
                f"{package_id} {version} comes from the folder {source}, outside the build; "
                "dotnet-purifier takes packages from folder feeds only inside the build's tree"
            )
        file = local_package_file(feed, package_id, version)
        if file is None:
            fail(f"{package_id} {version} is not in its folder feed {source}")
        local.append({"path": path, "file": str(file.relative_to(build.resolve())), "hash": sha512_sri(nupkg)})
    return artifacts, local, sdk_packages


def lock(args):
    force = "--force" in args
    targets = [a for a in args if a != "--force"]
    root = build_root()
    targets = targets or default_targets(root)
    lock_path = root / LOCK_NAME
    def_hash = definition_hash(root, targets)
    if not force and lock_path.is_file():
        old = json.loads(lock_path.read_text())
        if old.get("version") == FORMAT_VERSION and old.get("definitionHash") == def_hash:
            info(f"{LOCK_NAME} is up to date")
            return

    major = sdk_major(root)
    dotnet = f"{sdk(major)}/bin/dotnet"
    tools = (root / ".config" / "dotnet-tools.json").is_file()

    with tempfile.TemporaryDirectory(prefix="dotnet-purifier-") as tmp:
        tmp = Path(tmp)
        build = tmp / "build"
        shutil.copytree(root, build, ignore=lambda _d, names: [n for n in names if n in SKIPPED_DIRS])
        home = tmp / "home"
        home.mkdir()
        env = dotnet_env(home, dotnet)

        sdk_version = run(dotnet, ["--version"], build, env, capture=True).strip()
        info(f"locking with the .NET SDK {sdk_version}")
        # A build may run the local tools, and needs its workloads, so both
        # are installed first.
        if tools:
            run(dotnet, ["tool", "restore"], build, env)
        for target in targets:
            run(dotnet, ["workload", "restore", target, "--skip-manifest-update"], build, env)
        workloads = installed_workloads(dotnet, build, env)
        projects = [describe(dotnet, build, p, env) for p in project_files(dotnet, build, targets, env)]
        if not projects:
            fail("the build has no projects")
        if len({p["name"] for p in projects}) != len(projects):
            fail("two projects share a file name; dotnet-purifier names packages after project files")

        for project in projects:
            out = tmp / "out" / project["name"]
            for command in build_commands(project):
                run(dotnet, [a.replace("{out}", str(out)) for a in command], build, env)
            project["artifacts"] = artifacts_of(project, out)
            restore = [a for a in build_commands(project)[0][1:] if a not in ("-o", "{out}")]
            if "-f" in restore:
                i = restore.index("-f")
                del restore[i : i + 2]
            for host in BUILD_HOSTS:
                run(dotnet, ["restore", *restore, f"-p:NETCoreSdkRuntimeIdentifier={host}"], build, env)

        sources = configured_sources(dotnet, build, env)
        bases = {}
        artifacts, local, sdk_packages = installed_packages(home / "packages", build, sources, dotnet)
        known = {a["path"] for a in artifacts}
        artifacts += workload_artifacts(dotnet, workloads, sources, bases, known)
        artifacts.sort(key=lambda a: a["path"])

    data = {
        "version": FORMAT_VERSION,
        "sdk": {"version": sdk_version, "major": major},
        "targets": targets,
        "definitionHash": def_hash,
        "tools": tools,
        "workloads": workloads,
        "projects": projects,
        "artifacts": artifacts,
        "localPackages": local,
        "sdkPackages": sdk_packages,
    }
    lock_path.write_text(json.dumps(data, indent=2) + "\n")
    info(f"wrote {LOCK_NAME}: {len(projects)} projects, {len(artifacts) + len(local) + len(sdk_packages)} packages")


def read_lock(root):
    lock_path = root / LOCK_NAME
    if not lock_path.is_file():
        fail(f"{root} has no {LOCK_NAME}; run `dotnet-purifier lock` first")
    data = json.loads(lock_path.read_text())
    if data.get("version") != FORMAT_VERSION:
        fail(f"{LOCK_NAME} has format {data.get('version')}; run `dotnet-purifier lock --force`")
    if data.get("definitionHash") != definition_hash(root, data["targets"]):
        info(f"the build definition changed since {LOCK_NAME} was made; run `dotnet-purifier lock`")
    return data


def interactive_expr(root, attribute):
    lib = os.environ["DOTNET_PURIFIER_NIX_LIB"]
    return (
        f"(import {lib}/interactive.nix {{ pkgs = {nixpkgs_expr()}; "
        f"src = {root.resolve()}; }}).{attribute}"
    )


def user_package_folder(env):
    if env.get("NUGET_PACKAGES"):
        return Path(env["NUGET_PACKAGES"])
    return Path(env.get("DOTNET_CLI_HOME") or Path.home()) / ".nuget" / "packages"


def install(package_folder, target):
    """Copies the packages of a Nix-built package folder that the target
    folder lacks into it, the way NuGet installs them."""
    for version_dir in sorted(Path(package_folder).glob("*/*")):
        dest = target / version_dir.relative_to(package_folder)
        if not (dest / ".nupkg.metadata").exists():
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(version_dir, dest, symlinks=False, copy_function=shutil.copyfile)


def locked_config(path, package_folder):
    """Writes a NuGet configuration whose only source is the locked
    packages, for `dotnet tool restore`, which reads the sources of a
    configuration rather than `RestoreSources`."""
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<configuration>\n"
        "  <packageSources>\n"
        "    <clear />\n"
        f'    <add key="dotnet-purifier" value="{package_folder}" />\n'
        "  </packageSources>\n"
        "  <packageSourceMapping>\n"
        "    <clear />\n"
        "  </packageSourceMapping>\n"
        "</configuration>\n"
    )
    return path


def run_dotnet(args):
    root = build_root()
    data = read_lock(root)
    state = root / STATE_DIR
    state.mkdir(exist_ok=True)
    package_folder = nix_build(interactive_expr(root, "packageFolder"), out_link=state / "packages")[0]
    env = dict(os.environ)
    install(package_folder, user_package_folder(env))
    # MSBuild reads environment variables as properties, so every restore
    # that dotnet runs, explicit or implicit, reads only the locked
    # packages; NuGet takes a package folder as a feed.
    env["RestoreSources"] = package_folder
    env["NuGetAudit"] = "false"
    # Nix checked every package against the SHA-512 that the lock run
    # recorded, after NuGet had verified the package's signature in that
    # run. Verifying the signature again needs the network, for revocation
    # checks.
    env["DOTNET_NUGET_SIGNATURE_VERIFICATION"] = "false"
    env["DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE"] = "true"
    dotnet = f"{sdk(data['sdk']['major'])}/bin/dotnet"
    reference_packs_only(env, state / "packs", dotnet)
    # A self-contained build runs on the runtime NuGet installed, which
    # loads ICU and OpenSSL from the system; the Nix build's wrapper gives
    # it the same libraries.
    libraries = nix_build(interactive_expr(root, "runtimeLibraries"), out_link=state / "runtime-libraries")[0]
    env["LD_LIBRARY_PATH"] = os.pathsep.join(filter(None, [f"{libraries}/lib", env.get("LD_LIBRARY_PATH")]))
    # A workload install reads its packs from the locked packages, with the
    # manifests the SDK ships with, as in the lock run.
    if args[:2] in (["workload", "restore"], ["workload", "install"]) and "--source" not in args:
        args = [*args, "--skip-manifest-update", "--source", package_folder]
    if args[:2] == ["tool", "restore"] and "--configfile" not in args:
        args = [*args, "--configfile", str(locked_config(state / "nuget.config", package_folder))]
    os.execve(dotnet, ["dotnet", *args], with_sdk_on_path(env, dotnet))


def push(args):
    root = build_root()
    data = read_lock(root)
    packages = [p["name"] for p in data["projects"] if p["kind"] == "package"]
    if not packages:
        fail("the build has no packable projects")
    dotnet = f"{sdk(data['sdk']['major'])}/bin/dotnet"
    outputs = nix_build(interactive_expr(root, "nupkgs"))
    for out in outputs:
        for nupkg in sorted(Path(out).glob("*.nupkg")):
            info(f"pushing {nupkg.name}")
            result = subprocess.run([dotnet, "nuget", "push", str(nupkg), *args], check=False)
            if result.returncode != 0:
                fail(f"pushing {nupkg.name} failed")


def main():
    commands = {"lock": lock, "dotnet": run_dotnet, "push": push}
    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    commands[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
