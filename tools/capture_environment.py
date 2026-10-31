"""Capture runtime pins and actual installed third-party license texts."""
import importlib.metadata as metadata
import json
import sys
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]


def main():
    pending=['fastapi','uvicorn','python-multipart','duckdb','openpyxl']
    found={}
    while pending:
        name=pending.pop().lower().replace('_','-')
        if name in found: continue
        distribution=metadata.distribution(name)
        found[name]=distribution
        for line in distribution.requires or []:
            req=Requirement(line)
            if req.marker is None or req.marker.evaluate({'extra':''}):
                pending.append(req.name)
    (ROOT/'requirements.lock.txt').write_text('\n'.join(f'{name}=={d.version}' for name,d in sorted(found.items()))+'\n',encoding='utf-8')
    license_dir=ROOT/'licenses';license_dir.mkdir(exist_ok=True)
    summaries=[]
    for name,dist in sorted(found.items()):
        texts=[]
        for f in dist.files or []:
            if '.dist-info/' in str(f).replace('\\','/').lower() and any(t in Path(f).name.lower() for t in ('license','licence','copying','notice','authors')):
                path=dist.locate_file(f)
                if path.is_file(): texts.append(f'--- {Path(f).name} ---\n'+path.read_text(encoding='utf-8',errors='replace'))
        declared=dist.metadata.get('License-Expression') or dist.metadata.get('License') or 'See package metadata and included texts.'
        summaries.append({'name':name,'version':dist.version,'license':declared,'license_texts_captured':len(texts)})
        (license_dir/(name+'.txt')).write_text(f'{name} {dist.version}\nDeclared license: {declared}\n\n'+'\n\n'.join(texts),encoding='utf-8')
    (license_dir/'dependencies.json').write_text(json.dumps(summaries,indent=2),encoding='utf-8')
    (license_dir/'README.md').write_text('# Dependency licenses\n\nRuntime package names and exact versions are in `requirements.lock.txt`. The accompanying text files retain license/notice texts from those installed packages. `dependencies.json` records each package’s declared license and captured text count. These are actual dependency identifiers. The app does not bundle Python environments or dependency binaries in this project library.\n',encoding='utf-8')
    print(f'Captured {len(found)} runtime dependency pins and license records.')


if __name__=='__main__': main()
