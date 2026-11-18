"""Non-installing, non-authorizing integration-candidate exports."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .qualification import integrity_check

SCHEMA = "arena-integration-candidate/1"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def export_candidate(package: Path, out: Path) -> dict[str, Any]:
    """Create a deterministic ZIP for review/integration testing, never installation.

    The command intentionally does not call the release gate, modify package
    evidence, admit an adapter intent, or issue authorization. It only checks
    package integrity and copies the exact listed package bytes into an archive
    carrying an explicit non-authorizing envelope.
    """
    if package.is_symlink() or not package.is_dir():
        raise ValueError("candidate source must be an existing, non-symlink package directory")
    package = package.resolve()
    if out.suffix.lower() != ".zip":
        raise ValueError("candidate export path must end in .zip")
    if out.is_symlink():
        raise ValueError("candidate export cannot overwrite a symlink")
    out = out.resolve()
    if _inside(out, package):
        raise ValueError("candidate export must be outside its source package")
    if out.exists():
        raise ValueError(f"candidate export already exists: {out}")

    integrity = integrity_check(package)
    if not integrity["ok"]:
        raise ValueError("candidate source package failed integrity check: "
                         + "; ".join(integrity["problems"]))
    manifest = integrity["manifest"]
    try:
        evidence = json.loads((package / "evidence.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"candidate source evidence is unreadable: {exc}") from exc
    if (not isinstance(evidence, dict)
            or evidence.get("spec") != manifest.get("spec")
            or evidence.get("spec_version") != manifest.get("spec_version")
            or evidence.get("package_version") != manifest.get("package_version")):
        raise ValueError("candidate source evidence does not identify the package manifest")

    listed = dict(manifest["files"])
    listed.update(manifest.get("metadata_files", {}))
    names = sorted(set(listed) | {"manifest.json"})
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or not path.parts or ".." in path.parts:
            raise ValueError(f"unsafe package member path: {name!r}")

    envelope = {
        "schema": SCHEMA,
        "candidate_only": True,
        "non_authorizing": True,
        "installable": False,
        "release_authorized": False,
        "qualification": "UNCHANGED",
        "integrity": "PASS",
        "package": manifest.get("spec"),
        "package_version": manifest.get("package_version"),
        "spec_version": manifest.get("spec_version"),
        "package_manifest_sha256": integrity["manifest_sha256"],
        "package_files": [
            {"path": name, "sha256": (listed[name]["sha256"]
                                       if name in listed else integrity["manifest_sha256"])}
            for name in names
        ],
        "package_lifecycle": evidence.get("lifecycle", {}),
        "campaign_admission": "NOT_ASSESSED",
        "campaign_live": "NOT_ASSESSED",
        "warning": (
            "Review/integration candidate only. This archive does not install, "
            "admit, qualify, release, or authorize the package. The production "
            "release gate and all target-environment checks remain unchanged."
        ),
    }
    envelope_bytes = (json.dumps(envelope, indent=2, sort_keys=True) + "\n").encode("utf-8")

    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{out.name}.", suffix=".tmp", dir=out.parent)
        os.close(fd)
        try:
            with zipfile.ZipFile(temp_name, "w", compression=zipfile.ZIP_DEFLATED,
                                 compresslevel=9) as archive:
                for name in names:
                    info = zipfile.ZipInfo(f"package/{name}", date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o100644 << 16
                    archive.writestr(info, (package / name).read_bytes(), compress_type=zipfile.ZIP_DEFLATED,
                                     compresslevel=9)
                info = zipfile.ZipInfo("CANDIDATE.json", date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, envelope_bytes, compress_type=zipfile.ZIP_DEFLATED,
                                 compresslevel=9)
            try:
                os.link(temp_name, out)
            except FileExistsError as exc:
                raise ValueError(f"candidate export target appeared during write: {out}") from exc
            os.unlink(temp_name)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
    except OSError as exc:
        raise ValueError(f"cannot write candidate export: {exc}") from exc

    archive_sha256 = _sha256(out.read_bytes())
    return {"schema": SCHEMA, "status": "EXPORTED_NON_AUTHORIZING_CANDIDATE",
            "path": str(out), "sha256": archive_sha256,
            "package": manifest.get("spec"), "package_manifest_sha256": integrity["manifest_sha256"],
            "installable": False, "release_authorized": False}
