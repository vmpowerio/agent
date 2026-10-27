#!/usr/bin/env python3
"""Prepare local Chocolatey pack inputs from an already qualified Windows bundle.

No download, package-manager invocation or executable is run. Chocolatey's own
pack command creates the nupkg on the separately admitted Windows fixture.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('agent_stage', ROOT / 'scripts/stage-distribution.py')
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


def regular(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected a regular input file')
    return path.read_bytes()


def prepare(bundle, expected, node_archive, nssm_archive, version, output, repo=ROOT):
    if not re.fullmatch(r'[a-f0-9]{64}', expected):
        raise ValueError('Qualified manifest SHA256 required')
    if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', version):
        raise ValueError('An explicit three-part package version is required')
    if tuple(map(int, version.split('.'))) <= (0, 0, 1):
        raise ValueError('Package must upgrade published 0.0.1')
    if output.exists() or output.is_symlink():
        raise ValueError('Output exists; retain previous package inputs')
    if bundle.is_symlink() or not bundle.is_dir():
        raise ValueError('Expected an unlinked bundle directory')
    if stage.digest(regular(bundle / 'bundle-manifest.json')) != expected:
        raise ValueError('Bundle manifest hash mismatch')
    manifest = stage.verify_bundle(bundle)
    runtime_pins = json.loads(regular(repo / 'distribution/runtime-pins.json'))
    if manifest['target'] != 'win-x64' or manifest['nodeVersion'] != runtime_pins['nodeVersion']:
        raise ValueError('Expected pinned Windows x64 bundle')
    if manifest['runtimeArchiveSha256'] != runtime_pins['targets']['win-x64']['sha256']:
        raise ValueError('Runtime provenance mismatch')
    if manifest['sourceLockSha256'] != stage.digest(regular(repo / 'package-lock.json')):
        raise ValueError('Bundle dependency lock differs from this source')
    regular(node_archive)
    runtime = stage.runtime_files(node_archive, 'win-x64', runtime_pins)
    for name, raw in runtime.items():
        if regular(bundle / 'runtime' / name) != raw:
            raise ValueError('Bundle runtime differs from official pinned archive')
    pins = json.loads(regular(repo / 'distribution/nssm-pins.json'))
    if stage.digest(regular(nssm_archive)) != pins['archiveSha256']:
        raise ValueError('NSSM archive hash mismatch')
    with zipfile.ZipFile(nssm_archive) as archive:
        wrapper = archive.read(pins['x64Member'])
        license_bytes = archive.read('nssm-' + pins['version'] + '/README.txt')
    if stage.digest(wrapper) != pins['x64Sha256'] or b'NSSM is public domain' not in license_bytes:
        raise ValueError('NSSM executable or licence mismatch')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.windows-package-', dir=output.parent) as temp:
        root = Path(temp) / 'package'
        tools = root / 'tools'
        tools.mkdir(parents=True)
        shutil.copytree(bundle, tools / 'bundle')
        stage.verify_bundle(tools / 'bundle')
        if stage.digest(regular(tools / 'bundle/bundle-manifest.json')) != expected:
            raise ValueError('Bundle changed while preparing package inputs')
        # Do not expose bundled node or NSSM as global Chocolatey shims.
        # Ignore markers live outside the sealed bundle; copy node's marker into
        # the package file mapping separately, not into its verified manifest.
        (tools / 'nssm.exe').write_bytes(wrapper)
        (tools / 'nssm.exe.ignore').write_bytes(b'')
        (tools / 'node.exe.ignore').write_bytes(b'')
        (tools / 'NSSM-LICENSE.txt').write_bytes(license_bytes)
        for name in ('chocolateyInstall.ps1', 'chocolateyUninstall.ps1', 'package-shim.ps1'):
            (tools / name).write_bytes(regular(repo / 'packages/win32/node24/tools' / name))
        for relative in ('scripts/install-windows-bundle.ps1', 'distribution/nssm-pins.json'):
            target = tools / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(regular(repo / relative))
        (tools / 'package-input.json').write_text(json.dumps({
            'packageVersion': version, 'bundleManifestSha256': expected,
            'nodeArchiveSha256': manifest['runtimeArchiveSha256'],
            'nssmArchiveSha256': pins['archiveSha256'], 'nssmExeSha256': pins['x64Sha256']
        }, indent=2) + '\n')
        package = ET.Element('package', xmlns='http://schemas.microsoft.com/packaging/2015/06/nuspec.xsd')
        metadata = ET.SubElement(package, 'metadata')
        for key, value in [('id', 'vmpower-agent'), ('version', version), ('authors', 'VMPower'),
                           ('description', 'Offline Node 24 VMPower agent upgrade; preserves existing configuration.'),
                           ('requireLicenseAcceptance', 'false'), ('projectUrl', 'https://github.com/vmpowerio/agent')]:
            ET.SubElement(metadata, key).text = value
        # No unversioned nssm dependency: the verified wrapper is embedded.
        files = ET.SubElement(package, 'files')
        ET.SubElement(files, 'file', src='tools\\**', target='tools', exclude='tools\\node.exe.ignore')
        ET.SubElement(files, 'file', src='tools\\node.exe.ignore', target='tools\\bundle\\runtime')
        ET.ElementTree(package).write(root / 'vmpower-agent.nuspec', encoding='utf-8', xml_declaration=True)
        root.rename(output)
    return {'state': 'PACK_INPUTS_ONLY', 'version': version, 'bundleManifestSha256': expected,
            'nativePackageManagerQualified': False, 'output': str(output)}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for flag in ('bundle', 'node-archive', 'nssm-archive', 'output'):
        p.add_argument('--' + flag, type=Path, required=True)
    p.add_argument('--expected-manifest-sha256', required=True)
    p.add_argument('--version', required=True)
    a = p.parse_args()
    print(json.dumps(prepare(a.bundle, a.expected_manifest_sha256, a.node_archive,
                             a.nssm_archive, a.version, a.output)))
