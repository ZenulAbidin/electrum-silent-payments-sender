#!/usr/bin/env python3
"""Build a deterministic Electrum external-plugin ZIP and checksum file."""

from hashlib import sha256
from pathlib import Path
import json
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "silent_payments_sender"
DIST = ROOT / "dist"
SOURCE_EXCLUDES = {"dist", "__pycache__", ".git", ".pytest_cache"}


def _write_member(bundle, path, archive_name):
    info = zipfile.ZipInfo(archive_name, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    bundle.writestr(info, path.read_bytes())


def main() -> None:
    manifest = json.loads((PLUGIN / "manifest.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    archive = DIST / f"silent_payments_sender-{version}.zip"
    source_archive = DIST / f"electrum-silent-payments-sender-{version}-source.zip"
    DIST.mkdir(exist_ok=True)

    files = sorted(
        path for path in PLUGIN.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and not path.name.endswith(".pyc")
    )
    with zipfile.ZipFile(
        archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as bundle:
        for path in files:
            _write_member(
                bundle,
                path,
                (Path("silent_payments_sender") / path.relative_to(PLUGIN)).as_posix(),
            )

    source_files = sorted(
        path for path in ROOT.rglob("*")
        if path.is_file()
        and not any(part in SOURCE_EXCLUDES for part in path.relative_to(ROOT).parts)
        and not path.name.endswith(".pyc")
    )
    with zipfile.ZipFile(
        source_archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as bundle:
        source_root = Path(f"electrum-silent-payments-sender-{version}")
        for path in source_files:
            _write_member(
                bundle,
                path,
                (source_root / path.relative_to(ROOT)).as_posix(),
            )

    artifacts = [archive, source_archive]
    checksums = [
        f"{sha256(path.read_bytes()).hexdigest()}  {path.name}"
        for path in artifacts
    ]
    (DIST / "SHA256SUMS").write_text(
        "\n".join(checksums) + "\n", encoding="ascii"
    )
    btt_template = (ROOT / "BTT_POST.md").read_text(encoding="utf-8")
    (DIST / "BTT_POST_READY.md").write_text(
        btt_template.replace(
            "    [PASTE SHA256SUMS HERE]",
            "\n".join(f"    {checksum}" for checksum in checksums),
        ),
        encoding="utf-8",
    )
    for path, checksum in zip(artifacts, checksums):
        print(path)
        print(checksum.split()[0])


if __name__ == "__main__":
    main()
