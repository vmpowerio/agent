#!/usr/bin/env python3
"""Build the versioned Linux deb from a verified bundle; never install or execute it.

The actual hooks are shared with disposable dpkg qualification. Version 0.2.0
(or an explicit prerelease thereof) exceeds both observed legacy 0.1 families.
Publication and supported-host qualification remain separate root-owned gates.
"""
import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import tarfile

sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('deb_installer',ROOT/'scripts/install-linux-bundle.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)


def tar_bytes(files):
    output=io.BytesIO()
    with tarfile.open(fileobj=output,mode='w',format=tarfile.USTAR_FORMAT) as archive:
        directories=sorted({str(parent) for name in files for parent in Path(name).parents if str(parent)!='.'},key=lambda x:(x.count('/'),x))
        for name in directories:
            entry=tarfile.TarInfo('./'+name);entry.type=tarfile.DIRTYPE;entry.mode=0o755;entry.uid=entry.gid=0;archive.addfile(entry)
        for name,(raw,mode) in sorted(files.items()):
            entry=tarfile.TarInfo('./'+name);entry.size=len(raw);entry.mode=mode;entry.uid=entry.gid=0
            archive.addfile(entry,io.BytesIO(raw))
    return gzip.compress(output.getvalue(),mtime=0)


def ar_bytes(members):
    data=bytearray(b'!<arch>\n')
    for name,body in members:
        header=f'{name+"/":<16}{0:<12}{0:<6}{0:<6}{"100644":<8}{len(body):<10}`\n'.encode()
        if len(header)!=60:raise ValueError('Invalid ar member')
        data.extend(header);data.extend(body)
        if len(body)%2:data.extend(b'\n')
    return bytes(data)


def package_files(bundle,expected,version):
    if not re.fullmatch(r'0\.2\.0(?:~[a-z0-9][a-z0-9.]*)?',version):raise ValueError('Explicit 0.2.0 release/prerelease required')
    if not re.fullmatch(r'[0-9a-f]{64}',expected):raise ValueError('Exact lowercase bundle SHA256 required')
    if bundle.is_symlink() or not bundle.is_dir():raise ValueError('Real bundle directory required')
    manifest=installer.stage.verify_bundle(bundle)
    raw_manifest=(bundle/'bundle-manifest.json').read_bytes()
    if hashlib.sha256(raw_manifest).hexdigest()!=expected or manifest['target']!='linux-x64':raise ValueError('Qualified Linux manifest mismatch')
    payload={}
    for name,digest in manifest['files'].items():
        raw=(bundle/name).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Bundle changed during packaging')
        payload['usr/lib/vmpower-agent/bundle/'+name]=(raw,0o755 if name=='runtime/node' else 0o644)
    payload['usr/lib/vmpower-agent/bundle/bundle-manifest.json']=(raw_manifest,0o644)
    for name in ('install-linux-bundle.py','stage-distribution.py'):
        payload['usr/lib/vmpower-agent/scripts/'+name]=((ROOT/'scripts'/name).read_bytes(),0o644)
    legacy='packages/debian/vmpower-agent/etc/systemd/system/vmpower.service'
    payload['usr/lib/vmpower-agent/'+legacy]=((ROOT/legacy).read_bytes(),0o644)
    payload['usr/bin/vmpower']=(installer.launcher_bytes(expected),0o755)
    payload['etc/systemd/system/vmpower.service']=(installer.unit_bytes(),0o644)
    # Configuration and retained /opt releases intentionally are not dpkg data files.
    control=(f'Package: vmpower-agent\nVersion: {version}\nArchitecture: amd64\n'
             'Maintainer: VMPower\nPriority: optional\n'
             'Pre-Depends: python3 (>= 3.9)\nDepends: systemd, passwd, libc6 (>= 2.28), libstdc++6, libgcc-s1\n'
             'Description: VMPower agent with a pinned Node24 runtime\n').encode()
    template=(ROOT/'scripts/linux-deb-hook.py').read_text()
    hook=template.replace('VERSION = None  # BUILDER_VERSION','VERSION = '+repr(version)).replace('EXPECTED = None  # BUILDER_EXPECTED','EXPECTED = '+repr(expected)).encode()
    controls={'control':(control,0o644),'md5sums':((''.join(hashlib.md5(raw,usedforsecurity=False).hexdigest()+'  '+name+'\n' for name,(raw,_) in sorted(payload.items()))).encode(),0o644)}
    controls.update({phase:(hook,0o755) for phase in ('preinst','postinst','prerm','postrm')})
    return payload,controls


def build(bundle,expected,version,output):
    if output.exists() or output.is_symlink():raise ValueError('Output already exists')
    payload,controls=package_files(bundle,expected,version)
    raw=ar_bytes([('debian-binary',b'2.0\n'),('control.tar.gz',tar_bytes(controls)),('data.tar.gz',tar_bytes(payload))])
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as handle:handle.write(raw);handle.flush();os.fsync(handle.fileno())
    return {'package':'vmpower-agent','version':version,'architecture':'amd64','sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'bundleManifestSha256':expected,'maintainerHookSha256':hashlib.sha256(controls['postinst'][0]).hexdigest(),'files':len(payload)}


def read_package(path):
    """Reopen our fixed three-member deb without extracting or executing anything."""
    before=path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or not 0<before.st_size<=160*1024*1024:
        raise ValueError('Bounded nonempty single-link package required')
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW),'rb') as handle:
        opened=os.fstat(handle.fileno())
        if (opened.st_dev,opened.st_ino)!=(before.st_dev,before.st_ino):raise ValueError('Package changed while opening')
        raw=handle.read(160*1024*1024+1)
        after=os.fstat(handle.fileno())
    current=path.lstat()
    if len(raw)!=before.st_size or any((s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)!=(before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns) for s in (after,current)):
        raise ValueError('Package changed while reading')
    if raw[:8]!=b'!<arch>\n':raise ValueError('Invalid deb archive')
    position=8;members={}
    for name in ('debian-binary','control.tar.gz','data.tar.gz'):
        header=raw[position:position+60]
        try:size=int(header[48:58])
        except ValueError:raise ValueError('Invalid deb member size') from None
        expected=f'{name+"/":<16}{0:<12}{0:<6}{0:<6}{"100644":<8}{size:<10}`\n'.encode()
        if header!=expected or not 0<size<=len(raw)-position-60:raise ValueError('Invalid deb member')
        members[name]=raw[position+60:position+60+size];position+=60+size
        if size%2:
            if raw[position:position+1]!=b'\n':raise ValueError('Invalid deb padding')
            position+=1
    if position!=len(raw) or members['debian-binary']!=b'2.0\n':raise ValueError('Unexpected deb members')
    archives={}
    for name in ('control.tar.gz','data.tar.gz'):
        files={};seen=set();total=0
        with gzip.GzipFile(fileobj=io.BytesIO(members[name])) as compressed:
            tar=compressed.read(512*1024*1024+1)
        if len(tar)>512*1024*1024:raise ValueError('Uncompressed archive bound exceeded')
        with tarfile.open(fileobj=io.BytesIO(tar),mode='r:') as archive:
            for entry in archive:
                total+=entry.size
                if len(seen)>=20000 or entry.size<0 or total>512*1024*1024 or entry.name in seen:raise ValueError('Archive bounds or duplicate member')
                seen.add(entry.name)
                if not entry.name.startswith('./') or any(x in ('','..','.') for x in entry.name[2:].split('/')) or entry.uid or entry.gid or entry.mtime:
                    raise ValueError('Unexpected archive metadata')
                if entry.isdir():
                    if entry.mode!=0o755 or entry.size:raise ValueError('Unexpected directory metadata')
                elif entry.isfile():files[entry.name[2:]]=(archive.extractfile(entry).read(),entry.mode)
                else:raise ValueError('Package links or special files refused')
        expected_names={'./'+f for f in files}|{'./'+str(parent) for f in files for parent in Path(f).parents if str(parent)!='.'}
        if seen!=expected_names:raise ValueError('Unexpected archive directory inventory')
        archives[name]=files
    return {'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)},archives


def verify_package(path,bundle,expected,version,receipt):
    measured,archives=read_package(path)
    if measured!={k:receipt[k] for k in ('sha256','bytes')}:raise ValueError('Retained package size/hash mismatch')
    payload,controls=package_files(bundle,expected,version)
    if archives!={'data.tar.gz':payload,'control.tar.gz':controls}:raise ValueError('Reopened package differs from qualified inputs')
    return {**measured,'archiveReopened':True,'exactControlAndData':True,'files':len(payload)}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--bundle',type=Path,required=True);parser.add_argument('--expected-manifest-sha256',required=True);parser.add_argument('--version',required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    print(json.dumps(build(args.bundle,args.expected_manifest_sha256,args.version,args.output)))
