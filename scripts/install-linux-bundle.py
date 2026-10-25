#!/usr/bin/env python3
"""Install an already-qualified Linux bundle; offline, no credential arguments.

Requires root and an explicit --apply. Configuration arrives only through stdin
on first install and is never overwritten or removed. No release version chosen.
"""
import argparse
import grp
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid

sys.dont_write_bytecode=True
spec=importlib.util.spec_from_file_location('stage',Path(__file__).with_name('stage-distribution.py'))
stage=importlib.util.module_from_spec(spec);spec.loader.exec_module(stage)
MARKER='# VMPower Node24 bundle installer\n'
LEGACY_UNIT=(Path(__file__).parents[1]/'packages/debian/vmpower-agent/etc/systemd/system/vmpower.service').read_bytes()


def config_bytes(existing, incoming):
    raw=existing if existing is not None else incoming
    if raw is None: raise ValueError('First install requires configuration JSON on stdin')
    if len(raw)>65536: raise ValueError('Configuration exceeds the supported size')
    try: value=json.loads(raw)
    except (ValueError,UnicodeError): raise ValueError('Invalid configuration JSON') from None
    if not isinstance(value,dict) or any(not isinstance(value.get(k),str) or not value[k].strip() for k in ['key','secret','vm_id']):
        raise ValueError('Configuration requires nonempty key, secret and vm_id')
    if 'host' in value and (not isinstance(value['host'],str) or not value['host'].strip()):
        raise ValueError('Invalid host configuration')
    return existing if existing is not None else (json.dumps(value,ensure_ascii=False)+'\n').encode()


class System:
    def destination(self,path):
        for parent in [path,*path.parents]:
            if parent.is_symlink(): raise ValueError('Linked managed destination')
            if parent.exists() and (parent.stat().st_uid!=0 or parent.stat().st_mode & 0o022):
                raise ValueError('Managed destination must be root owned and not group/world writable')
    def call(self,*args,allowed=(0,)):
        result=subprocess.run(args,capture_output=True,timeout=30,env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin'})
        if result.returncode not in allowed: raise RuntimeError('System operation failed: '+args[0])
        return result.returncode
    def account(self):
        try: account=pwd.getpwnam('vmpower')
        except KeyError:
            self.call('useradd','--system','--user-group','--no-create-home','--home-dir','/nonexistent','--shell','/usr/sbin/nologin','vmpower')
            account=pwd.getpwnam('vmpower')
        if account.pw_uid==0: raise ValueError('Refusing root service account')
        group=grp.getgrgid(account.pw_gid)
        if group.gr_name!='vmpower' or any(name!='vmpower' for name in group.gr_mem) or any(user.pw_gid==account.pw_gid and user.pw_name!='vmpower' for user in pwd.getpwall()):
            raise ValueError('Service account requires a dedicated vmpower group')
        return account.pw_gid
    def config_owner(self,p,gid): os.chown(p,0,gid)
    def stable_child(self,executable):
        # SCM "active" alone can describe an immediately crashing/restarting app.
        initial=None;stable_since=None;deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            result=subprocess.run(['systemctl','show','vmpower.service','--property=MainPID','--value'],capture_output=True,timeout=5,check=True)
            pid=int(result.stdout.strip())
            try: correct=pid>1 and os.readlink('/proc/'+str(pid)+'/exe')==str(executable)
            except FileNotFoundError: correct=False
            if initial is not None and (pid!=initial or not correct): raise RuntimeError('Service child is not stable')
            if correct and initial is None: initial=pid;stable_since=time.monotonic()
            if correct and time.monotonic()-stable_since>=5: return
            time.sleep(0.5)
        raise RuntimeError('Stable service child was not observed')


def atomic_write(path,data,mode):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.vmpower-',dir=path.parent)
    try:
        os.fchmod(fd,mode)
        with os.fdopen(fd,'wb') as f: f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)


def snapshot(path):
    if path.is_symlink(): raise ValueError('Refusing a linked managed file')
    if path.exists(): return (path.read_bytes(),stat.S_IMODE(path.stat().st_mode))
    return None


def restore(path,old):
    if old is None:
        if path.exists(): path.unlink()
    else: atomic_write(path,*old)


def seal_private_release(root):
    # Call only while the copy is below a private (0700) staging directory.
    entries=[root,*root.rglob('*')]
    if any(p.is_symlink() for p in entries): raise ValueError('Copied bundle symlink is not qualified')
    for p in entries:
        if p.is_dir(): p.chmod(0o755)
        elif p.is_file(): p.chmod(0o755 if p==root/'runtime/node' else 0o644)
        else: raise ValueError('Unsupported copied bundle file')


def install(bundle,expected,incoming=None,root=Path('/'),system=None):
    system=system or System()
    if not isinstance(expected,str) or not re.fullmatch(r'[0-9a-f]{64}',expected):
        raise ValueError('Expected exactly 64 lowercase hexadecimal SHA256 characters')
    if bundle.is_symlink() or not bundle.is_dir():
        raise ValueError('Bundle input must be a real directory, not a symlink')
    manifest=stage.verify_bundle(bundle)
    manifest_bytes=(bundle/'bundle-manifest.json').read_bytes()
    approved_digest=hashlib.sha256(manifest_bytes).hexdigest()
    if approved_digest!=expected or manifest['target']!='linux-x64':
        raise ValueError('Expected qualified Linux bundle manifest hash')
    prefix=root/'opt/vmpower-agent';unit=root/'etc/systemd/system/vmpower.service';launcher=root/'usr/bin/vmpower';config=root/'etc/vmpower.conf'
    for p in [prefix,prefix/'rollback',prefix/'releases',unit,launcher,config]:
        if p.is_symlink() or any(parent.is_symlink() for parent in p.parents if parent!=root):
            raise ValueError('Refusing symlink in managed destination')
        system.destination(p)
    old_unit=snapshot(unit);old_launcher=snapshot(launcher)
    if old_unit and old_unit[0]!=LEGACY_UNIT and not old_unit[0].startswith(MARKER.encode()):
        raise ValueError('Existing service unit is not a recognized VMPower unit')
    if config.exists() and (not config.is_file() or config.stat().st_nlink!=1):
        raise ValueError('Configuration must be a regular unlinked file')
    raw=config_bytes(config.read_bytes() if config.exists() else None,incoming)
    gid=system.account()
    prefix.mkdir(parents=True,exist_ok=True);prefix.chmod(0o755)
    backups=prefix/'rollback';backups.mkdir(exist_ok=True);backups.chmod(0o700)
    backup=backups/uuid.uuid4().hex;backup.mkdir(mode=0o700)
    if old_launcher: atomic_write(backup/'vmpower',*old_launcher)
    if old_unit: atomic_write(backup/'vmpower.service',*old_unit)
    releases=prefix/'releases';releases.mkdir(exist_ok=True);releases.chmod(0o755)
    release=releases/approved_digest
    if release.is_symlink(): raise ValueError('Refusing symlink release')
    system.destination(release)
    if release.exists():
        # Previously installed releases must already be protected; do not race
        # a writer by hashing a writable tree and only then repairing its modes.
        for p in release.rglob('*'): system.destination(p)
        if hashlib.sha256((release/'bundle-manifest.json').read_bytes()).hexdigest()!=expected: raise ValueError('Retained release mismatch')
        stage.verify_bundle(release)
    else:
        temporary=Path(tempfile.mkdtemp(prefix='.stage-',dir=releases))
        try:
            shutil.copytree(bundle,temporary/'bundle',symlinks=True)
            seal_private_release(temporary/'bundle')
            if hashlib.sha256((temporary/'bundle/bundle-manifest.json').read_bytes()).hexdigest()!=expected:
                raise ValueError('Copied bundle manifest does not match approved SHA256')
            stage.verify_bundle(temporary/'bundle')
            os.rename(temporary/'bundle',release)
        finally: shutil.rmtree(temporary)
    if not config.exists():
        # Exclusive creation: a concurrent configuration is never replaced.
        config.parent.mkdir(parents=True,exist_ok=True)
        # Keep new bytes private even if root's primary/inherited group is not
        # the service group. Grant group read only after verified ownership below.
        fd=os.open(config,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as f: f.write(raw);f.flush();os.fsync(f.fileno())
    system.config_owner(config,gid);config.chmod(0o640)
    was_active=system.call('systemctl','is-active','--quiet','vmpower.service',allowed=(0,3,4))==0
    was_enabled=system.call('systemctl','is-enabled','--quiet','vmpower.service',allowed=(0,1,3,4))==0
    if was_active: system.call('systemctl','stop','vmpower.service')
    start=old_unit is None or was_active
    try:
        executable='/opt/vmpower-agent/releases/'+approved_digest
        atomic_write(launcher,('#!/bin/sh\n'+MARKER+'exec "'+executable+'/runtime/node" "'+executable+'/app/index.js" "$@"\n').encode(),0o755)
        text=MARKER+'[Unit]\nDescription=VMPower agent\nAfter=network.target\n\n[Service]\nType=simple\nExecStart=/usr/bin/vmpower\nUser=vmpower\nRestart=on-failure\nRestartSec=10\nNoNewPrivileges=true\n\n[Install]\nWantedBy=multi-user.target\n'
        atomic_write(unit,text.encode(),0o644)
        system.call('systemctl','daemon-reload')
        if old_unit is None or was_enabled: system.call('systemctl','enable','vmpower.service')
        if start:
            system.call('systemctl','start','vmpower.service')
            system.call('systemctl','is-active','--quiet','vmpower.service')
            system.stable_child(release/'runtime/node')
    except Exception:
        system.call('systemctl','stop','vmpower.service',allowed=(0,5))
        if not was_enabled: system.call('systemctl','disable','vmpower.service',allowed=(0,1))
        restore(unit,old_unit);restore(launcher,old_launcher)
        system.call('systemctl','daemon-reload')
        if was_enabled: system.call('systemctl','enable','vmpower.service')
        if was_active: system.call('systemctl','start','vmpower.service')
        raise
    return {'state':'installed','configuration':'retained' if incoming is None else 'created-or-retained','release':expected,'started':start,'rollbackFiles':str(backup)}


def uninstall(root=Path('/'),system=None):
    system=system or System();unit=root/'etc/systemd/system/vmpower.service';launcher=root/'usr/bin/vmpower'
    system.destination(unit);system.destination(launcher)
    if unit.is_symlink() or launcher.is_symlink(): raise ValueError('Linked managed file')
    if not unit.exists() or not unit.read_bytes().startswith(MARKER.encode()): raise ValueError('Only this installer managed service can be removed')
    if not launcher.exists() or not launcher.read_bytes().startswith(('#!/bin/sh\n'+MARKER).encode()): raise ValueError('Unrecognized launcher')
    system.call('systemctl','stop','vmpower.service');system.call('systemctl','disable','vmpower.service')
    unit.unlink();launcher.unlink();system.call('systemctl','daemon-reload')
    return {'state':'uninstalled','configuration':'preserved','releases':'preserved','serviceAccount':'preserved'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['install','uninstall']);p.add_argument('--bundle',type=Path);p.add_argument('--expected-manifest-sha256');p.add_argument('--config-stdin',action='store_true');p.add_argument('--apply',action='store_true');a=p.parse_args()
    if not a.apply: p.error('No changes made. Explicit --apply required on a qualified Linux host.')
    if sys.platform!='linux' or os.geteuid()!=0: p.error('Root on Linux required')
    if a.action=='install':
        if not a.bundle or not a.expected_manifest_sha256 or len(a.expected_manifest_sha256)!=64:p.error('Exact bundle and qualified manifest SHA256 required')
        result=install(a.bundle,a.expected_manifest_sha256,sys.stdin.buffer.read(65537) if a.config_stdin else None)
    else: result=uninstall()
    print(json.dumps(result))
