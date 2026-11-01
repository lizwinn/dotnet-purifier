# SPDX-License-Identifier: MIT
"""Installs NuGet packages into a package folder the way NuGet does.

  python3 package-folder.py PACKAGES_JSON OUT

PACKAGES_JSON lists the packages as { path, file, hash }: `path` is
<id>/<version>/<id>.<version>.nupkg, with the id and version in lower
case, `file` the .nupkg and `hash` its SRI SHA-512. For each package OUT
gets the directory <id>/<version> with the .nupkg, its contents, the
nuspec as <id>.nuspec, <file>.sha512 and .nupkg.metadata. NuGet takes a
package with these files as installed, and the folder is also a feed that
a restore reads.
"""

import json
import shutil
import sys
import urllib.parse
import zipfile
from pathlib import Path

# The parts of a .nupkg that belong to the package format, which NuGet
# leaves out when it installs a package.
SKIPPED = ("[Content_Types].xml", "_rels/", "package/")


def install(package, out):
    path = Path(package["path"])
    package_id = path.parts[0]
    directory = out / path.parent
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(package["file"], out / path)
    content_hash = package["hash"].removeprefix("sha512-")
    with zipfile.ZipFile(out / path) as nupkg:
        for entry in nupkg.infolist():
            if entry.is_dir() or entry.filename.startswith(SKIPPED):
                continue
            # The entries of a .nupkg have URL-encoded names.
            name = urllib.parse.unquote(entry.filename)
            if "/" not in name and name.lower().endswith(".nuspec"):
                name = f"{package_id}.nuspec"
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with nupkg.open(entry) as source, open(target, "wb") as dest:
                shutil.copyfileobj(source, dest)
    (out / f"{path}.sha512").write_text(content_hash)
    metadata = {"version": 2, "contentHash": content_hash, "source": str(out)}
    (directory / ".nupkg.metadata").write_text(json.dumps(metadata))


def main():
    packages = json.loads(Path(sys.argv[1]).read_text())
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    for package in packages:
        install(package, out)


if __name__ == "__main__":
    main()
