"""Refresh minor and patch releases in both pip-compile locks, runtime first."""

import subprocess
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory

from packaging.requirements import Requirement
from packaging.version import Version


def refresh(directory: Path, source: str, lock: str) -> None:
    command = ["pip-compile", "--strip-extras", "--no-header", "--upgrade"]
    for line in (directory / lock).read_text(encoding="utf-8").splitlines():
        if not line or line.startswith(("#", "-", " ", "\t")):
            continue
        requirement = Requirement(line)
        if requirement.url or (requirement.marker and not requirement.marker.evaluate()):
            continue
        (pin,) = requirement.specifier
        version = Version(pin.version)
        next_major = Version(f"{version.epoch}!{version.major + 1}")
        command.extend(["--upgrade-package", f"{requirement.name}>={version.public},<{next_major}"])
    subprocess.run([*command, source, "-o", lock], cwd=directory, check=True)


if __name__ == "__main__":
    pairs = [("pyproject.toml", "requirements.txt"), ("requirements-dev.in", "requirements-dev.txt")]
    # Keep canonical filenames for pip-compile's annotations and the dev constraint.
    with TemporaryDirectory(dir=".") as staging:
        directory = Path(staging)
        for source, lock in pairs:
            copyfile(source, directory / source)
            copyfile(lock, directory / lock)
            refresh(directory, source, lock)
        for _, lock in pairs:
            (directory / lock).replace(lock)
