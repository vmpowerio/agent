#!/usr/bin/python3
"""Debian maintainer hook template; builder binds VERSION and EXPECTED.

Do not execute this source template. The same generated hook is installed in all
four maintainer-script slots. Failed activation restores runtime state; dpkg can
remain half-configured until the operator reinstalls the retained previous deb.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import uuid
sys.dont_write_bytecode=True

VERSION = None  # BUILDER_VERSION
EXPECTED = None  # BUILDER_EXPECTED
STATE=Path('/var/lib/vmpower-agent')
UNIT=Path('/etc/systemd/system/vmpower.service')
LAUNCHER=Path('/usr/bin/vmpower')
CONFIG=Path('/etc/vmpower.conf')
LIB=Path('/usr/lib/vmpower-agent')
LEGACY_UNIT_SHA='6fce1ebea22adb4f5ada3d6a8add52cad5264d6b4ff3bc78b6796a6ab34c2564'
LEGACY_BINARY_SHAS={'6e26bae20a2d0a45135c0b8591d41982175cff5b844ffcd2c3a610b1b49e3a80',
                    'fd8bee37cb01a23aff850eb93096a6c8da7aef7ba4e62c548649399420c6849b'}
MARKER=b'# VMPower Node24 bundle installer\n'


def command(*args,allowed=(0,)):
    result=subprocess.run(args,capture_output=True,timeout=35,env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin'})
    if result.returncode not in allowed: raise RuntimeError('Package operation failed: '+args[0])
    return result.returncode


def protected(path):
    for item in [path,*path.parents]:
        if item.is_symlink(): raise ValueError('Linked package state/destination')
        if item.exists():
            info=item.stat()
            if info.st_uid!=0 or info.st_mode & 0o022: raise ValueError('Unprotected package state/destination')
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):raise ValueError('Special package state/destination')
    if path.exists() and path.is_file() and path.stat().st_nlink!=1: raise ValueError('Linked package file')


def write(path,data,mode=0o600):
    protected(path)
    fd,name=tempfile.mkstemp(prefix='.package-',dir=path.parent)
    try:
        os.fchmod(fd,mode)
        with os.fdopen(fd,'wb') as handle: handle.write(data);handle.flush();os.fsync(handle.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def journal():
    protected(STATE/'pending.json')
    value=json.loads((STATE/'pending.json').read_bytes())
    if value.get('schema')!=1 or not isinstance(value.get('id'),str) or len(value['id'])!=32 or any(c not in '0123456789abcdef' for c in value['id']):
        raise ValueError('Unrecognized package transaction')
    return value


def save(value):write(STATE/'pending.json',(json.dumps(value)+'\n').encode())


def restore(value):
    directory=STATE/value['id'];protected(directory)
    for name,target in [('unit',UNIT),('launcher',LAUNCHER)]:
        protected(target)
        entry=value['files'][name]
        if entry is None:
            if target.exists():target.unlink()
        else:
            source=directory/name;protected(source);raw=source.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=entry['sha256']:raise ValueError('Rollback snapshot mismatch')
            write(target,raw,entry['mode'])


def configuration():
    protected(CONFIG)
    existing=CONFIG.exists()
    if existing:
        if not CONFIG.is_file() or CONFIG.stat().st_size>65536:raise ValueError('Invalid configuration file')
        raw=CONFIG.read_bytes();value=json.loads(raw)
    else:
        value={key:os.environ.get(env,'') for key,env in [('key','API_KEY'),('secret','API_SECRET'),('vm_id','VM_ID')]}
        raw=(json.dumps(value)+'\n').encode()
    if not isinstance(value,dict) or any(not isinstance(value.get(k),str) or not value[k].strip() for k in ('key','secret','vm_id')):
        raise ValueError('Existing JSON configuration or API_KEY/API_SECRET/VM_ID required')
    if not existing:
        # Exclusive 0600 creation preserves the legacy first-install environment contract.
        fd=os.open(CONFIG,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as handle:handle.write(raw);handle.flush();os.fsync(handle.fileno())


def preinst():
    for target in (STATE,UNIT,LAUNCHER,CONFIG):protected(target)
    if UNIT.exists():
        unit=UNIT.read_bytes()
        if hashlib.sha256(unit).hexdigest()==LEGACY_UNIT_SHA:
            if not LAUNCHER.is_file() or LAUNCHER.stat().st_size>128*1024*1024 or hashlib.sha256(LAUNCHER.read_bytes()).hexdigest() not in LEGACY_BINARY_SHAS:
                raise ValueError('Unqualified legacy executable family')
        elif not unit.startswith(MARKER):raise ValueError('Unrecognized existing service unit')
    if (STATE/'pending.json').exists() and journal().get('state') not in ('complete','runtime-restored'):
        raise ValueError('Unfinished package transaction requires reconciliation')
    configuration()
    STATE.mkdir(mode=0o700,exist_ok=True);STATE.chmod(0o700)
    value={'schema':1,'id':uuid.uuid4().hex,'version':VERSION,'state':'prepared','files':{},
           'active':command('systemctl','is-active','--quiet','vmpower.service',allowed=(0,3,4))==0,
           'enabled':command('systemctl','is-enabled','--quiet','vmpower.service',allowed=(0,1,3,4))==0}
    directory=STATE/value['id'];directory.mkdir(mode=0o700)
    for name,target in [('unit',UNIT),('launcher',LAUNCHER)]:
        if target.exists():
            if not target.is_file() or target.stat().st_size>128*1024*1024:raise ValueError('Invalid existing package file')
            raw=target.read_bytes();write(directory/name,raw)
            value['files'][name]={'sha256':hashlib.sha256(raw).hexdigest(),'mode':stat.S_IMODE(target.stat().st_mode)}
        else:value['files'][name]=None
    save(value)


def activate():
    spec=importlib.util.spec_from_file_location('package_installer',LIB/'scripts/install-linux-bundle.py')
    installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
    installer.install(LIB/'bundle',EXPECTED)


def postinst():
    value=journal()
    if value['version']!=VERSION:raise ValueError('Package transaction version mismatch')
    if value['state'] not in ('prepared','runtime-restored','complete'):raise ValueError('Invalid package transaction state')
    if value['state']=='complete':
        preinst();value=journal()  # Reconfigure snapshots the currently installed release.
    restore(value)
    try:
        activate()
    except Exception:
        command('systemctl','stop','vmpower.service',allowed=(0,5))
        restore(value);command('systemctl','daemon-reload')
        command('systemctl','enable' if value['enabled'] else 'disable','vmpower.service',allowed=(0,1))
        if value['active']:command('systemctl','start','vmpower.service')
        value['state']='runtime-restored';save(value)
        raise RuntimeError('Activation failed; runtime restored. Reinstall the retained previous deb to reconcile dpkg.') from None
    value['state']='complete';save(value)


def main(phase,args):
    if VERSION is None or EXPECTED is None:raise ValueError('Unbound maintainer-hook template')
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Root on Linux required')
    action=args[0] if args else ''
    if phase=='preinst' and action in ('install','upgrade'):preinst()
    elif phase=='postinst' and action=='configure':postinst()
    elif phase=='prerm' and action=='remove':
        command('systemctl','stop','vmpower.service',allowed=(0,5))
        command('systemctl','disable','vmpower.service',allowed=(0,1))
    elif phase=='postrm' and action in ('remove','purge'):command('systemctl','daemon-reload')
    # Upgrade prerm/postrm deliberately retain the live predecessor until the
    # verified installer's transaction stops it. Config/account/releases survive removal.


if __name__=='__main__':
    try:main(Path(sys.argv[0]).name.rsplit('.',1)[-1],sys.argv[1:])
    except Exception as exc:
        # Configuration, arguments and environment are never printed.
        print('VMPower package hook failed: '+type(exc).__name__+'. Check /var/lib/vmpower-agent/pending.json; if runtime-restored, reinstall the retained previous deb.',file=sys.stderr);sys.exit(1)
