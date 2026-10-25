#!/usr/bin/env python3
"""Hosted only: fetch exact public vendor bytes, stage and label lifecycle fixtures."""
import argparse,hashlib,importlib.util,json,os,platform,shutil,socket,sys,time,urllib.error,urllib.request,zipfile
from pathlib import Path
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('stage',ROOT/'scripts/stage-distribution.py')
stage=importlib.util.module_from_spec(spec);spec.loader.exec_module(stage)
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): raise ValueError('Unexpected vendor redirect')
def download(url,destination,digest):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    for attempt in range(3):
        try:
            with opener.open(url,timeout=60) as response:
                raw=response.read(160*1024*1024+1)
            break
        except urllib.error.HTTPError as error:
            transient=error.code==429 or 500<=error.code<=599
            error.close()
            if not transient or attempt==2:raise RuntimeError('PINNED_DOWNLOAD_HTTP_'+str(error.code)) from None
        except urllib.error.URLError as error:
            reason=error.reason
            transient=isinstance(reason,(TimeoutError,ConnectionError)) or (isinstance(reason,socket.gaierror) and reason.errno==socket.EAI_AGAIN)
            if not transient:raise RuntimeError('PINNED_DOWNLOAD_TRANSPORT_REJECTED') from None
            if attempt==2:raise RuntimeError('PINNED_DOWNLOAD_NETWORK_FAILURE') from None
        except (TimeoutError,ConnectionError):
            if attempt==2:raise RuntimeError('PINNED_DOWNLOAD_NETWORK_FAILURE') from None
        time.sleep(2*(attempt+1))
    if len(raw)>160*1024*1024 or hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Pinned vendor archive mismatch')
    destination.write_bytes(raw)
def main(out):
    if os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted':raise ValueError('Ephemeral hosted runner required')
    if out!=(Path(os.environ['RUNNER_TEMP'])/'vmpower-native').resolve():raise ValueError('Fixed runner fixture directory required')
    target={'Linux':'linux-x64','Windows':'win-x64'}[platform.system()]
    if platform.machine().lower() not in ('x86_64','amd64'):raise ValueError('Native x64 required')
    out.mkdir(exist_ok=False);(out/'evidence').mkdir();pins=json.loads((ROOT/'distribution/runtime-pins.json').read_text())
    pin=pins['targets'][target];archive=out/pin['archive']
    download('https://nodejs.org/dist/v'+pins['nodeVersion']+'/'+pin['archive'],archive,pin['sha256'])
    stage.stage(ROOT,ROOT/'node_modules',archive,target,out/'bundle')
    result={'target':target,'nodeArchiveSha256':pin['sha256'],'bundles':{}}
    for label in ['bundle','upgrade','crashing']:
        bundle=out/label
        if label!='bundle':
            shutil.copytree(out/'bundle',bundle)
            app=bundle/'app/index.js'
            if label=='upgrade':app.write_bytes(app.read_bytes()+b'\n// synthetic native upgrade fixture; never publish\n')
            else:app.write_bytes(b'process.exit(64); // synthetic crashing fixture; never publish\n')
            manifest=json.loads((bundle/'bundle-manifest.json').read_text())
            manifest['files']['app/index.js']=stage.digest(app.read_bytes())
            manifest['qualification']='SYNTHETIC LIFECYCLE FIXTURE; NEVER PUBLISH'
            (bundle/'bundle-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        stage.verify_bundle(bundle)
        result['bundles'][label]=stage.digest((bundle/'bundle-manifest.json').read_bytes())
    if target=='win-x64':
        nssm=json.loads((ROOT/'distribution/nssm-pins.json').read_text());wrapper=out/'nssm.zip'
        download(nssm['url'],wrapper,nssm['archiveSha256'])
        if hashlib.sha1(wrapper.read_bytes()).hexdigest()!=nssm['publisherSha1']:raise ValueError('Publisher checksum mismatch')
        with zipfile.ZipFile(wrapper) as z:
            raw=z.read(nssm['x64Member'])
            if stage.digest(raw)!=nssm['x64Sha256']:raise ValueError('Wrapper mismatch')
            (out/'nssm.exe').write_bytes(raw)
            license_bytes=z.read('nssm-2.24-101-g897c7ad/README.txt')
            if b'NSSM is public domain' not in license_bytes:raise ValueError('Wrapper license mismatch')
            source={name:stage.digest(z.read(name)) for name in z.namelist() if '/src/' in name and not name.endswith('/')}
        result['nssm']={'archiveSha256':nssm['archiveSha256'],'x64Sha256':nssm['x64Sha256'],'licenseSha256':stage.digest(license_bytes),'bundledSourceHashes':source,'signature':'UNRUN','binarySourceReproducibility':'Unknown'}
    result['head']=os.environ.get('QUALIFIED_SOURCE_SHA','Unknown')
    (out/'evidence/prepared.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'prepared':True,'target':target,'fixtureVariants':2}))
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);main(parser.parse_args().output.resolve())
