"""Fetch the planned first 50,000 2019 keys using an equivalent bounded query."""
import argparse
import concurrent.futures
import csv
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE='https://data.cityofnewyork.us/resource/76ig-c548'
WHERE="created_date >= '2019-01-01T00:00:00.000' AND created_date < '2020-01-01T00:00:00.000'"
FIELDS='unique_key,created_date,closed_date,agency,agency_name,complaint_type,descriptor,incident_zip,city,status,borough,resolution_description,resolution_action_updated_date,latitude,longitude,location_type'


def query(params):
    with urllib.request.urlopen(BASE+'.json?'+urllib.parse.urlencode(params),timeout=90) as response:return json.load(response)


def main():
    p=argparse.ArgumentParser();p.add_argument('--cache',type=Path,required=True);args=p.parse_args()
    target=args.cache/'nyc';target.mkdir(parents=True,exist_ok=True)
    bounds_path=target/'bounds.json'
    if bounds_path.exists():
        bounds=json.loads(bounds_path.read_text());minimum=bounds['minimum'];upper=bounds['upper'];count=bounds['count']
    else:
        minimum=query({'$select':'min(unique_key) as first_key,max(unique_key) as last_key,count(*) as total','$where':WHERE})[0]
        lower=minimum['first_key'];span=100000
        while True:
            upper=str(int(lower)+span)
            bounded=WHERE+f" AND unique_key <= '{upper}'"
            count=int(query({'$select':'count(*) as total','$where':bounded})[0]['total'])
            if count>=50000:break
            span*=2
        bounds_path.write_text(json.dumps({'minimum':minimum,'upper':upper,'count':count},indent=2))
    lower=minimum['first_key'];bounded=WHERE+f" AND unique_key <= '{upper}'"
    print(f'Bounded key range contains {count} requests',flush=True)
    params={'$select':FIELDS,'$where':bounded,'$order':'unique_key','$limit':'50000'}
    url=BASE+'.json?'+urllib.parse.urlencode(params)
    started=time.perf_counter()
    # Small disjoint key intervals avoid the service's expensive broad sort.
    # Read every key in the proven bounded interval, then apply the same filter/sort.
    parts=target/'acquisition-parts';parts.mkdir(exist_ok=True)
    def fetch_interval(start):
        stop=min(start+4999,int(upper))
        key_where=f"unique_key >= '{start}' AND unique_key <= '{stop}'"
        part=parts/f'{start}-{stop}.json'
        if part.exists():result=json.loads(part.read_text(encoding='utf-8'))
        else:
            result=query({'$select':FIELDS,'$where':key_where,'$limit':'5000'})
            assert len(result)<=5000
            part.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
        assert all(start<=int(r['unique_key'])<=stop for r in result)
        print(f'Acquired key interval {start}-{stop}: {len(result)} requests',flush=True)
        return result
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        all_rows=[r for group in pool.map(fetch_interval,range(int(lower),int(upper)+1,5000)) for r in group]
    assert len({r['unique_key'] for r in all_rows})==len(all_rows),'Repeated primary keys in acquisition'
    eligible=[r for r in all_rows if '2019-01-01T00:00:00.000'<=r['created_date']<'2020-01-01T00:00:00.000']
    assert len(eligible)==count,'Interval acquisition is not complete against independent server count'
    rows=sorted(eligible,key=lambda r:int(r['unique_key']))[:50000]
    assert len(rows)==50000
    keys=[r['unique_key'] for r in rows]
    assert keys==sorted(keys) and keys[0]==lower and len(set(keys))==50000
    (target/'source.json').write_text(json.dumps(rows,ensure_ascii=False),encoding='utf-8')
    with (target/'source.csv').open('w',encoding='utf-8',newline='') as stream:
        writer=csv.DictWriter(stream,FIELDS.split(','));writer.writeheader();writer.writerows(rows)
    (target/'selection.json').write_text(json.dumps({'query':params,'source_url':url,'full_2019_rows':int(minimum['total']),
        'range_count':count,'first_key':keys[0],'last_key':keys[-1],
        'transport':'Disjoint 5000-key intervals, each limited to 5000 unique integer keys; complete range count checked against independent server count, then filtered and sorted locally.',
        'equivalence':'The added upper bound contains at least 50000 rows and the global minimum key. Therefore ORDER BY unique_key LIMIT 50000 is unchanged.',
        'json_to_csv':'Lossless tabular serialization of selected scalar fields; absent optional JSON properties become blank CSV values.',
        'download_seconds':round(time.perf_counter()-started,3)},indent=2),encoding='utf-8')
    print('NYC first 50000 keys saved',flush=True)


if __name__=='__main__':main()
