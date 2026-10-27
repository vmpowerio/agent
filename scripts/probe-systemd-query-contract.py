#!/usr/bin/env python3
"""One fixed hosted-runner query diagnosis; no fixture or application execution."""
import json,os,re,sys,time
import native_linux_commands as commands

def main():
    if not (sys.platform=='linux' and os.geteuid()==0 and
            os.environ.get('GITHUB_ACTIONS')=='true' and
            os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted' and
            os.environ.get('ImageOS')=='ubuntu24'):
        raise RuntimeError('PROBE_HOST_REFUSED')
    run=os.environ.get('GITHUB_RUN_ID','');head=os.environ.get('GITHUB_SHA','')
    if not re.fullmatch('[0-9]+',run) or not re.fullmatch('[0-9a-f]{40}',head):
        raise RuntimeError('PROBE_IDENTITY_REFUSED')
    units=['vmpowerfixture'+run+'.slice','vmpowerqualify'+run+'.scope','vmpowercleanup'+run+'.scope']
    queries=[('VERSION',['--version']),
             ('LIST_UNITS',['list-units','--plain','--full','--no-legend','--no-pager','--all',*units]),
             ('LIST_UNIT_FILES',['list-unit-files','--plain','--full','--no-legend','--no-pager',*units]),
             ('MANAGER_UNIT_PATH',['show','--property=UnitPath','--value'])]
    env={'PATH':'/usr/bin:/bin','LANG':'C','SYSTEMD_COLORS':'0','SYSTEMD_PAGER':'cat'}
    commands.LIMIT=8192;commands.EVIDENCE=None;commands.handle_signals()
    started=time.monotonic();results=[]
    for tag,args in queries:
        remaining=18-(time.monotonic()-started)
        if remaining<=0:raise RuntimeError('PROBE_DEADLINE')
        try:
            # Exit 1 is recorded as DATA only. It grants no admission permission.
            p=commands.run('/usr/bin/systemctl',*args,allowed=tuple(range(256)),timeout=min(4,remaining),env=env)
            stderr=p.stderr.strip().lower();labels=[]
            for label,prefix in [('UNKNOWN_METHOD','unknown method'),('OPTION_UNSUPPORTED','unrecognized option'),
                                 ('MANAGER_UNAVAILABLE','failed to connect to bus'),('QUERY_ERROR','failed to list unit files')]:
                if stderr.startswith(prefix):labels.append(label)
            row={'queryTag':tag,'state':'COMPLETED','exitCode':p.returncode,
                 'stdoutEmpty':not bool(p.stdout.strip()),'stderrEmpty':not bool(stderr),
                 'stdoutLines':len(p.stdout.splitlines()),'stdoutWords':len(p.stdout.split()),
                 'stderrLabels':labels or (['UNCLASSIFIED_WITHHELD'] if stderr else [])}
            if tag=='VERSION':
                match=re.match(r'^systemd ([0-9]+)(?:\s|$)',p.stdout)
                row['systemdMajor']=int(match.group(1)) if match else None
        except commands.CommandFailure as exc:
            row={'queryTag':tag,'state':exc.record['classification'],'exitCode':exc.record['exitCode'],'outputWithheld':True}
        results.append(row)
    print(json.dumps({'state':'READ_ONLY_QUERY_RESULTS_NOT_NATIVE_ACCEPTANCE','source':head,
                      'results':results,'fixtureOrPackageOrPolicyMutation':False,
                      'rawOutputOrEnvironmentExported':False}))

if __name__=='__main__':
    try:main()
    except BaseException:
        print('FIXED_QUERY_PROBE_REFUSED_NO_NATIVE_ACCEPTANCE');sys.exit(1)
