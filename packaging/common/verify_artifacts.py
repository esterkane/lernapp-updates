#!/usr/bin/env python3
"""Extract built installers without installing and compare their source to this checkout."""
import argparse
import hashlib
import io
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifacts', nargs='+', type=Path)
    args = parser.parse_args()
    verifier = Path(__file__).with_name('source_payload.py')
    checksums = []
    for artifact in args.artifacts:
        with tempfile.TemporaryDirectory(prefix='lernapp-verify-') as temporary:
            dest = Path(temporary)
            if artifact.suffix == '.pkg':
                subprocess.run(['pkgutil', '--expand-full', str(artifact), str(dest / 'pkg')], check=True)
                archives = list((dest / 'pkg').rglob('src.tar.gz'))
                if len(archives) != 1:
                    raise SystemExit('Expected exactly one source archive in macOS package')
                with tarfile.open(archives[0]) as archive:
                    archive.extractall(dest / 'src', filter='data')
                source = dest / 'src'
            elif artifact.suffix == '.zip':
                with zipfile.ZipFile(artifact) as archive:
                    archive.extractall(dest)
                sources = list(dest.glob('*/src'))
                if len(sources) != 1:
                    raise SystemExit('Expected exactly one source directory in Windows ZIP')
                source = sources[0]
            elif artifact.name.endswith('-linux-installer.sh'):
                payload = artifact.read_bytes().split(b'\n__ARCHIVE__\n', 1)[1]
                with tarfile.open(fileobj=io.BytesIO(payload), mode='r:gz') as archive:
                    archive.extractall(dest, filter='data')
                source = dest / 'src'
            else:
                raise SystemExit(f'Unsupported installer: {artifact}')
            subprocess.run([sys.executable, str(verifier), 'verify', str(source)], check=True)
        checksums.append(f'{hashlib.sha256(artifact.read_bytes()).hexdigest()}  {artifact.name}')
    print('\n'.join(checksums))


if __name__ == '__main__':
    main()
