#!/usr/bin/env python3
"""Stage exact public package bytes before native isolation; never execute them."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import sys

sys.dont_write_bytecode=True
spec=importlib.util.spec_from_file_location('native_preparation',Path(__file__).with_name('prepare-native-inputs.py'))
preparation=importlib.util.module_from_spec(spec);spec.loader.exec_module(preparation)
ORIGIN='https://vmpower.blob.core.windows.net/'
PINS={
    'linux-x64':{'directory':'legacy-debs','assets':[
        ('dashboard-linux.deb','repo/dist/debian/vmpower-agent.amd64.deb',10794560,'06bf0d767e4625db0477f3e23e30b4e36ac6e7a7d50882b3c655c472f45875b1'),
        ('docs-linux.deb','cdn/dist/debian/vmpower-agent.deb',11090764,'e8c0028ea78322e5937becba766654f178e4dfbe1c9817af33e024d93b588311')]},
    'win-x64':{'directory':'legacy-packages','assets':[
        ('dashboard-windows.nupkg','repo/dist/win32/vmpower-agent/vmpower-agent.nupkg',10731882,'1e72a1db993a3b013b8503e8cde2f0492a8bcee18539ee9e1056c2eb203893af'),
        ('docs-windows.nupkg','cdn/dist/win32/vmpower-agent/vmpower-agent.0.0.1.nupkg',10744853,'3b34023ffa2d05b54bdf10abfe9b3c515ab5dccf70af823407dcd1cb976a8956')]},
}

def stage(out,target,download=preparation.download):
    if os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted':
        raise ValueError('Ephemeral hosted runner required')
    out=preparation.require_output(out)
    if any((out/name).exists() or (out/name).is_symlink() for name in ('owned.json','owned-windows.json')):
        raise ValueError('Legacy inputs must be staged before fixture ownership begins')
    if target not in PINS:raise ValueError('Pinned native target required')
    prepared=json.loads((out/'evidence/prepared.json').read_text())
    if prepared['target']!=target:raise ValueError('Prepared native target mismatch')
    pin=PINS[target];destination=out/pin['directory'];destination.mkdir(exist_ok=False)
    assets=[]
    for name,object_path,size,digest in pin['assets']:
        file=destination/name
        download(ORIGIN+object_path,file,digest)
        if file.is_symlink() or not file.is_file() or file.stat().st_nlink!=1:
            raise ValueError('Regular single-link package required')
        raw=file.read_bytes()
        if len(raw)!=size or hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Pinned package bytes differ')
        assets.append({'name':name,'bytes':size,'sha256':digest})
    receipt={'target':target,'assets':assets,'executed':False,'stagedBeforeQuarantine':True}
    (out/'evidence/legacy-inputs.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    target={'Linux':'linux-x64','Windows':'win-x64'}.get(platform.system())
    if platform.machine().lower() not in ('x86_64','amd64'):raise ValueError('Native x64 required')
    print(json.dumps(stage(args.output,target)))
