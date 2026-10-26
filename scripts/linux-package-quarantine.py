#!/usr/bin/env python3
"""Bounded whole-runner package quarantine; only the disposable native fixture.

No package script runs until both root and service-user direct socket controls
pass. A separate-session watchdog restores runner connectivity after terminating
the qualification process group on deadline. It never removes the service-UID
rules, so a systemd child stays denied until the existing always-cleanup runs.
"""
import argparse
import errno
import inspect
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import subprocess
import sys
import time

LIMIT_SECONDS=420
SELF=Path(__file__).resolve()

def guard():
    if not (sys.platform=='linux' and os.geteuid()==0 and os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted' and os.environ.get('ImageOS')=='ubuntu24'):
        raise RuntimeError('Package quarantine requires admitted disposable Ubuntu24')

def rules(run):
    if not re.fullmatch(r'[0-9]+',run):raise ValueError('Numeric native run identity required')
    return [[binary,'!','-o','lo','-m','comment','--comment','VMPOWER_PACKAGE_'+run,'-j','REJECT'] for binary in ('iptables','ip6tables')]

def command(*args,allowed=(0,)):
    result=subprocess.run(args,capture_output=True,text=True,timeout=15)
    if result.returncode not in allowed:raise RuntimeError('Package quarantine command failed: '+args[0])
    return result

def remove_rules(run):
    for binary,*args in rules(run):
        if command(binary,'-w','-C','OUTPUT',*args,allowed=(0,1)).returncode==0:
            command(binary,'-w','-D','OUTPUT',*args)
        if command(binary,'-w','-C','OUTPUT',*args,allowed=(0,1)).returncode!=1:
            raise RuntimeError('Package quarantine rule remains')

def identity(pid):
    try:return Path('/proc/'+str(pid)+'/stat').read_text().split(') ',1)[1].split()[19]
    except FileNotFoundError:return None

def watchdog(pid,start,run):
    guard();rules(run)
    # Never accept an arbitrary group from an unvalidated caller.
    argv=Path('/proc/'+str(pid)+'/cmdline').read_bytes().split(b'\0')
    if identity(pid)!=start or os.getpgid(pid)!=pid or not any(item.endswith(b'/qualify-native-linux.py') for item in argv) or b'--legacy-dpkg' not in argv:
        raise RuntimeError('Quarantine watchdog owner mismatch')
    print('READY',flush=True)
    time.sleep(LIMIT_SECONDS)
    # Kill package/maintainer descendants before restoring host connectivity.
    # The pre-existing service-UID rules continue to cover systemd children.
    if identity(pid) in (None,start):
        try:os.killpg(pid,signal.SIGKILL)
        except ProcessLookupError:pass
    remove_rules(run)

def probe(port):
    with socket.create_connection(('127.0.0.1',port),timeout=5):pass
    codes={}
    for family,address in [(socket.AF_INET,'1.1.1.1'),(socket.AF_INET6,'2606:4700:4700::1111')]:
        with socket.socket(family,socket.SOCK_STREAM) as connection:
            connection.settimeout(5)
            try:connection.connect((address,443))
            except OSError as exc:
                if exc.errno not in (errno.EACCES,errno.EPERM,errno.EHOSTUNREACH,errno.ENETUNREACH,errno.ECONNREFUSED):
                    raise RuntimeError('Explicit package egress denial unproven') from None
                codes['ipv4' if family==socket.AF_INET else 'ipv6']=errno.errorcode[exc.errno]
            else:raise RuntimeError('Package quarantine allowed external connection')
    return {'loopback':'PASS','external':'DENIED','codes':codes}

def begin(native,out,port):
    guard();run=os.environ['GITHUB_RUN_ID'];expected=rules(run)
    owned=json.loads((out/'owned.json').read_text())
    if 'packageQuarantine' in owned:raise RuntimeError('Package quarantine already journaled')
    for binary,*args in expected:
        if command(binary,'-w','-C','OUTPUT',*args,allowed=(0,1)).returncode!=1:
            raise RuntimeError('Package quarantine rule already exists')
    # The watchdog may kill this group, never the workflow shell/runner group.
    if os.getpgrp()!=os.getpid():os.setsid()
    if os.getpgrp()!=os.getpid():raise RuntimeError('Private qualifier process group required')
    parent=os.getpid();start=identity(parent)
    watcher=subprocess.Popen([sys.executable,str(SELF),'--watchdog',str(parent),start,run],start_new_session=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    owned['packageQuarantine']={'run':run,'watchdogPid':watcher.pid,'watchdogStart':identity(watcher.pid),'seconds':LIMIT_SECONDS}
    native.write(out/'owned.json',owned)
    ready,_,_=select.select([watcher.stdout],[],[],5)
    if not ready or watcher.stdout.readline()!=b'READY\n':raise RuntimeError('Package quarantine watchdog did not become ready')
    watcher.stdout.close()
    # Journal intent before each rule; normal cleanup reconciles partial setup.
    for binary,*args in expected:
        owned['firewall'].append([binary,*args]);native.write(out/'owned.json',owned)
        command(binary,'-w','-I','OUTPUT','1',*args)
        command(binary,'-w','-C','OUTPUT',*args)
    root=probe(port)
    # The checkout can be below private runner ancestors. Pass only this fixed,
    # public probe source; do not widen those ancestors for the service account.
    probe_source='import errno,json,socket\n'+inspect.getsource(probe)+'\nprint(json.dumps(probe('+str(int(port))+')))'
    service=json.loads(command('runuser','-u','vmpower','--',sys.executable,'-c',probe_source).stdout)
    counters=command('iptables','-w','-L','OUTPUT','-n','-v','-x').stdout.splitlines()
    if not any('VMPOWER_PACKAGE_'+run in row.split() and int(row.split()[0])>=2 for row in counters):
        raise RuntimeError('Root and service quarantine packet counters unproven')
    if watcher.poll() is not None:raise RuntimeError('Package quarantine watchdog unavailable')
    return {'status':'PASS','scope':'all identities and protocols, nonloopback IPv4/IPv6','root':root,'service':service,'ipv4RejectPacketsAtLeastTwo':True,'ipv6RuleInstalled':True,'deadlineSeconds':LIMIT_SECONDS}

def finish(owned):
    value=owned.get('packageQuarantine')
    if value is None:return
    guard()
    if value['run']!=os.environ['GITHUB_RUN_ID'] or value['seconds']!=LIMIT_SECONDS:raise RuntimeError('Package quarantine journal mismatch')
    remove_rules(value['run'])
    pid=value['watchdogPid']
    if identity(pid)==value['watchdogStart']:
        argv=Path('/proc/'+str(pid)+'/cmdline').read_bytes().split(b'\0')
        if str(SELF).encode() not in argv or b'--watchdog' not in argv:raise RuntimeError('Package quarantine watchdog mismatch')
        os.kill(pid,signal.SIGTERM)
        try:os.waitpid(pid,0)
        except ChildProcessError:pass

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--watchdog',nargs=3);parser.add_argument('--probe',type=int);args=parser.parse_args()
    if args.watchdog:watchdog(int(args.watchdog[0]),args.watchdog[1],args.watchdog[2])
    elif args.probe:print(json.dumps(probe(args.probe)))
    else:parser.error('Explicit fixture mode required')
