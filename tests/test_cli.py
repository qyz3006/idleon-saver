"""Tests for the unified idleon-saver CLI.

Self-contained (no conftest fixtures) so they run with
``pytest tests/test_cli.py --noconftest`` even without the optional ``plyvel``
dependency. The ``decode``/``encode`` subcommands themselves require plyvel at
runtime, so their round-trip is covered by the frozen ``test_stencyl`` /
``test_scripts`` suites; here we smoke-test ``--help`` and exercise the
``export`` subcommand (which does not need plyvel).
"""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "tests" / "data"


@pytest.fixture
def workdir(tmp_path):
    d = tmp_path / "work"
    d.mkdir()
    return d


@pytest.fixture
def save_data():
    return {
        "local": json.loads((DATA / "local.json").read_text()),
        "firebase": json.loads((DATA / "firebase.json").read_text()),
    }


def test_cli_help_all_subcommands():
    from idleon_saver.cli import main

    for cmd in ("decode", "encode", "export", "gui"):
        with pytest.raises(SystemExit) as exc:
            main([cmd, "--help"])
        assert exc.value.code == 0


def test_cli_export_companion_local(workdir, save_data):
    from idleon_saver.cli import main

    infile = workdir / "decoded.json"
    infile.write_text(json.dumps(save_data["local"]))

    main(
        [
            "export",
            "--infile",
            "decoded.json",
            "--outfile",
            "out",
            "--source",
            "local",
            "--to",
            "idleon_companion",
            "--workdir",
            str(workdir),
        ]
    )

    out = workdir / "out" / "idleon_companion.json"
    assert out.exists()
    exported = json.loads(out.read_text())
    assert exported["statues"]
    assert all(s.endswith(" Statue") for s in exported["statues"])
    assert exported["chars"]
    assert exported["alchemy"]


def test_cli_export_cogstruction_firebase(workdir, save_data):
    from idleon_saver.cli import main

    infile = workdir / "decoded.json"
    infile.write_text(json.dumps(save_data["firebase"]))

    main(
        [
            "export",
            "--infile",
            "decoded.json",
            "--outfile",
            "out",
            "--source",
            "firebase",
            "--to",
            "cogstruction",
            "--workdir",
            str(workdir),
        ]
    )

    assert (workdir / "out" / "cog_datas.csv").exists()
    assert (workdir / "out" / "empties_datas.csv").exists()


def test_cli_export_toolbox_firebase(workdir, save_data):
    from idleon_saver.cli import main

    infile = workdir / "decoded.json"
    infile.write_text(json.dumps(save_data["firebase"]))

    main(
        [
            "export",
            "--infile",
            "decoded.json",
            "--outfile",
            "out",
            "--source",
            "firebase",
            "--to",
            "toolbox",
            "--workdir",
            str(workdir),
        ]
    )

    out = workdir / "out" / "idleon_toolbox.json"
    assert out.exists()
    assert json.loads(out.read_text()) == save_data["firebase"]


def test_cli_export_efficiency_local(workdir, save_data):
    from idleon_saver.cli import main

    infile = workdir / "decoded.json"
    infile.write_text(json.dumps(save_data["local"]))

    main(
        [
            "export",
            "--infile",
            "decoded.json",
            "--outfile",
            "out",
            "--source",
            "local",
            "--to",
            "efficiency",
            "--workdir",
            str(workdir),
        ]
    )

    out = workdir / "out" / "idleon_efficiency.json"
    assert out.exists()
    payload = json.loads(out.read_text())
    assert "Cloudsave" in payload
