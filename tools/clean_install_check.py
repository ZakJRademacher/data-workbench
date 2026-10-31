"""Verify an extracted download using a standard Python and the public launcher."""
import argparse
import hashlib
import io
import json
import os
import subprocess
import time
import urllib.request
import zipfile
from pathlib import Path

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--workdir',type=Path,required=True);parser.add_argument('--python',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True);args=parser.parse_args()
    scratch=args.workdir.resolve();scratch.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(args.archive) as archive:
        assert all(not n.startswith(('/', '\\')) and '..' not in Path(n).parts for n in archive.namelist())
        archive.extractall(scratch/'download')
    root=scratch/'download/data-workbench';environment=scratch/'environment'
    subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(root/'tools/launch.ps1'),
                    '-Check','-PythonPath',str(args.python.resolve()),'-EnvironmentRoot',str(environment)],check=True)
    python=environment/'Scripts/python.exe';env=dict(os.environ,DATA_WORKBENCH_RUNTIME=str(scratch/'runtime'))
    env.pop('PYTHONPATH',None)
    server=subprocess.Popen(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(root/'tools/launch.ps1'),
        '-NoBrowser','-EnvironmentRoot',str(environment)],
        cwd=root,env=env,stdout=(scratch/'server.log').open('w'),stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    base='http://127.0.0.1:8810';token=None
    def request(path,payload=None):
        headers={'Content-Type':'application/json'}
        if token:headers['X-Workbench-Token']=token
        data=None if payload is None else json.dumps(payload).encode()
        with urllib.request.urlopen(urllib.request.Request(base+path,data=data,headers=headers),timeout=20) as response:
            body=response.read();return json.loads(body) if response.headers.get_content_type()=='application/json' else body
    try:
        for _ in range(100):
            try:session=request('/api/session');break
            except OSError:time.sleep(.1)
        else:raise AssertionError('Extracted app did not start')
        assert session['version']=='2.0.1',session
        assert Path(session['project_root']).resolve()==(root/'projects').resolve(),session
        token=session['token'];project=request('/api/demo',{})
        pid=project['id'];run=request('/api/projects/'+pid+'/runs',{})
        for _ in range(300):
            run=request('/api/projects/'+pid+'/runs/'+run['id'])
            if run['state'] not in ('queued','running'):break
            time.sleep(.1)
        assert run['state']=='completed' and run['manifest']['input_rows']==20 and run['manifest']['review_rows']==6,run
        replay=request('/api/projects/'+pid+'/runs',{});assert replay['id']==run['id']
        package=request('/api/projects/'+pid+'/runs/'+run['id']+'/package')
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            manifest=json.loads(archive.read('manifest.json'))
            for filename,expected in manifest['output_hashes'].items():
                assert hashlib.sha256(archive.read(filename)).hexdigest()==expected
        version=subprocess.check_output([str(python),'-c','import sys; print(sys.version.split()[0])'],text=True).strip()
        record={'passed':True,'archive_sha256':hashlib.sha256(args.archive.read_bytes()).hexdigest(),
                'python':version,'independent_standard_installation':True,'launcher_check':True,
                'public_launcher_started_server':True,'extracted_source_server':True,'demo_input_rows':20,'demo_review_rows':6,
                'verified_replay':True,'download_and_all_output_hashes':True,'port':8810}
        args.evidence.parent.mkdir(parents=True,exist_ok=True);args.evidence.write_text(json.dumps(record,indent=2),encoding='utf-8')
        print(json.dumps(record))
    finally:
        if server.poll() is None:
            subprocess.run(['taskkill','/PID',str(server.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
        server.wait(timeout=15)

if __name__=='__main__':main()
