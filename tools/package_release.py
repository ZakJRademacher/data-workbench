"""Build a curated source download, excluding user projects and development files."""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FOLDERS={'workbench','web','tests','tools','docs','licenses','evidence'}
FILES={'.gitignore','LICENSE','README.md','requirements.txt','requirements.lock.txt','requirements-dev.txt','Start Workbench.cmd'}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path);args=parser.parse_args()
    output=args.output or ROOT/'deliverables/Data-Workbench-2.0.0.zip'
    output.parent.mkdir(parents=True,exist_ok=True)
    paths=[]
    for path in ROOT.rglob('*'):
        relative=path.relative_to(ROOT)
        if not path.is_file() or any(p in {'__pycache__','.pytest_cache','.git'} for p in relative.parts):continue
        if (len(relative.parts)==1 and path.name in FILES) or relative.parts[0] in FOLDERS:
            paths.append(path)
        elif relative.as_posix()=='deliverables/Northstar demo package.zip':paths.append(path)
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(paths):archive.write(path,Path('data-workbench')/path.relative_to(ROOT))
    record={'filename':output.name,'files':len(paths),'bytes':output.stat().st_size,
            'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
            'excluded':'Private projects, raw external datasets, environments, caches, local paths, and repository administration.'}
    output.with_suffix('.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(json.dumps(record))

if __name__=='__main__':main()
