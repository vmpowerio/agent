#!/usr/bin/env python3
"""Stage an offline, integrity-bound runtime bundle. Never install or publish.

Input node_modules must come from a separately qualified npm ci of this lock.
Version validation is not a replacement for npm tarball integrity verification.
This tool preserves official Node bytes and does not execute any staged code.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import struct
import tarfile
import tempfile
import zipfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def production_nodes(lock):
    if lock.get('lockfileVersion') != 1:
        raise ValueError('Unqualified lock format')
    result = {}
    def walk(entries, prefix=PurePosixPath('node_modules')):
        for name, entry in entries.items():
            parts = PurePosixPath(name).parts
            if '..' in parts or name.startswith('/') or '\\' in name:
                raise ValueError('Unsafe dependency name')
            location = prefix / name
            if not entry.get('dev', False):
                if not entry.get('integrity') or not entry.get('resolved', '').startswith('https://registry.npmjs.org/'):
                    raise ValueError('Unqualified dependency origin')
                result[str(location)] = (name, entry['version'], entry['integrity'])
            walk(entry.get('dependencies', {}), location / 'node_modules')
    walk(lock['dependencies'])
    return result


def runtime_files(archive, target, pins):
    raw = archive.read_bytes()
    pin = pins['targets'][target]
    if archive.name != pin['archive'] or digest(raw) != pin['sha256']:
        raise ValueError('Runtime archive does not match the pinned official bytes')
    root = 'node-v' + pins['nodeVersion'] + '-' + target
    wanted = {root + '/' + pin['binary']: 'node.exe' if target == 'win-x64' else 'node',
              root + '/LICENSE': 'LICENSE'}
    output = {}
    if target == 'win-x64':
        with zipfile.ZipFile(archive) as z:
            for member in z.infolist():
                if member.filename in wanted:
                    if member.filename in output or member.file_size > 150 * 1024 * 1024:
                        raise ValueError('Invalid runtime member')
                    output[wanted[member.filename]] = z.read(member)
    else:
        with tarfile.open(archive) as t:
            for member in t:
                if member.name in wanted:
                    if not member.isfile() or member.size > 150 * 1024 * 1024:
                        raise ValueError('Invalid runtime member')
                    output[wanted[member.name]] = t.extractfile(member).read()
    if set(output) != set(wanted.values()):
        raise ValueError('Runtime archive is missing required files')
    binary = output['node.exe' if target == 'win-x64' else 'node']
    if target == 'linux-x64':
        if binary[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', binary, 18)[0] != 62:
            raise ValueError('Expected Linux x64 ELF')
    else:
        pe = struct.unpack_from('<I', binary, 60)[0]
        if binary[:2] != b'MZ' or binary[pe:pe+4] != b'PE\0\0' or struct.unpack_from('<H', binary, pe+4)[0] != 0x8664:
            raise ValueError('Expected Windows x64 PE')
    return output


def copy_package(source, destination):
    if source.is_symlink() or not source.is_dir():
        raise ValueError('Missing or linked dependency directory')
    destination.mkdir(parents=True, exist_ok=True)
    for entry in source.iterdir():
        if entry.name == 'node_modules':
            continue  # Nested production packages are handled from the lock.
        if entry.is_symlink():
            raise ValueError('Dependency symlink is not qualified')
        if entry.is_dir():
            copy_package(entry, destination / entry.name)
        elif entry.is_file():
            shutil.copyfile(entry, destination / entry.name)
            (destination / entry.name).chmod(0o644)
        else:
            raise ValueError('Unsupported dependency file')


def verify_bundle(root):
    if any(p.is_symlink() for p in root.rglob('*')):
        raise ValueError('Bundle symlink is not qualified')
    manifest = json.loads((root / 'bundle-manifest.json').read_text())
    actual = {p.relative_to(root).as_posix(): digest(p.read_bytes()) for p in root.rglob('*')
              if p.is_file() and p != root / 'bundle-manifest.json'}
    if actual != manifest['files']:
        raise ValueError('Bundle contents differ from the manifest')
    return manifest


def stage(repo, modules, archive, target, output):
    if modules.name != 'node_modules':
        raise ValueError('Expected the explicit node_modules input directory')
    pins = json.loads((repo / 'distribution/runtime-pins.json').read_text())
    runtime = runtime_files(archive, target, pins)
    lock_bytes = (repo / 'package-lock.json').read_bytes()
    nodes = production_nodes(json.loads(lock_bytes))
    if output.exists():
        raise ValueError('Output already exists; preserve prior artifacts')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.agent-stage-', dir=output.parent) as temp:
        root = Path(temp) / 'bundle'
        (root / 'runtime').mkdir(parents=True)
        for name, data in runtime.items():
            p = root / 'runtime' / name
            p.write_bytes(data)
            p.chmod(0o755 if name in ('node','node.exe') else 0o644)
        for location, (name, version, integrity) in nodes.items():
            source = modules.parent / location
            if source.is_symlink() or not source.is_dir() or (source / 'package.json').is_symlink():
                raise ValueError('Linked or missing dependency metadata')
            meta = json.loads((source / 'package.json').read_text())
            if meta.get('name') != name or meta.get('version') != version:
                raise ValueError('Installed dependency does not match the lock: ' + name)
            copy_package(source, root / 'app' / location)
        compiled = [repo / 'build/index.js', *sorted((repo / 'build/lib').glob('*.js'))]
        if len(compiled) != 5 or not all(p.is_file() and not p.is_symlink() for p in compiled):
            raise ValueError('Expected the five compiled application files; build first')
        for source in compiled:
            dest = root / 'app' / source.relative_to(repo / 'build')
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
            dest.chmod(0o644)
        shutil.copyfile(repo / 'package.json', root / 'app/package.json')
        launcher = root / ('vmpower.cmd' if target == 'win-x64' else 'vmpower')
        launcher.write_text('@echo off\r\n"%~dp0runtime\\node.exe" "%~dp0app\\index.js" %*\r\n' if target == 'win-x64' else '#!/bin/sh\nexec "$(dirname "$0")/runtime/node" "$(dirname "$0")/app/index.js" "$@"\n')
        launcher.chmod(0o755)
        manifest = {'schema':1,'target':target,'nodeVersion':pins['nodeVersion'],
                    'runtimeArchiveSha256':pins['targets'][target]['sha256'],
                    'sourceLockSha256':digest(lock_bytes),'minimumPlatform':pins['targets'][target]['minimum'],
                    'applicationVersion':json.loads((repo/'package.json').read_text())['version'],
                    'productionPackages':[{'path':loc,'name':v[0],'version':v[1],'lockIntegrity':v[2]} for loc,v in sorted(nodes.items())],
                    'files':{p.relative_to(root).as_posix():digest(p.read_bytes()) for p in sorted(root.rglob('*')) if p.is_file()},
                    'qualification':'Staged bytes only; native execution, installer/service and release acceptance are separate.'}
        (root/'bundle-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        verify_bundle(root)
        os.rename(root, output)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--modules',type=Path,required=True)
    parser.add_argument('--runtime-archive',type=Path,required=True)
    parser.add_argument('--target',choices=['linux-x64','win-x64'],required=True)
    parser.add_argument('--output',type=Path,required=True)
    a=parser.parse_args()
    result=stage(a.repo.resolve(),a.modules.resolve(),a.runtime_archive.resolve(),a.target,a.output.absolute())
    print(json.dumps({'target':result['target'],'files':len(result['files']),'productionPackages':len(result['productionPackages']),'output':str(a.output)}))
