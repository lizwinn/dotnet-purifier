# SPDX-License-Identifier: MIT
"""dotnet-purifier: lock a .NET build's NuGet downloads, and run dotnet from them.

  dotnet-purifier lock [--force] [PROJECT_OR_SOLUTION...]
      Runs the build once with an empty NuGet package folder and records
      every package NuGet downloads in dotnet-purifier.lock.json at the
      build root, with the projects the build has. The default is the one
      solution at the build root, or else every project file below it. The
      lock is renewed only when the build definition changed, unless
      --force is given.

  dotnet-purifier dotnet [ARG...]
      Runs dotnet with every restore reading the locked packages, which
      Nix fetches into a local feed under .dotnet-purifier/ at the build
      root.

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

# The files whose content decides what a restore downloads.
DEFINITION_NAMES = {"global.json", "nuget.config", "dotnet-tools.json", "packages.lock.json"}
DEFINITION_SUFFIXES = PROJECT_SUFFIXES + SOLUTION_SUFFIXES + (".props", ".targets")

# MSBuild properties every build of the purifier passes, in the lock run
# and in the Nix sandbox alike. A framework-dependent build that the
# `dotnet` command runs needs no apphost, and NuGet's vulnerability audit
# downloads advisories that a sandboxed build cannot reach.
BUILD_PROPERTIES = ["-p:Configuration=Release", "-p:UseAppHost=false", "-p:NuGetAudit=false"]


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


def sri(algorithm, path):
    digest = hashlib.new(algorithm)
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return f"{algorithm}-" + base64.b64encode(digest.digest()).decode()


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
    return result.stdout.split()[0]


def nixpkgs_expr():
    nixpkgs = os.environ["DOTNET_PURIFIER_NIXPKGS"]
    system = os.environ["DOTNET_PURIFIER_SYSTEM"]
    return f'(import {nixpkgs} {{ system = "{system}"; }})'


def sdk(major):
    """The nixpkgs .NET SDK of a major version, which a Nix build of the
    lockfile uses too."""
    return nix_build(f"{nixpkgs_expr()}.dotnetCorePackages.sdk_{major}_0.out")


def dotnet_env(home):
    # .NET keeps its named mutexes under XDG_RUNTIME_DIR.
    (home / "run").mkdir(mode=0o700)
    env = dict(os.environ)
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
        DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE="true",
    )
    return env


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
    if kind == "package":
        record["packageId"] = props["PackageId"]
        record["packageVersion"] = props["PackageVersion"]
    return record


def build_commands(project):
    """The dotnet commands that build a project, in the lock run and in the
    Nix sandbox alike. nix/lib/build-dotnet-project.nix runs the same
    commands for each kind."""
    path = project["path"]
    framework = ["-f", project["targetFrameworks"][0]]
    if project["kind"] == "exe":
        return [["publish", path, *framework, *BUILD_PROPERTIES, "-o", "{out}"]]
    if project["kind"] == "package":
        return [["pack", path, *BUILD_PROPERTIES, "-o", "{out}"]]
    return [["build", path, *BUILD_PROPERTIES]]


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


def locked_packages(packages_dir):
    """Every package of a NuGet package folder, as a download entry."""
    bases = {}
    artifacts = []
    for metadata in sorted(packages_dir.glob("*/*/.nupkg.metadata")):
        version_dir = metadata.parent
        package_id = version_dir.parent.name
        version = version_dir.name
        nupkg = version_dir / f"{package_id}.{version}.nupkg"
        source = json.loads(metadata.read_text()).get("source")
        if not source:
            fail(f"{package_id} {version} has no source in {metadata}")
        if not source.startswith(("https://", "http://")):
            fail(
                f"{package_id} {version} comes from {source}; dotnet-purifier fetches only from http(s) feeds"
            )
        base = service_index_base(source, bases)
        artifacts.append(
            {
                "path": nupkg.name,
                "url": f"{base}{package_id}/{version}/{nupkg.name}",
                "hash": sri("sha512", nupkg),
            }
        )
    return artifacts


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
    sdk_root = sdk(major)
    dotnet = f"{sdk_root}/bin/dotnet"

    with tempfile.TemporaryDirectory(prefix="dotnet-purifier-") as tmp:
        tmp = Path(tmp)
        build = tmp / "build"
        shutil.copytree(root, build, ignore=lambda _d, names: [n for n in names if n in SKIPPED_DIRS])
        home = tmp / "home"
        home.mkdir()
        env = dotnet_env(home)

        sdk_version = run(dotnet, ["--version"], build, env, capture=True).strip()
        info(f"locking with the .NET SDK {sdk_version}")
        projects = [describe(dotnet, build, p, env) for p in project_files(dotnet, build, targets, env)]
        if not projects:
            fail("the build has no projects")
        if len({p["name"] for p in projects}) != len(projects):
            fail("two projects share a file name; dotnet-purifier names packages after project files")

        for project in projects:
            out = tmp / "out" / project["name"]
            for command in build_commands(project):
                run(dotnet, [a.replace("{out}", str(out)) for a in command], build, env)
        if (build / ".config" / "dotnet-tools.json").is_file():
            run(dotnet, ["tool", "restore"], build, env)

        artifacts = locked_packages(home / "packages")

    data = {
        "version": FORMAT_VERSION,
        "sdk": {"version": sdk_version, "major": major},
        "targets": targets,
        "definitionHash": def_hash,
        "tools": (root / ".config" / "dotnet-tools.json").is_file(),
        "projects": projects,
        "artifacts": artifacts,
    }
    lock_path.write_text(json.dumps(data, indent=2) + "\n")
    info(f"wrote {LOCK_NAME}: {len(projects)} projects, {len(artifacts)} packages")


def realize(root):
    """Builds the local feed of the lockfile's packages, with a GC root
    under .dotnet-purifier/, and returns its store path."""
    lib = os.environ["DOTNET_PURIFIER_NIX_LIB"]
    lock_path = (root / LOCK_NAME).resolve()
    state = root / STATE_DIR
    state.mkdir(exist_ok=True)
    expr = f"import {lib}/interactive.nix {{ pkgs = {nixpkgs_expr()}; lockFile = {lock_path}; }}"
    return nix_build(expr, out_link=state / "feed")


def run_dotnet(args):
    root = build_root()
    lock_path = root / LOCK_NAME
    if not lock_path.is_file():
        fail(f"{root} has no {LOCK_NAME}; run `dotnet-purifier lock` first")
    data = json.loads(lock_path.read_text())
    if data.get("version") != FORMAT_VERSION:
        fail(f"{LOCK_NAME} has format {data.get('version')}; run `dotnet-purifier lock --force`")
    if data.get("definitionHash") != definition_hash(root, data["targets"]):
        info(f"the build definition changed since {LOCK_NAME} was made; run `dotnet-purifier lock`")
    feed = realize(root)
    sdk_root = sdk(data["sdk"]["major"])
    env = dict(os.environ)
    # MSBuild reads environment variables as properties, so every restore
    # that dotnet runs, explicit or implicit, reads only the feed.
    env["RestoreSources"] = feed
    env["NuGetAudit"] = "false"
    # Nix checked every package of the feed against the SHA-512 that the
    # lock run recorded, after NuGet had verified the package's signature
    # in that run. Verifying the signature again needs the network, for
    # revocation checks.
    env["DOTNET_NUGET_SIGNATURE_VERIFICATION"] = "false"
    # The SDK looks for workload updates on nuget.org in the background. It
    # reads this variable as true only when it says "true".
    env["DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE"] = "true"
    os.execve(f"{sdk_root}/bin/dotnet", ["dotnet", *args], env)


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("lock", "dotnet"):
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    if sys.argv[1] == "lock":
        lock(sys.argv[2:])
    else:
        run_dotnet(sys.argv[2:])


if __name__ == "__main__":
    main()
