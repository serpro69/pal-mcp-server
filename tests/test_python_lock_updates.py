"""Exercise the weekly updater against a local wheel index."""

import os
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

UPDATE = Path(__file__).resolve().parents[1] / "scripts" / "update_python_locks.py"


def test_grouped_lock_refresh(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    wheelhouse = tmp_path / "wheel index"
    wheelhouse.mkdir()
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setenv("PIP_FIND_LINKS", wheelhouse.as_uri())
    monkeypatch.setenv("PATH", f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}")

    for name, versions, requires in (
        ("mcp", ["2.3", "2.3.1", "3.0"], "Requires-Dist: shared>=1\n"),
        ("shared", ["1.0", "1.2", "2.0"], ""),
    ):
        for version in versions:
            package = f"{name}-{version}"
            info = f"{package}.dist-info"
            with ZipFile(wheelhouse / f"{package}-py3-none-any.whl", "w") as wheel:
                wheel.writestr(
                    f"{info}/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n{requires}"
                )
                wheel.writestr(f"{info}/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
                wheel.writestr(f"{info}/RECORD", "")

    Path("pyproject.toml").write_text('[project]\nname = "sample"\nversion = "1"\ndependencies = ["mcp>=2.3,<3"]\n')
    dev_source = Path("requirements-dev.in")
    dev_source.write_text("-c requirements.txt\nshared>=1\n")
    runtime, dev = Path("requirements.txt"), Path("requirements-dev.txt")
    runtime.write_text("mcp==2.3\nshared==1.0\n")
    dev.write_text("shared==1.0\n")

    subprocess.run([sys.executable, UPDATE], check=True)
    assert "mcp==2.3.1" in runtime.read_text().splitlines()
    assert all("shared==1.2" in lock.read_text().splitlines() for lock in (runtime, dev))
    snapshot = (runtime.read_bytes(), dev.read_bytes())
    for source, lock in (("pyproject.toml", runtime), (dev_source, dev)):
        subprocess.run(["pip-compile", "--strip-extras", "--no-header", source, "-o", lock], check=True)
    assert (runtime.read_bytes(), dev.read_bytes()) == snapshot

    # A successful runtime upgrade followed by an unsatisfiable dev dependency leaves both locks intact.
    runtime.write_text("mcp==2.3\nshared==1.0\n")
    dev_source.write_text("-c requirements.txt\nshared==9\n")
    before_failure = (runtime.read_bytes(), dev.read_bytes())
    result = subprocess.run([sys.executable, UPDATE], capture_output=True)
    assert result.returncode != 0 and b"shared==9" in result.stderr
    assert (runtime.read_bytes(), dev.read_bytes()) == before_failure
