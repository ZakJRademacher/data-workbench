"""Freeze the five benchmark sources. Bulk inputs are kept in a caller-selected cache."""
import argparse
import csv
import hashlib
import json
import shutil
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from datetime import datetime, timezone

LIBRARY = Path.home() / 'OneDrive' / 'Data Analytics Projects'
ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()


def download(url,path):
    if path.exists(): return
    path.parent.mkdir(parents=True,exist_ok=True)
    req=urllib.request.Request(url,headers={'User-Agent':'DataWorkbenchBenchmark/2.0'})
    temporary=path.with_suffix(path.suffix+'.part')
    with urllib.request.urlopen(req,timeout=300) as response, temporary.open('wb') as f:
        shutil.copyfileobj(response,f)
    temporary.replace(path)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--cache',type=Path,required=True)
    args=parser.parse_args();cache=args.cache.resolve();cache.mkdir(parents=True,exist_ok=True)
    records=[]
    def record(name,url,files,license,selection,acquisition='downloaded',metadata=None):
        records.append({'name':name,'source_url':url,'license':license,'selection':selection,'acquisition':acquisition,
            'source_metadata':metadata,
            'files':[{'filename':p.name,'sha256':sha(p),'bytes':p.stat().st_size,'cache_relative':str(p.relative_to(cache))} for p in files]})
        print(name+' frozen',flush=True)
        frozen={'frozen_at_utc':datetime.now(timezone.utc).isoformat(),'sources':records,'bulk_cache_location':'Caller-selected directory outside the project library'}
        (cache/'source-manifest.json').write_text(json.dumps(frozen,indent=2),encoding='utf-8')
        (ROOT/'evidence/external-source-manifest.json').write_text(json.dumps(frozen,indent=2),encoding='utf-8')
    uci=cache/'uci';download('https://archive.ics.uci.edu/static/public/352/online+retail.zip',uci/'online-retail.zip')
    with zipfile.ZipFile(uci/'online-retail.zip') as z:
        member=next(n for n in z.namelist() if n.lower().endswith('.xlsx'))
        workbook=uci/'Online Retail.xlsx'
        if not workbook.exists():
            with z.open(member) as source, workbook.open('wb') as target:shutil.copyfileobj(source,target)
    record('UCI Online Retail','https://archive.ics.uci.edu/dataset/352/online+retail',[workbook],'CC BY 4.0','Complete workbook; no record sampling')
    for name,domain,view,where,order in [
        ('Chicago Food Inspections','data.cityofchicago.org','4ijn-s7e5',"inspection_date < '2020-01-01T00:00:00.000'",'inspection_id'),
        ('NYC 311 historical','data.cityofnewyork.us','76ig-c548',"created_date >= '2019-01-01T00:00:00.000' AND created_date < '2020-01-01T00:00:00.000'",'unique_key')]:
        folder=cache/('chicago' if name.startswith('Chicago') else 'nyc');folder.mkdir(exist_ok=True)
        query={'$where':where,'$order':order,'$limit':'50000'}
        if name.startswith('NYC'):
            query['$select']='unique_key,created_date,closed_date,agency,agency_name,complaint_type,descriptor,incident_zip,city,status,borough,resolution_description,resolution_action_updated_date,latitude,longitude,location_type'
        url=f'https://{domain}/resource/{view}.csv?'+urllib.parse.urlencode(query)
        path=folder/'source.csv'
        if name.startswith('NYC') and not path.exists():
            from fetch_nyc import main as fetch
            import subprocess,sys
            subprocess.run([sys.executable,str(ROOT/'tools/fetch_nyc.py'),'--cache',str(cache)],check=True)
        else:download(url,path)
        download(f'https://{domain}/api/views/{view}.json',folder/'metadata.json')
        with path.open(encoding='utf-8-sig',newline='') as f:count=sum(1 for _ in csv.DictReader(f))
        if count!=50000:raise RuntimeError(f'{name}: expected 50000 records, received {count}')
        metadata=json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
        selection=json.loads((folder/'selection.json').read_text()) if (folder/'selection.json').exists() else {'query':query,'rows':count}
        record(name,f'https://{domain}/d/{view}',[path],metadata.get('licenseId') or 'See publisher terms; no bulk redistribution',selection)
    superstore=cache/'superstore';superstore.mkdir(exist_ok=True)
    job=LIBRARY/'01 Portfolio Projects/cloud-file-intake/local-data/workbench/jobs/ed70472a5bde78ebf06548051e1988a0a82506e732e9a17bcd77121018d61d9a'
    target=superstore/'Sample - Superstore.csv'
    if not target.exists():
        source=next(job.rglob('00.csv'),None)
        if source is None:raise RuntimeError('Download the Superstore CSV from the documented Kaggle page and place it in cache/superstore/Sample - Superstore.csv.')
        shutil.copyfile(source,target)
    if sha(target)!='58fb59c63089e564d835e4aabe19d371757de1137a5bf1888243ae386c4b7a97':raise RuntimeError('Superstore hash does not match the frozen benchmark version')
    super_metadata=superstore/'kaggle-view-metadata.json'
    download('https://www.kaggle.com/api/v1/datasets/view/vivek468/superstore-dataset-final',super_metadata)
    record('Kaggle Superstore','https://www.kaggle.com/datasets/vivek468/superstore-dataset-final',[target],
        'Other (specified in description); educational purposes only; credits original Tableau creators; no raw redistribution',
        'Complete 9994-row cached source','Existing October 29 download; SHA-256 verified against prior acquisition evidence',
        {'filename':super_metadata.name,'sha256':sha(super_metadata),'licenseName':json.loads(super_metadata.read_text())['licenseName']})
    olist=cache/'olist';olist.mkdir(exist_ok=True)
    original=LIBRARY/'01 Portfolio Projects/commerce-operations-powerbi'
    manifest_path=olist/'prior-source-manifest.json'
    if manifest_path.exists():manifest=json.loads(manifest_path.read_text())
    elif (original/'data/processed/snapshots/954dbeb91a2096f6/manifest.json').exists():manifest=json.loads((original/'data/processed/snapshots/954dbeb91a2096f6/manifest.json').read_text())
    else:raise RuntimeError('Download the six documented Olist files from Kaggle, then use the frozen source hashes in evidence/external-source-manifest.json to prepare prior-source-manifest.json.')
    (olist/'prior-source-manifest.json').write_text(json.dumps(manifest,indent=2))
    files=[]
    for filename,expected in manifest['source_hashes'].items():
        target=olist/filename
        if not target.exists():shutil.copyfile(original/'data/raw'/filename,target)
        if sha(target)!=expected:raise RuntimeError('Olist source fingerprint changed: '+filename)
        files.append(target)
    olist_metadata=olist/'kaggle-view-metadata.json'
    download('https://www.kaggle.com/api/v1/datasets/view/olistbr/brazilian-ecommerce',olist_metadata)
    record('Kaggle Olist','https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce',files,'CC BY-NC-SA 4.0','Six original related CSV tables; no geolocation table','Existing October 16 download; raw per-file hashes checked by benchmark against prior snapshot manifest',
        {'filename':olist_metadata.name,'sha256':sha(olist_metadata),'licenseName':json.loads(olist_metadata.read_text())['licenseName']})
    frozen={'frozen_at_utc':datetime.now(timezone.utc).isoformat(),'sources':records,'bulk_cache_location':'Caller-selected directory outside the project library'}
    (cache/'source-manifest.json').write_text(json.dumps(frozen,indent=2),encoding='utf-8')
    (ROOT/'evidence/external-source-manifest.json').write_text(json.dumps(frozen,indent=2),encoding='utf-8')


if __name__=='__main__':main()
