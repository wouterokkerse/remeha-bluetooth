"""Create an installable ZIP without caches, captures or local secrets."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parent.parent


def main():
    destination = ROOT / "dist/remeha-elga-ace-bluetooth.zip"
    destination.parent.mkdir(exist_ok=True)
    integration = ROOT / "custom_components/elga_ace"
    files = [path for path in integration.rglob("*") if path.is_file() and "__pycache__" not in path.parts and path.suffix in {".py", ".json"}]
    files += [ROOT / "README.md", ROOT / "LICENSE", ROOT / "hacs.json"]
    files += [path for path in (ROOT / "esphome").rglob("*")
              if path.is_file() and not any(part in {"__pycache__", ".esphome"} for part in path.parts)
              and path.name != "secrets.yaml" and path.suffix in {".py", ".h", ".cpp", ".yaml", ".md", ".example"}]
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, path.relative_to(ROOT).as_posix())
    print(destination)


if __name__ == "__main__":
    main()
