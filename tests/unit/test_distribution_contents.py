"""Packaging boundary checks operate on real archive metadata, not config text."""

from __future__ import annotations

import importlib.util
import io
import json
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest
from pathspec import GitIgnoreSpec

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts/check_distribution_contents.py"
_SPEC = importlib.util.spec_from_file_location("check_distribution_contents", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
distribution = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(distribution)


def _archive(tmp_path: Path, kind: str, names: list[str]) -> Path:
    payload = b"synthetic fixture payload: must never be read or printed"
    if kind == "sdist":
        archive = tmp_path / "trading_partner-1.0.tar.gz"
        with tarfile.open(archive, "w:gz") as target:
            for name in names:
                member = tarfile.TarInfo(f"trading_partner-1.0/{name}")
                member.size = len(payload)
                target.addfile(member, io.BytesIO(payload))
        return archive
    archive = tmp_path / "trading_partner-1.0-py3-none-any.whl"
    with zipfile.ZipFile(archive, "w") as target:
        for name in names:
            target.writestr(name, payload)
    return archive


@pytest.mark.parametrize("kind", ["sdist", "wheel"])
def test_source_config_examples_and_migrations_are_allowed(tmp_path: Path, kind: str) -> None:
    names = [
        "src/application/__init__.py",
        "console/app/page.tsx",
        "console/package-lock.json",
        ".env.example",
        ".env.production.example",
        "console/.env.example",
        "config/account_basis_checkpoints.example.yaml",
        "config/vendor_chains.default.yaml",
        "migrations/versions/0073_research_evidence_links.py",
        "trading_partner_migrations/versions/0073_research_evidence_links.py",
        "infrastructure/config/a_share_trading_calendar.v1.json",
        "trading_partner-1.0.dist-info/METADATA",
        "scripts/check_distribution_contents.py",
    ]
    report = distribution.inspect_distribution(_archive(tmp_path, kind, names))
    assert report["kind"] == kind
    assert report["members"] == len(names)
    assert report["blocked"] == dict.fromkeys(distribution.CATEGORIES, 0)


@pytest.mark.parametrize("kind", ["sdist", "wheel"])
@pytest.mark.parametrize(
    ("name", "category"),
    [
        ("data/.gitkeep", "runtime_data"),
        ("data/diagnostics/fixture.json", "runtime_data"),
        ("data/research/fixture.json", "runtime_data"),
        (".env", "secrets"),
        (".env.local", "secrets"),
        ("console/.env.production", "secrets"),
        ("runtime.env", "secrets"),
        ("nested/runtime.env.backup", "secrets"),
        ("config/account_basis_checkpoints.yaml", "owner_config"),
        ("console/node_modules/react/index.js", "dependencies"),
        ("node_modules/pkg/package.json", "dependencies"),
        ("console/.yarn/cache/pkg.zip", "dependencies"),
        ("console/.pnp.cjs", "dependencies"),
        ("console/.next/server/app/page.js", "build_outputs"),
        ("console/.next-design-preview/server/app/page.js", "build_outputs"),
        ("console/.next-integration/server/app/page.js", "build_outputs"),
        ("console/.vinext/page.js", "build_outputs"),
        ("console/.wrangler/state.json", "build_outputs"),
        ("console/.vercel/config.json", "build_outputs"),
        ("console/out/index.html", "build_outputs"),
        ("console/dist/index.js", "build_outputs"),
        ("console/build/index.js", "build_outputs"),
        ("console/outputs/page.html", "build_outputs"),
        ("console/work/page.html", "build_outputs"),
        ("console/coverage/report.html", "build_outputs"),
        ("console/test-results/result.json", "build_outputs"),
        ("console/playwright-report/index.html", "build_outputs"),
        ("console/blob-report/report.zip", "build_outputs"),
        ("console/tsconfig.tsbuildinfo", "build_outputs"),
        ("nested/build.tsbuildinfo", "build_outputs"),
        ("../escape.json", "unsafe_paths"),
        ("console\\node_modules\\pkg.js", "unsafe_paths"),
    ],
)
def test_rejects_contamination(tmp_path: Path, kind: str, name: str, category: str) -> None:
    report = distribution.inspect_distribution(_archive(tmp_path, kind, [name]))
    expected = dict.fromkeys(distribution.CATEGORIES, 0)
    expected[category] = 1
    assert report["blocked"] == expected


@pytest.mark.parametrize("kind", ["sdist", "wheel"])
def test_payloads_are_not_read_and_cli_only_reports_counts(
    tmp_path: Path,
    kind: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    archive = _archive(tmp_path, kind, ["data/private-fixture-name.json", "src/bootstrap.py"])

    def reject_payload_read(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("archive payload must not be opened")

    monkeypatch.setattr(tarfile.TarFile, "extractfile", reject_payload_read)
    monkeypatch.setattr(zipfile.ZipFile, "open", reject_payload_read)
    assert distribution.main([str(archive)]) == 1
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["members"] == 2
    assert report["blocked"]["runtime_data"] == 1
    assert "private-fixture-name" not in output
    assert "payload" not in output
    assert str(archive) not in output


def test_clean_archives_return_success(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    archives = [_archive(tmp_path, kind, ["src/bootstrap.py"]) for kind in ("sdist", "wheel")]
    assert distribution.main([str(archive) for archive in archives]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 2


@pytest.mark.parametrize("suffix", [".tar.gz", ".whl", ".zip"])
def test_invalid_archives_fail_without_disclosing_paths(
    tmp_path: Path,
    suffix: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    archive = tmp_path / f"private-fixture-name{suffix}"
    archive.write_bytes(b"invalid fixture archive")
    assert distribution.main([str(archive)]) == 1
    output = capsys.readouterr().out
    assert json.loads(output) == {"error": "unable to inspect distribution"}
    assert "private-fixture-name" not in output


@pytest.mark.parametrize("kind", ["sdist", "wheel"])
def test_empty_archives_fail(tmp_path: Path, kind: str) -> None:
    archive = _archive(tmp_path, kind, [])
    with pytest.raises(ValueError, match="empty distribution"):
        distribution.inspect_distribution(archive)


def test_sdist_without_package_root_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "unwrapped.tar.gz"
    with tarfile.open(archive, "w:gz") as target:
        target.addfile(tarfile.TarInfo("data/private-fixture-name.json"))
    report = distribution.inspect_distribution(archive)
    assert report["blocked"]["unsafe_paths"] == 1


@pytest.mark.parametrize(
    ("name", "excluded"),
    [
        ("data/.gitkeep", True),
        ("data/research/example.json", True),
        ("data/diagnostics/fixture.json", True),
        (".env", True),
        (".env.local", True),
        (".env.example", False),
        ("console/.env.example", False),
        ("console/app/page.tsx", False),
        ("config/account_basis_checkpoints.example.yaml", False),
        ("config/vendor_chains.default.yaml", False),
        ("migrations/versions/0073_research_evidence_links.py", False),
    ],
)
def test_sdist_exclusions_override_vcs_file_negations(name: str, excluded: bool) -> None:
    root = Path(__file__).resolve().parents[2]
    config = tomllib.loads((root / "pyproject.toml").read_text())
    exclusions = config["tool"]["hatch"]["build"]["targets"]["sdist"]["exclude"]
    # Hatchling combines VCS patterns first, then configured excludes, using
    # GitIgnoreSpec (not PathSpec). Specific file negations can outrank a later
    # directory-only exclusion, as the tracked empty data placeholder proved.
    vcs_patterns = [".env*", "!data/.gitkeep", "!data/research/example.json"]
    matcher = GitIgnoreSpec.from_lines([*vcs_patterns, *exclusions])
    assert matcher.match_file(name) is excluded
