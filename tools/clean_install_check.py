"""Verify an extracted download using a standard Python and the public launcher."""
import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import time
import urllib.request
import zipfile
from pathlib import Path

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--workdir',type=Path,required=True);parser.add_argument('--python',type=Path)
    parser.add_argument('--portable',action='store_true',help='the archive carries its own Python; use no other')
    parser.add_argument('--evidence',type=Path,required=True);args=parser.parse_args()
    if args.portable==bool(args.python):parser.error('pass exactly one of --python or --portable')
    scratch=args.workdir.resolve()
    # Leftovers from an earlier run could stand in for files the archive lacks.
    if scratch.exists() and any(scratch.iterdir()):parser.error(f'{scratch} is not empty; use a new work folder')
    scratch.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(args.archive) as archive:
        assert all(not n.startswith(('/', '\\')) and '..' not in Path(n).parts for n in archive.namelist())
        archive.extractall(scratch/'download')
    root=scratch/'download/data-workbench';environment=scratch/'environment'
    env=dict(os.environ,DATA_WORKBENCH_RUNTIME=str(scratch/'runtime'))
    for name in ('PYTHONPATH','PYTHONHOME','DATA_WORKBENCH_PYTHON'):env.pop(name,None)
    check=['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(root/'tools/launch.ps1'),'-Check','-EnvironmentRoot',str(environment)]
    if args.portable:
        # Hide every installed Python: only Windows itself stays on PATH.
        system=os.environ.get('SystemRoot',r'C:\Windows')
        env['PATH']=os.pathsep.join([system+r'\System32',system,system+r'\System32\WindowsPowerShell\v1.0'])
        python=root/'runtime/python/python.exe'
    else:
        check+=['-PythonPath',str(args.python.resolve())]
        python=environment/'Scripts/python.exe'
    subprocess.run(check,check=True,env=env)
    if args.portable:assert not environment.exists(),'Portable launch created a venv'
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
        expected=re.search(r'VERSION = "([^"]+)"',(root/'workbench/common.py').read_text(encoding='utf-8')).group(1)
        assert session['version']==expected,session
        assert Path(session['project_root']).resolve()==(root/'projects').resolve(),session
        if args.portable:
            # A venv's python.exe hands off to the base interpreter, so only the portable run can check this.
            owner=subprocess.check_output(['powershell.exe','-NoProfile','-Command',
                '(Get-Process -Id (Get-NetTCPConnection -LocalPort 8810 -State Listen | Select-Object -First 1).OwningProcess).Path'],text=True).strip()
            assert Path(owner).resolve()==python.resolve(),f'Server runs {owner}, expected {python}'
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
                'python':version,'server_executable':str(Path(owner).relative_to(scratch)) if args.portable else 'environment venv',
                'bundled_python':args.portable,'independent_standard_installation':not args.portable,'launcher_check':True,
                'public_launcher_started_server':True,'extracted_source_server':True,'demo_input_rows':20,'demo_review_rows':6,
                'verified_replay':True,'download_and_all_output_hashes':True,'port':8810}
        args.evidence.parent.mkdir(parents=True,exist_ok=True);args.evidence.write_text(json.dumps(record,indent=2),encoding='utf-8')
        print(json.dumps(record))
    finally:
        if server.poll() is None:
            subprocess.run(['taskkill','/PID',str(server.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
        server.wait(timeout=15)

if __name__=='__main__':main()
