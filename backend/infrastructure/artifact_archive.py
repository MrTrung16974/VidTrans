"""Build complete, isolated archives before starting an HTTP download."""
import tempfile
import zipfile
from pathlib import Path


def build_artifact_archive(artifacts: list[Path], directory: Path) -> Path:
    # Every response owns its file: another download cannot truncate it.
    with tempfile.NamedTemporaryFile(prefix="download-", suffix=".zip", dir=directory, delete=False) as temporary:
        archive_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for artifact in artifacts:
                archive.write(artifact, arcname=artifact.name)
        return archive_path
    except BaseException:
        archive_path.unlink(missing_ok=True)
        raise
