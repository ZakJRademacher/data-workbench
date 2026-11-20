"""Build the Windows download that carries its own Python, so users install nothing.

The app files are the same ones the source download ships. Next to them goes
`runtime/python`: python.org's embeddable CPython, checked against its published
SHA-256, with the locked dependencies installed into it. `tools/launch.ps1` uses
that interpreter when it is present and never creates a venv or calls pip.

Release builds run on Windows x64 with the same CPython minor version as the
bundle, so pip picks wheels with the target's own environment markers.
`--cross` allows a local trial build from another Python.
"""
import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from workbench.common import VERSION
from tools.package_release import FOLDERS,TEXT,release_paths

PYTHON={'version':'3.14.10',
        'url':'https://www.python.org/ftp/python/3.14.10/python-3.14.10-embed-amd64.zip',
        'sha256':'90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159'}
TAG='python'+''.join(PYTHON['version'].split('.')[:2])  # python314
# Paths in a ._pth file are relative to the file. `..\..` is the app root, so
# `-m workbench.app` resolves; site stays disabled, so PYTHONPATH and user
# site-packages on the user's machine are ignored.
PTH=f'{TAG}.zip\r\n.\r\nLib\\site-packages\r\n..\\..\r\n'

def sha256(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1<<20),b''):digest.update(block)
    return digest.hexdigest()

def fetch_python(cache):
    cache.mkdir(parents=True,exist_ok=True)
    target=cache/PYTHON['url'].rsplit('/',1)[1]
    if not target.exists() or sha256(target)!=PYTHON['sha256']:
        print('Downloading',PYTHON['url'])
        partial=target.with_suffix('.part')
        with urllib.request.urlopen(PYTHON['url'],timeout=120) as response,partial.open('wb') as stream:
            shutil.copyfileobj(response,stream)
        partial.replace(target)
    actual=sha256(target)
    if actual!=PYTHON['sha256']:raise SystemExit(f'{target.name} SHA-256 is {actual}, expected {PYTHON["sha256"]}')
    return target

def install_packages(site,cross):
    major,minor=PYTHON['version'].split('.')[:2]
    # A free-threaded build would make pip pick cp314t wheels, which the bundled
    # standard interpreter cannot load.
    native=(sys.platform=='win32' and platform.machine().lower() in {'amd64','x86_64'}
            and sys.implementation.name=='cpython' and f'{sys.version_info[0]}.{sys.version_info[1]}'==f'{major}.{minor}'
            and not sysconfig.get_config_var('Py_GIL_DISABLED'))
    if not native and not cross:
        raise SystemExit(f'Build with 64-bit Windows CPython {major}.{minor}, or pass --cross for a trial build.')
    command=[sys.executable,'-m','pip','install','--disable-pip-version-check','--no-compile','--no-deps',
             '--only-binary=:all:','--target',str(site),'-r',str(ROOT/'requirements.lock.txt')]
    if not native:
        command+=['--platform','win_amd64','--implementation','cp','--python-version',f'{major}.{minor}','--abi',f'cp{major}{minor}']
    subprocess.run(command,check=True)
    # --target adds console-script launchers that point at the build machine's Python.
    shutil.rmtree(site/'bin',ignore_errors=True)
    for cache in list(site.rglob('__pycache__')):shutil.rmtree(cache,ignore_errors=True)
    return native

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,help='ZIP to write (default: dist/Data-Workbench-<version>-windows-x64.zip)')
    parser.add_argument('--cache',type=Path,default=ROOT/'.portable-cache')
    parser.add_argument('--cross',action='store_true',help='allow a trial build from a non-matching Python')
    args=parser.parse_args()
    output=(args.output or ROOT/f'dist/Data-Workbench-{VERSION}-windows-x64.zip').resolve()
    cache=args.cache.resolve()
    # Anything inside the app folders would be packed into the download itself.
    for path in (output,cache):
        if any(path.is_relative_to(ROOT/folder) for folder in FOLDERS):
            raise SystemExit(f'{path} is inside the app files; choose a location outside {", ".join(sorted(FOLDERS))}.')
    cache.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='stage-',dir=cache))
    try:build(output,cache,stage,args.cross)
    finally:shutil.rmtree(stage,ignore_errors=True)

def build(output,cache,stage,cross):
    runtime=stage/'runtime';python=runtime/'python'
    with zipfile.ZipFile(fetch_python(cache)) as archive:archive.extractall(python)
    (python/f'{TAG}._pth').write_bytes(PTH.encode('ascii'))
    native=install_packages(python/'Lib/site-packages',cross)
    lock=[line.strip() for line in (ROOT/'requirements.lock.txt').read_text(encoding='utf-8').splitlines() if line.strip()]
    marker={'app_version':VERSION,'python':PYTHON['version'],'python_sha256':PYTHON['sha256'],
            'python_source':PYTHON['url'],'packages':lock,'native_build':native}
    (runtime/'portable.json').write_text(json.dumps(marker,indent=2)+'\n',encoding='utf-8',newline='\r\n')

    app=release_paths()
    bundled=sorted(p for p in runtime.rglob('*') if p.is_file())
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for path in app:
            name=(Path('data-workbench')/path.relative_to(ROOT)).as_posix()
            info=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            data=path.read_bytes()
            if path.suffix.lower() in TEXT or path.name in {'.gitignore','LICENSE'}:
                data=data.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')
            archive.writestr(info,data)
        for path in bundled:
            name=(Path('data-workbench')/path.relative_to(stage)).as_posix()
            info=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,path.read_bytes())
    record={'filename':output.name,'files':len(app)+len(bundled),'bytes':output.stat().st_size,'sha256':sha256(output),
            'python':PYTHON['version'],'packages':lock,'native_build':native,
            'excluded':'Private projects, raw external datasets, environments, caches, local paths, and repository administration.'}
    with output.with_suffix('.json').open('w',encoding='utf-8',newline='\n') as stream:
        stream.write(json.dumps(record,indent=2)+'\n')
    print(json.dumps({k:record[k] for k in ('filename','files','bytes','sha256','native_build')}))

if __name__=='__main__':main()
