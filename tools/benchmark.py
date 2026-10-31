"""Reproducible one-million-input-row check. Bulk inputs stay outside the library."""
import argparse
import csv
import json
import os
import platform
import sys
import threading
import time
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workbench.common import VERSION, write_json
from workbench.store import ProjectStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workdir', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    root = args.workdir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ['DATA_WORKBENCH_RUNTIME'] = str(root / 'runtime')
    store = ProjectStore(root / 'projects')
    pid = store.create('One million row acceptance check')['id']
    specs = [('customers',300_000,['customer_id','name','region']),
             ('orders',350_000,['order_id','customer_id','amount','order_date']),
             ('payments',350_000,['payment_id','order_id','paid_amount'])]
    source_bytes = 0
    for name,count,headers in specs:
        path = root / (name+'.csv')
        with path.open('w',encoding='utf-8',newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(headers)
            for i in range(count):
                if name=='customers': row=[f'{i:07}',f'Customer {i}', ['West','East','North','South'][i%4]]
                elif name=='orders': row=[f'O{i:07}',f'{i%300000:07}','12.3456','2026-01-15']
                else: row=[f'P{i:07}',f'O{i:07}','12.3456']
                writer.writerow(row)
        source_bytes += path.stat().st_size
        store.add_source(pid,path.name,path)
    recipe = store.set_recipe(pid)
    for t in recipe['tables']:
        t['key']=[{'customers':'customer_id','orders':'order_id','payments':'payment_id'}[t['name']]]
    recipe['views']=[{'name':'orders_combined','base':'orders','joins':[
        {'table':'customers','left_on':['customer_id'],'right_on':['customer_id'],'how':'left','approved':True,'aggregations':[]},
        {'table':'payments','left_on':['order_id'],'right_on':['order_id'],'how':'left','approved':True,'aggregations':[]}]}]
    store.set_recipe(pid,recipe)
    process=psutil.Process()
    peak=[process.memory_info().rss]
    stop=threading.Event()
    def measure():
        while not stop.wait(.05):
            peak[0]=max(peak[0],process.memory_info().rss)
    monitor=threading.Thread(target=measure,daemon=True)
    monitor.start()
    began=time.perf_counter()
    run=store.start_run(pid,synchronous=True)
    elapsed=time.perf_counter()-began
    stop.set();monitor.join()
    manifest=run.get('manifest',{})
    success=(run['state']=='completed' and manifest.get('status')=='ready' and manifest.get('input_rows')==1_000_000
             and manifest.get('review_rows')==0 and manifest.get('views',[{}])[0].get('after')==350_000)
    totals={t['name']:t['totals'] for t in manifest.get('tables',[])}
    success=success and totals.get('orders',{}).get('amount',{}).get('sum')=='4320960.000000000000'
    evidence={'engine_version':VERSION,'passed':success,'input_rows':1_000_000,'input_bytes':source_bytes,
              'elapsed_seconds':round(elapsed,3),'peak_process_rss_mb':round(peak[0]/1024**2,1),
              'platform':platform.platform(),'python':platform.python_version(),'processor':platform.processor(),
              'ram_gb':round(psutil.virtual_memory().total/1024**3,1),'totals':totals,
              'state':run['state'],'message':run['message'],'tables':manifest.get('tables'),
              'views':manifest.get('views'),'output_bytes':sum(p.stat().st_size for p in (store.run_dir(pid,run['id'])/'outputs').glob('*') if p.is_file())}
    write_json(args.evidence,evidence)
    print(json.dumps(evidence,indent=2))
    store.worker.shutdown()
    if not success: raise SystemExit(1)


if __name__=='__main__': main()
