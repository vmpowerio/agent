#!/usr/bin/env python3
"""Inspect a locally packed nupkg without extracting or executing its contents."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile


def verify(inputs, packed):
    if inputs.is_symlink() or not inputs.is_dir() or packed.is_symlink() or not packed.is_file():
        raise ValueError('Regular pack inputs and archive required')
    expected = {}
    for file in (inputs / 'tools').rglob('*'):
        if file.is_symlink():
            raise ValueError('Linked package input')
        if file.is_file():
            name = file.relative_to(inputs).as_posix()
            if name == 'tools/node.exe.ignore':
                name = 'tools/bundle/runtime/node.exe.ignore'
            expected[name] = file.read_bytes()
    if any(Path(name).name.casefold() == 'vmpower-agent.amd64.exe' for name in expected):
        raise ValueError('Modern package must not claim the legacy shim basename')
    for marker in ('tools/nssm.exe.ignore', 'tools/bundle/runtime/node.exe.ignore'):
        if expected.get(marker) != b'':
            raise ValueError('Required empty shim suppression marker missing or changed')
    record = json.loads(expected['tools/package-input.json'])
    with zipfile.ZipFile(packed) as archive:
        names = archive.namelist()
        if len(names) != len(set(name.casefold() for name in names)):
            raise ValueError('Duplicate package member')
        if len(names) > len(expected) + 4:
            raise ValueError('Unexpected package members')
        seen = set()
        for entry in archive.infolist():
            name = entry.filename
            if '\\' in name or name.startswith('/') or any(part in ('', '.', '..') for part in name.split('/')):
                raise ValueError('Noncanonical package member')
            if name in expected:
                if entry.file_size != len(expected[name]) or archive.read(entry) != expected[name]:
                    raise ValueError('Packed member differs from verified inputs')
                seen.add(name)
            elif name == 'vmpower-agent.nuspec':
                if entry.file_size > 65536:
                    raise ValueError('Oversized metadata')
                metadata = ET.fromstring(archive.read(entry))
                match = re.fullmatch(r'\{(http://schemas\.microsoft\.com/packaging/[0-9]{4}/[0-9]{2}/nuspec\.xsd)\}package', metadata.tag)
                if not match:
                    raise ValueError('Unexpected nuspec schema')
                ns = {'n': match.group(1)}
                if (metadata.findtext('n:metadata/n:id', namespaces=ns) != 'vmpower-agent'
                        or metadata.findtext('n:metadata/n:version', namespaces=ns) != record['packageVersion']
                        or metadata.find('n:metadata/n:dependencies', ns) is not None):
                    raise ValueError('Packed identity or dependencies differ')
            elif (name in ('[Content_Types].xml', '_rels/.rels') or re.fullmatch(
                    r'package/services/metadata/core-properties/[A-Za-z0-9-]+\.psmdcp', name)):
                if entry.file_size > 65536:
                    raise ValueError('Oversized metadata')
            else:
                raise ValueError('Unexpected package member')
        if seen != set(expected) or 'vmpower-agent.nuspec' not in names:
            raise ValueError('Missing packed member')
    return {'status': 'PASS', 'packageVersion': record['packageVersion'],
            'bundleManifestSha256': record['bundleManifestSha256'],
            'nupkgSha256': hashlib.sha256(packed.read_bytes()).hexdigest(),
            'verifiedPayloadFiles': len(expected), 'shimMarkers': 'PASS'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--package', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.inputs, args.package)))
