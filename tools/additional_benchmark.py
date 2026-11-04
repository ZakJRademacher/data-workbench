"""Three further frozen-source checks against an extracted public release.

Only aggregate evidence is retained. Downloads and generated projects belong in
the caller-selected scratch directory, never in the public repository.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import threading
import time
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPECS = {
    'bank': {'page':'https://archive.ics.uci.edu/dataset/222/bank+marketing',
        'url':'https://archive.ics.uci.edu/static/public/222/bank%2Bmarketing.zip',
        'license':'CC BY 4.0; Moro, Rita and Cortez (2014), DOI 10.24432/C5K306',
        'selection':'Complete bank-additional-full.csv; 41188 records, semicolon-separated. Other variants excluded.',
        'delimiter':';', 'rows':41188, 'missing':[], 'key':[],
        'integers':['age','duration','campaign','pdays','previous'],
        'decimals':['emp.var.rate','cons.price.idx','cons.conf.idx','euribor3m','nr.employed'],
        'policy':'Keep unknown categories, pdays=999 sentinel, legitimate repeats and negatives. No imputation.'},
    'beijing': {'page':'https://archive.ics.uci.edu/dataset/501/beijing+multi+site+air+quality+data',
        'url':'https://archive.ics.uci.edu/static/public/501/beijing%2Bmulti%2Bsite%2Bair%2Bquality%2Bdata.zip',
        'license':'CC BY 4.0; Chen (2017), DOI 10.24432/C5RK5G; original monitoring/weather agencies credited by UCI',
        'selection':'All 12 PRSA station CSVs, March 2013-February 2017; 420768 records. Auxiliary data.csv/test.csv excluded.',
        'delimiter':',', 'rows':420768, 'missing':['NA'], 'key':['station','year','month','day','hour'],
        'integers':['year','month','day','hour'],
        'decimals':['PM2.5','PM10','SO2','NO2','CO','O3','TEMP','PRES','DEWP','RAIN','WSPM'],
        'policy':'Append 12 stations, retain each station/hour. NA is a documented missing marker, never zero; retain negative temperatures.'},
    'usgs': {'page':'https://earthquake.usgs.gov/earthquakes/feed/v1.0/csv.php',
        'url':'https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_month.csv',
        'license':'Credit: U.S. Geological Survey. USGS-produced data are U.S. public domain; see https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits',
        'selection':'Complete all-events past-30-days CSV at acquisition; rolling feed frozen by SHA-256.',
        'delimiter':',', 'missing':[], 'key':['id'],
        'integers':['nst','magNst'],
        'decimals':['latitude','longitude','depth','mag','gap','dmin','rms','horizontalError','depthError','magError'],
        'policy':'Preserve complete UTC timestamps and event IDs as text, missing measurements, negative magnitudes/depths and non-earthquake event types. Explicit numeric contract retains up to 12 nonzero fractional places; unsupported numeric spellings go to review.'}
}


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False),encoding='utf-8')
def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def identifier(header):
    name=re.sub('[^a-z0-9]+','_',header.strip().lower()).strip('_')
    return (name if name and name[0].isalpha() else 'field_'+name)[:60]
def rows(path,delimiter):
    with path.open(encoding='utf-8-sig',newline='') as stream:
        for number,row in enumerate(csv.DictReader(stream,delimiter=delimiter,strict=True),2):
            if None in row or any(v is None for v in row.values()):raise AssertionError('Nonrectangular source record')
            yield number,row
def download(url,path):
    if not path.exists():
        with urllib.request.urlopen(url,timeout=60) as response:data=response.read()
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    return path


def acquire(cache,evidence):
    cache.mkdir(parents=True,exist_ok=True)
    archive=download('https://raw.githubusercontent.com/ZakJRademacher/data-workbench/79d89586061172ac1f6a9a58dc3eb913b4976966/deliverables/Data-Workbench-2.0.0.zip',cache/'published-release.zip')
    app=cache/'release/data-workbench'
    if not app.exists():
        with zipfile.ZipFile(archive) as zipped:
            assert all('..' not in Path(n).parts and not Path(n).is_absolute() for n in zipped.namelist())
            zipped.extractall(cache/'release')
    frozen=[]
    for name,spec in SPECS.items():
        folder=cache/name;folder.mkdir(exist_ok=True)
        path=download(spec['url'],folder/('source.csv' if name=='usgs' else 'source.zip'))
        acquired=datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat()
        if name!='usgs':
            with zipfile.ZipFile(path) as outer:
                member=next(n for n in outer.namelist() if n.endswith('bank-additional.zip' if name=='bank' else 'PRSA2017_Data_20130301-20170228.zip'))
                with zipfile.ZipFile(io.BytesIO(outer.read(member))) as inner:
                    names=[n for n in inner.namelist() if (n.endswith('bank-additional-full.csv') if name=='bank' else Path(n).name.startswith('PRSA_Data_') and n.endswith('.csv'))]
                    assert len(names)==(1 if name=='bank' else 12)
                    for member in names:
                        target=folder/Path(member).name
                        if not target.exists():target.write_bytes(inner.read(member))
        paths=sorted(folder.glob('*.csv'));count=sum(1 for p in paths for _ in rows(p,spec['delimiter']))
        if 'rows' in spec:assert count==spec['rows']
        frozen.append({'case':name,**spec,'acquired_at_utc':acquired,'input_rows':count,
            'transport_archive_sha256':sha(path),'files':[{'filename':p.name,'sha256':sha(p),'bytes':p.stat().st_size} for p in paths]})
        print(f'Frozen {name}: {count} records across {len(paths)} CSVs',flush=True)
    record={'frozen_at_utc':datetime.now(timezone.utc).isoformat(),'application_archive_sha256':sha(archive),
        'application_source':'https://github.com/ZakJRademacher/data-workbench','version':'2.0.0','sources':frozen}
    write(cache/'frozen.json',record);write(evidence/'sources.json',record)
    return app


def value(raw,field):
    """Independent interpretation of the declared CSV/Decimal contract."""
    text=raw.strip() if field.get('trim',True) else raw
    if text=='' or text in field.get('missing',[]):return None,bool(field.get('required')),None
    if field['type']=='text':
        mapping=field.get('mapping',{})
        if field.get('case_insensitive'):
            mapping={k.lower():v for k,v in mapping.items()};text=mapping.get(text.lower(),text)
            allowed={v.lower():v for v in field.get('allowed',[])};text=allowed.get(text.lower(),text)
        else:text=mapping.get(text,text)
        return text,False,None
    if field['type']=='date':
        try:
            parsed=datetime.strptime(text,field.get('date_format','%Y-%m-%d'))
            if field.get('date_exact',True) and parsed.strftime(field.get('date_format','%Y-%m-%d'))!=text:raise ValueError()
            return parsed.date().isoformat(),False,None
        except ValueError:return None,True,'invalid_date'
    spelling=bool(re.fullmatch(r'[+-]?[0-9]+(?:\.[0-9]+)?',text))
    fraction=text.partition('.')[2].rstrip('0')
    with localcontext() as context:
        context.prec=80
        try:
            exact=Decimal(text)
            if not exact.is_finite() or abs(exact)>=Decimal('1e26'):return None,True,'outside_decimal_range'
            typed=exact.quantize(Decimal('1e-12'),rounding=ROUND_HALF_UP)
        except InvalidOperation:return None,True,'invalid_numeric_spelling'
    issue=None
    if not spelling:issue='unsupported_numeric_spelling'
    elif len(fraction)>field.get('decimal_places',12):issue='excess_decimal_precision'
    if field['type']=='integer':
        if exact!=exact.to_integral_value() or not -(2**63)<=exact<2**63:issue='fractional_or_out_of_range_integer'
        typed=typed.to_integral_value(rounding=ROUND_HALF_UP)
        if not -(2**63)<=typed<2**63:typed=None
    if field.get('inferred') and re.fullmatch('[+-]?0[0-9]+',text):issue='inferred_leading_zero'
    return typed,issue is not None,issue


def norm(v):
    if v is None:return None
    if isinstance(v,Decimal):return '0' if not v else format(v.normalize(),'f')
    if hasattr(v,'isoformat'):return v.isoformat()
    return str(v)
def hash_record(digest,source,row,values,status):
    digest.update((json.dumps([source,row,[norm(v) for v in values],status],ensure_ascii=False,separators=(',',':'))+'\n').encode())


def verify_exports(output, name, fields, paths, spec):
    """Check delivered CSV cells against original CSVs, without app helpers."""
    expected={s:hashlib.sha256() for s in ('accepted','review','excluded')}
    expected_counts=Counter()
    for path in paths:
        for number,raw in rows(path,spec['delimiter']):
            converted=[value(raw[h],f) for h,f in zip(raw,fields)]
            status='review' if any(v[1] for v in converted) else 'accepted'
            cells=[]
            for f,(v,_,_) in zip(fields,converted):
                if v is None:v=''
                elif f['type']=='text' and v.lstrip().startswith(('=','+','-','@','\t','\r')):v="'"+v
                cells.append(v)
            hash_record(expected[status],path.name,number,cells,status)
            expected_counts[status]+=1
    for status in expected:
        actual=hashlib.sha256();count=0
        filename=name+'.csv' if status=='accepted' else name+'-'+status+'.csv'
        for _,row in rows(output/filename,','):
            cells=[]
            for f in fields:
                v=row[f['name']]
                if v and f['type'] in ('integer','decimal'):v=Decimal(v)
                cells.append(v)
            hash_record(actual,row['__source_name'],int(row['__row']),cells,status);count+=1
        assert actual.hexdigest()==expected[status].hexdigest(), 'CSV cells/lineage mismatch: '+filename
        assert count==expected_counts[status], 'CSV population mismatch: '+filename
    return {'all_exported_csv_cells_and_source_references_match':True,'csv_counts':dict(expected_counts)}


def numeric_limits(paths, spec):
    counts=Counter();samples=defaultdict(list);records=Counter()
    for path in paths:
        for _,row in rows(path,spec['delimiter']):
            reasons=set()
            for header in spec['integers']+spec['decimals']:
                text=row[header].strip()
                if not text or text in spec['missing']:continue
                reason='unsupported_numeric_spelling' if not re.fullmatch(r'[+-]?[0-9]+(?:\.[0-9]+)?',text) else 'excess_decimal_precision' if len(text.partition('.')[2].rstrip('0'))>12 else None
                if reason:
                    key=header+':'+reason;counts[key]+=1;reasons.add(reason)
                    if len(samples[key])<3:samples[key].append(text)
            records.update(reasons)
    return {'affected_cells':dict(counts),'affected_record_reasons':dict(records),'examples':dict(samples),
        'note':'Natural source numbers, not injected defects; review means outside the declared numeric import contract, not a false measurement.'}


def run_checks(app,cache,work,evidence):
    # App imports resolve ONLY from the freshly downloaded, frozen release.
    sys.path.insert(0,str(app.resolve()))
    import duckdb
    import psutil
    from workbench.store import ProjectStore
    assert Path(sys.modules['workbench.store'].__file__).resolve().is_relative_to(app.resolve())
    os.environ['DATA_WORKBENCH_RUNTIME']=str(work/'runtime')
    frozen=read(cache/'frozen.json');assert sha(cache/'published-release.zip')==frozen['application_archive_sha256']
    with zipfile.ZipFile(cache/'published-release.zip') as archive:
        for member in archive.infolist():
            if not member.is_dir():
                assert (cache/'release'/member.filename).read_bytes()==archive.read(member), 'Extracted release changed: '+member.filename
    store=ProjectStore(work/'projects');results=[]
    try:
        for source_spec in frozen['sources']:
            name=source_spec['case'];paths=[cache/name/f['filename'] for f in source_spec['files']]
            assert all(sha(path)==entry['sha256'] for path,entry in zip(paths,source_spec['files']))
            project=store.create('Additional benchmark '+name);pid=project['id']
            sources=[store.add_source(pid,p.name,p) for p in paths]
            headers=sources[0]['headers'];assert all(s['headers']==headers for s in sources)
            sampled={f['name']:f['type'] for f in sources[0]['fields']}
            field_names={h:identifier(h) for h in headers};fields=[]
            for h in headers:
                kind='integer' if h in source_spec['integers'] else 'decimal' if h in source_spec['decimals'] else 'text'
                fields.append({'name':field_names[h],'type':kind,'required':h in source_spec['key'],
                    'trim':True,'missing':source_spec['missing'],'allowed':[],'mapping':{},'inferred':False})
            recipe={'version':2,'formats':{},'views':[],'tables':[{'name':name,'fields':fields,
                'key':[field_names[h] for h in source_spec['key']],'checks':[],'duplicate_policy':'keep_all',
                'sources':[{'id':s['id'],'mapping':{field_names[h]:h for h in headers},'ignored_columns':[]} for s in sources]}]}
            setup_changes=[{'field':f['name'],'sampled_type':sampled[f['name']],'configured_type':f['type']}
                for f in fields if sampled[f['name']]!=f['type']]
            # Freeze explicit rules before evaluating outcomes.
            store.set_recipe(pid,recipe);write(evidence/(name+'-policy.json'),{'case':name,'fields':fields,'key':source_spec['key'],'duplicate_policy':'keep_all','policy':source_spec['policy']})
            write(evidence/(name+'-numeric-limits.json'),numeric_limits(paths,source_spec))
            expected=hashlib.sha256();raw_expected=hashlib.sha256();counts=Counter();missing=Counter();natural=Counter();review_reasons=Counter();totals=defaultdict(Decimal);keys=set()
            for path in paths:
                for number,row in rows(path,source_spec['delimiter']):
                    prepared=[];bad=False;issues=[]
                    for header,field in zip(headers,fields):
                        typed,invalid,issue=value(row[header],field);prepared.append(typed);bad|=invalid
                        if typed is None:missing[field['name']]+=1
                        if issue:issues.append(issue)
                        if row[header]=='unknown':natural['unknown_category_cells']+=1
                        if row[header]=='NA':natural['NA_marker_cells']+=1
                        if field['type']!='text' and typed is not None and typed<0:natural['negative_numeric_cells']+=1
                    key=tuple(row[h].strip() for h in source_spec['key'])
                    if key:
                        assert key not in keys,'Unexpected repeated declared source key';keys.add(key)
                    status='review' if bad else 'accepted';counts[status]+=1;review_reasons.update(set(issues))
                    hash_record(expected,path.name,number,prepared,status)
                    hash_record(raw_expected,path.name,number,[row[h] for h in headers],'raw')
                    if not bad:
                        for f,v in zip(fields,prepared):
                            if f['type'] in ('decimal','integer') and v is not None:totals[f['name']]+=v
            stop=threading.Event();peak=[psutil.Process().memory_info().rss]
            def monitor():
                while not stop.wait(.05):peak[0]=max(peak[0],psutil.Process().memory_info().rss)
            thread=threading.Thread(target=monitor,daemon=True);thread.start();started=time.perf_counter()
            print('Processing '+name,flush=True)
            run=store.start_run(pid,synchronous=True);elapsed=time.perf_counter()-started;stop.set();thread.join()
            assert run['state']=='completed',run
            actual=hashlib.sha256();raw_actual=hashlib.sha256();input_count=0;accepted_precision_changes=0
            output=store.run_dir(pid,run['id'])/'outputs'
            with duckdb.connect(str(output/'data.duckdb'),read_only=True) as con:
                columns=','.join('"'+f['name']+'"' for f in fields)
                cursor=con.execute(f'SELECT __source_name,__row::BIGINT,{columns},__status,__raw FROM result_{name} ORDER BY __source_name,__row::BIGINT')
                while batch:=cursor.fetchmany(2000):
                    for result in batch:
                        filename,number=result[:2];prepared=result[2:-2];status=result[-2];raw=json.loads(result[-1]);input_count+=1
                        hash_record(actual,filename,number,prepared,status)
                        hash_record(raw_actual,filename,number,[raw[h] for h in headers],'raw')
                        if status=='accepted':
                            for h,f,v in zip(headers,fields,prepared):
                                if f['type']!='text' and v is not None and Decimal(raw[h].strip())!=Decimal(str(v)):accepted_precision_changes+=1
                assert expected.hexdigest()==actual.hexdigest(),'Prepared field/disposition mismatch in '+name
                assert raw_expected.hexdigest()==raw_actual.hexdigest(),'Raw value/lineage mismatch in '+name
                assert input_count==source_spec['input_rows'] and accepted_precision_changes==0
                for f,total in totals.items():
                    observed=con.execute(f'SELECT sum("{f}") FROM result_{name} WHERE __status=\'accepted\'').fetchone()[0]
                    assert observed==total,(name,f,total,observed)
            summary=run['manifest']['tables'][0]
            assert (summary['accepted'],summary['review'],summary['excluded'])==(counts['accepted'],counts['review'],0)
            assert not run['manifest']['file_issues']
            replay_start=time.perf_counter();replay=store.start_run(pid,synchronous=True);replay_seconds=time.perf_counter()-replay_start
            assert replay['id']==run['id']
            assert all(sha(output/f)==h for f,h in run['manifest']['output_hashes'].items())
            csv_check=verify_exports(output,name,fields,paths,source_spec)
            result={'case':name,'passed':True,'engine_build':run['manifest']['engine_build'],'tested_public_archive_sha256':frozen['application_archive_sha256'],
                'counts':dict(counts),'input_rows':input_count,'excluded':0,'missing_counts':dict(missing),'natural_features':dict(natural),
                'sampled_type_changes_before_analysis':setup_changes,
                'review_reason_counts':dict(review_reasons),'all_fields_dispositions_and_source_references_match':True,'all_raw_values_match':True,
                'accepted_numeric_value_changes':accepted_precision_changes,'independent_numeric_control_totals':{k:str(v) for k,v in totals.items()},
                'processing_seconds':round(elapsed,3),'peak_process_rss_mb':round(peak[0]/1024**2,1),'cached_replay_seconds':round(replay_seconds,3),
                'all_output_hashes_verified':True,**csv_check,'file_issues':[],'original_files':source_spec['files'],
                'limits':'Measurements include processing/export after upload setup. RSS is sampled process memory, including the driver and retained independent-control objects, not isolated application-only memory. Review numeric previews use the declared 12-place storage representation; original text remains unchanged in raw audit.'}
            write(evidence/(name+'.json'),result);results.append(result)
            print(json.dumps({'case':name,'counts':dict(counts),'seconds':round(elapsed,3),'passed':True}),flush=True)
    finally:store.worker.shutdown()
    write(evidence/'summary.json',{'passed':True,'cases':len(results),'total_records':sum(r['input_rows'] for r in results),
        'app_version':'2.0.0','app_archive_sha256':frozen['application_archive_sha256'],'results':results})


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--cache',type=Path,required=True);parser.add_argument('--workdir',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True);parser.add_argument('--acquire-only',action='store_true');parser.add_argument('--run-only',action='store_true')
    args=parser.parse_args();cache=args.cache.resolve();work=args.workdir.resolve();work.mkdir(parents=True,exist_ok=True)
    app=cache/'release/data-workbench'
    if not args.run_only:app=acquire(cache,args.evidence.resolve())
    if not args.acquire_only:run_checks(app,cache,work,args.evidence.resolve())

if __name__=='__main__':main()
