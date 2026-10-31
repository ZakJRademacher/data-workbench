"""Independent source controls and project comparison. No raw public rows are published."""
from __future__ import annotations
import argparse
import csv
import hashlib
import importlib.util
import itertools
import json
import os
import re
import sys
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, date
from decimal import Decimal
from pathlib import Path

import duckdb
import psutil
from openpyxl import load_workbook

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from workbench.common import file_hash, identifier, write_json
from workbench.store import ProjectStore
from sales_compatibility import build as compatibility, FIELDS as SALES_NAMES


def source_rows(path, encoding='utf-8-sig', delimiter=','):
    """Independent reader: deliberately does not import workbench.ingest."""
    if path.suffix.lower()=='.xlsx':
        book=load_workbook(path,read_only=True,data_only=True,keep_links=False)
        try:
            stream=book.active.iter_rows(values_only=True)
            headers=[str(v) for v in next(stream)]
            for number,row in enumerate(stream,2):
                if not any(v is not None for v in row):continue
                values=[v.isoformat() if isinstance(v,(datetime,date)) else '' if v is None else str(v) for v in row]
                yield number,dict(zip(headers,values))
        finally:book.close()
    else:
        with path.open(encoding=encoding,newline='') as stream:
            reader=csv.DictReader(stream,delimiter=delimiter)
            for number,row in enumerate(reader,2):yield number,row


def canonical_value(value,field):
    if value is None:return None
    if field['type'] in ('decimal','integer'):
        value=Decimal(str(value))
        return '0' if not value else format(value.normalize(),'f')
    if isinstance(value,date):return value.isoformat()
    return str(value)


def expected_value(raw,field):
    value=raw.strip() if field.get('trim',True) else raw
    if value=='' or value in field.get('missing',[]):
        return None,bool(field.get('required'))
    if field['type']=='text':return value,False
    if field['type']=='date':
        try:
            dt=datetime.strptime(value,field['date_format'])
            if field.get('date_exact',True) and dt.strftime(field['date_format'])!=value:raise ValueError('padding')
            return dt.date().isoformat(),False
        except ValueError:return None,True
    number=value.replace(field.get('thousands_separator','\0'),'') if field.get('thousands_separator') else value
    if field.get('decimal_separator','.')!= '.':number=number.replace(field['decimal_separator'],'.')
    if not re.fullmatch(r'[+-]?[0-9]+(?:\.[0-9]+)?',number):return None,True
    fraction=number.partition('.')[2]
    if field.get('precision_mode','nonzero')=='nonzero':fraction=fraction.rstrip('0')
    invalid=len(fraction)>field.get('decimal_places',12)
    parsed=Decimal(number)
    if abs(parsed)>=Decimal('1e26'):invalid=True
    if field['type']=='integer' and (parsed!=parsed.to_integral_value() or not -(2**63)<=parsed<2**63):invalid=True
    return canonical_value(parsed,field),invalid


def hash_row(h,number,values,status):
    h.update((json.dumps([number,values,status],ensure_ascii=False,separators=(',',':'))+'\n').encode())


def profile(store,pid,paths,table_names,numeric=None,dates=None):
    numeric=numeric or {};dates=dates or {}
    sources=[];tables=[]
    for path,name in zip(paths,table_names):
        source=store.add_source(pid,path.name,path);sources.append(source)
        fields=[];mapping={}
        for header in source['headers']:
            n=identifier(header)
            f={'name':n,'type':numeric.get(header,'text'),'required':False,'trim':True,'missing':[],'allowed':[],'mapping':{},'inferred':False}
            if header in dates:f.update(type='date',date_format=dates[header],date_exact=False)
            fields.append(f);mapping[n]=header
        assignment={'id':source['id'],'mapping':mapping,'ignored_columns':[]}
        existing=next((t for t in tables if t['name']==name),None)
        if existing:existing['sources'].append(assignment)
        else:tables.append({'name':name,'fields':fields,'key':[],'duplicate_policy':'keep_all','checks':[], 'sources':[assignment]})
    return {'version':2,'formats':{},'tables':tables,'views':[]},sources


def measure(store,pid):
    stop=threading.Event();memory=[psutil.Process().memory_info().rss]
    def monitor():
        while not stop.wait(.05):memory[0]=max(memory[0],psutil.Process().memory_info().rss)
    thread=threading.Thread(target=monitor,daemon=True);thread.start();began=time.perf_counter()
    run=store.start_run(pid,synchronous=True);elapsed=time.perf_counter()-began;stop.set();thread.join()
    if run['state']!='completed':raise AssertionError(run)
    return run,{'processing_seconds':round(elapsed,3),'peak_process_rss_mb':round(memory[0]/1024**2,1)}


def check_values(store,pid,run,recipe,sources,paths):
    results=[]
    with duckdb.connect(str(store.run_dir(pid,run['id'])/'outputs/data.duckdb'),read_only=True) as con:
        for source,path in zip(sources,paths):
            table=next(t for t in recipe['tables'] if any(a['id']==source['id'] for a in t['sources']))
            assignment=next(a for a in table['sources'] if a['id']==source['id'])
            fields=table['fields'];mapping=assignment['mapping']
            expected=hashlib.sha256();actual=hashlib.sha256();raw_hash=hashlib.sha256();actual_raw_hash=hashlib.sha256()
            counts=Counter();missing=Counter();totals=defaultdict(Decimal)
            for number,row in source_rows(path,source['settings']['encoding'],source['settings']['delimiter']):
                values=[];bad=False
                for f in fields:
                    v,invalid=expected_value(row[mapping[f['name']]],f);values.append(v);bad|=invalid
                    if v is None:missing[f['name']]+=1
                status='review' if bad else 'accepted';counts[status]+=1
                hash_row(expected,number,values,status)
                hash_row(raw_hash,number,[row[mapping[f['name']]] for f in fields],'raw')
                if not bad:
                    for f,v in zip(fields,values):
                        if f['type'] in ('decimal','integer') and v is not None:totals[f['name']]+=Decimal(v)
            columns=','.join('"'+f['name']+'"' for f in fields)
            cursor=con.execute(f'SELECT __row::BIGINT,{columns},__status,__raw FROM "result_{table["name"]}" WHERE __source_id=? ORDER BY __row::BIGINT',[source['id']])
            while batch:=cursor.fetchmany(2000):
                for r in batch:
                    hash_row(actual,r[0],[canonical_value(v,f) if v is not None else None for v,f in zip(r[1:-2],fields)],r[-2])
                    raw=json.loads(r[-1]);hash_row(actual_raw_hash,r[0],[raw[mapping[f['name']]] for f in fields],'raw')
            summary=next(t for t in run['manifest']['tables'] if t['name']==table['name'])
            assert expected.hexdigest()==actual.hexdigest(),f'Value/disposition mismatch in {table["name"]}'
            assert raw_hash.hexdigest()==actual_raw_hash.hexdigest(),f'Raw value preservation mismatch in {table["name"]}'
            actual_counts=dict(con.execute(f'SELECT __status,count(*) FROM "result_{table["name"]}" WHERE __source_id=? GROUP BY __status',[source['id']]).fetchall())
            assert actual_counts.get('accepted',0)==counts['accepted'] and actual_counts.get('review',0)==counts['review'] and actual_counts.get('excluded',0)==0
            for field,total in totals.items():
                observed=con.execute(f'SELECT sum("{field}") FROM "result_{table["name"]}" WHERE __source_id=? AND __status=\'accepted\'',[source['id']]).fetchone()[0]
                assert observed==total
            results.append({'table':table['name'],'source':path.name,'input_rows':sum(counts.values()),'accepted':counts['accepted'],'review':counts['review'],
                'missing_counts':dict(missing),'independent_totals':{k:str(v) for k,v in totals.items()},'normalized_rows_sha256':actual.hexdigest(),
                'raw_rows_sha256':actual_raw_hash.hexdigest(),'every_field_and_disposition_matches':True})
    return results


def partition_large(path,work):
    if path.stat().st_size<=50*1024*1024:return [path],None
    folder=work/'partitions'/path.parent.name;folder.mkdir(parents=True,exist_ok=True)
    paths=[];records=[]
    with path.open(encoding='utf-8-sig',newline='') as source:
        reader=csv.reader(source);headers=next(reader);batch=[];start=2
        def emit(rows,start):
            target=folder/f'source-part-{len(paths)+1:02}.csv'
            with target.open('w',encoding='utf-8',newline='') as f:
                writer=csv.writer(f);writer.writerow(headers);writer.writerows(rows)
            if target.stat().st_size>50*1024*1024:raise RuntimeError('Partition still exceeds 50 MB')
            paths.append(target);records.append({'file':target.name,'sha256':file_hash(target),'source_first_record':start,'rows':len(rows)})
        for row in reader:
            batch.append(row)
            if len(batch)==5000:emit(batch,start);start+=len(batch);batch=[]
        if batch:emit(batch,start)
    def records_from(p):
        with p.open(encoding='utf-8-sig',newline='') as stream:
            reader=csv.reader(stream);next(reader);yield from reader
    sentinel=object()
    for original,partitioned in itertools.zip_longest(records_from(path),itertools.chain.from_iterable(records_from(p) for p in paths),fillvalue=sentinel):
        assert original==partitioned,'Partitioning changed or dropped an original record'
    return paths,{'reason':'Source exceeds the unchanged 50 MB single-file limit. Lossless CSV partition only; no value cleaning.','original_sha256':file_hash(path),'original_bytes':path.stat().st_size,'partitions':records,'every_original_field_in_order_verified':True}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--cache',type=Path,required=True);parser.add_argument('--workdir',type=Path,required=True)
    parser.add_argument('--old-project',type=Path,required=True);parser.add_argument('--case',default='all')
    args=parser.parse_args();cache=args.cache.resolve();scratch=args.workdir.resolve();scratch.mkdir(parents=True,exist_ok=True)
    os.environ['DATA_WORKBENCH_RUNTIME']=str(scratch/'runtime');store=ProjectStore(scratch/'projects')
    evidence=ROOT/'evidence/external';evidence.mkdir(parents=True,exist_ok=True)
    frozen=json.loads((cache/'source-manifest.json').read_text(encoding='utf-8'))
    frozen_hashes={f['sha256'] for s in frozen['sources'] for f in s['files']}
    specs=[('uci',[cache/'uci/Online Retail.xlsx'],['retail'],{'Quantity':'integer','UnitPrice':'decimal'},{}),
           ('chicago',[cache/'chicago/source.csv'],['inspections'],{},{}),
           ('nyc',[cache/'nyc/source.csv'],['requests'],{},{}),
           ('superstore',[cache/'superstore/Sample - Superstore.csv'],['superstore'],{'Sales':'decimal','Profit':'decimal','Quantity':'integer','Discount':'decimal'},{'Order Date':'%m/%d/%Y','Ship Date':'%m/%d/%Y'}),
           ('olist',sorted((cache/'olist').glob('*.csv')),['customers','items','payments','orders','products','translations'],{'price':'decimal','freight_value':'decimal','payment_value':'decimal'}, {})]
    for name,paths,names,numeric,dates in specs:
        if args.case not in ('all',name):continue
        input_files=[{'filename':p.name,'sha256':file_hash(p),'bytes':p.stat().st_size} for p in paths]
        assert all(f['sha256'] in frozen_hashes for f in input_files),'Input differs from frozen source manifest'
        partition=None
        if len(paths)==1:
            paths,partition=partition_large(paths[0],scratch)
            names=[names[0]]*len(paths)
        if name=='olist':
            prior=json.loads((cache/'olist/prior-source-manifest.json').read_text())
            for path in paths:assert file_hash(path)==prior['source_hashes'][path.name],f'Prior Olist provenance mismatch: {path.name}'
        pid=store.create('External benchmark '+name)['id'];recipe,sources=profile(store,pid,paths,names,numeric,dates)
        if name=='olist':
            recipe['views']=[{'name':'orders_combined','base':'orders','joins':[
                {'table':'customers','left_on':['customer_id'],'right_on':['customer_id'],'how':'left','approved':True,'aggregations':[]},
                {'table':'items','left_on':['order_id'],'right_on':['order_id'],'how':'left','approved':True,'aggregations':[{'op':'sum','field':'price','name':'merchandise'},{'op':'sum','field':'freight_value','name':'freight'}]},
                {'table':'payments','left_on':['order_id'],'right_on':['order_id'],'how':'left','approved':True,'aggregations':[{'op':'sum','field':'payment_value','name':'paid'}]}]}]
        store.set_recipe(pid,recipe);print('Processing '+name,flush=True);run,timing=measure(store,pid)
        checks=check_values(store,pid,run,recipe,sources,paths)
        result={'case':name,'passed':True,'processing':timing,'tables':checks,'views':run['manifest']['views'],
                'input_files':input_files,
                'source_fingerprint_verified_against_frozen_manifest':True,
                'file_issues':run['manifest']['file_issues'],'old_project_fit':'Unsupported native schema or file format; not scored as a cleaning error',
                'configuration':{'typed_fields':sum(f['type']!='text' for t in recipe['tables'] for f in t['fields']),
                                 'duplicate_policy':'keep_all; repeated source lines are preserved','timestamp_policy':'preserve complete source timestamps as text'},
                'engine_build':run['manifest']['engine_build'],'input_partitioning':partition}
        if name=='olist':
            # Independent dictionaries avoid using either project SQL to define expected joined totals.
            sums=defaultdict(lambda:[Decimal(0),Decimal(0)]);pays=defaultdict(Decimal);customers=set()
            for _,r in source_rows(cache/'olist/olist_order_items_dataset.csv'):
                sums[r['order_id']][0]+=Decimal(r['price']);sums[r['order_id']][1]+=Decimal(r['freight_value'])
            for _,r in source_rows(cache/'olist/olist_order_payments_dataset.csv'):pays[r['order_id']]+=Decimal(r['payment_value'])
            for _,r in source_rows(cache/'olist/olist_customers_dataset.csv'):customers.add(r['customer_id'])
            h=hashlib.sha256();order_count=0
            for number,r in source_rows(cache/'olist/olist_orders_dataset.csv'):
                order_count+=1;item=sums.get(r['order_id']);payment=pays.get(r['order_id'])
                hash_row(h,number,[r['order_id'],None if item is None else str(item[0].normalize()),None if item is None else str(item[1].normalize()),None if payment is None else str(payment.normalize()),r['customer_id'] in customers],'joined')
            actual=hashlib.sha256()
            with duckdb.connect(str(store.run_dir(pid,run['id'])/'outputs/data.duckdb'),read_only=True) as con:
                for number,oid,merch,freight,paid,cid in con.execute('SELECT __row::BIGINT,order_id,items__merchandise,items__freight,payments__paid,customers__customer_id FROM orders_combined ORDER BY __row::BIGINT').fetchall():
                    hash_row(actual,number,[oid,None if merch is None else str(merch.normalize()),None if freight is None else str(freight.normalize()),None if paid is None else str(paid.normalize()),cid is not None],'joined')
            assert h.hexdigest()==actual.hexdigest(),'Olist joined values mismatch'
            result['independent_join']={'order_rows':order_count,'every_order_and_aggregated_value_matches':True,'sha256':actual.hexdigest()}
        replay_started=time.perf_counter();replay=store.start_run(pid,synchronous=True)
        assert replay['id']==run['id'];result['verified_replay_seconds']=round(time.perf_counter()-replay_started,3)
        write_json(evidence/(name+'.json'),result)
        print(json.dumps({'case':name,'passed':True,'rows':run['manifest']['input_rows'],**timing}),flush=True)
    store.worker.shutdown()


if __name__=='__main__':main()
