#!/usr/bin/env python3
"""Validate source syntax and an Electrum external-plugin release ZIP."""

from pathlib import Path
import json
import py_compile
import sys
import tempfile
import zipfile
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: check_release.py PATH_TO_ZIP")
    archive = Path(sys.argv[1]).resolve()

    for path in sorted((ROOT / "silent_payments_sender").glob("*.py")):
        py_compile.compile(str(path), doraise=True)

    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        if not names or any(not name.startswith("silent_payments_sender/") for name in names):
            raise SystemExit("bad ZIP root")
        if len(names) != len(set(names)):
            raise SystemExit("duplicate ZIP member")
        manifest_name = "silent_payments_sender/manifest.json"
        if manifest_name not in names:
            raise SystemExit("missing manifest")
        manifest = json.loads(bundle.read(manifest_name))
        required = {
            "name", "fullname", "description", "author",
            "available_for", "version", "min_electrum_version",
        }
        if not required.issubset(manifest):
            raise SystemExit("incomplete manifest")
        if manifest["name"] != "silent_payments_sender":
            raise SystemExit("manifest name does not match ZIP root")
        if "qt" not in manifest["available_for"]:
            raise SystemExit("plugin is not marked available for Qt")
        if icon := manifest.get("icon"):
            icon_name = f"silent_payments_sender/{icon}"
            if icon_name not in names:
                raise SystemExit("manifest icon is missing from ZIP")
            if icon.endswith(".svg"):
                try:
                    root = ElementTree.fromstring(bundle.read(icon_name))
                except ElementTree.ParseError as exc:
                    raise SystemExit(f"manifest SVG icon is invalid: {exc}")
                if not root.tag.endswith("svg") or not root.get("viewBox"):
                    raise SystemExit("manifest SVG icon lacks an SVG root/viewBox")
        with tempfile.TemporaryDirectory() as directory:
            bundle.extractall(directory)
            for path in Path(directory).rglob("*.py"):
                py_compile.compile(str(path), doraise=True)
    print(f"OK: {archive}")


if __name__ == "__main__":
    main()
