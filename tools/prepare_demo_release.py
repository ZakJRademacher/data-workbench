import json
import os
import sys
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from workbench.common import read_json, write_json
from workbench.demo import create_demo
from workbench.store import ProjectStore


def main():
    store=ProjectStore(ROOT/'projects')
    existing=store.list()
    project=existing[0] if existing else create_demo(store)
    run=store.start_run(project['id'],synchronous=True)
    assert run['state']=='completed' and run['manifest']['input_rows']==20 and run['manifest']['review_rows']==6,run
    deliverables=ROOT/'deliverables';deliverables.mkdir(exist_ok=True)
    output=store.run_dir(project['id'],run['id'])/'outputs'
    with zipfile.ZipFile(deliverables/'Northstar demo package.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for path in output.iterdir():
            if path.is_file(): archive.write(path,path.name)
    write_json(ROOT/'evidence'/'demo-check.json',{'passed':True,'project_id':project['id'],'run_id':run['id'],
               'counts':run['manifest']['tables'],'input_rows':20,'review_rows':6,'workbook':str((output/'Review.xlsx').relative_to(ROOT))})
    print(output/'Review.xlsx')
    store.worker.shutdown()


if __name__=='__main__':main()
