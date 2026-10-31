"""Compare the documented sales contract with the original independent reference."""
import argparse
import csv
import importlib.util
import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

import duckdb

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from workbench.common import write_json
from workbench.store import ProjectStore
from sales_compatibility import build,FIELDS


def main():
    p=argparse.ArgumentParser();p.add_argument('--old-project',type=Path,required=True);p.add_argument('--input',type=Path,required=True)
    p.add_argument('--workdir',type=Path,required=True);p.add_argument('--name',required=True);p.add_argument('--native',type=Path)
    p.add_argument('--labels',type=Path,help='Independent seeded ground-truth labels, separate from the old reference.')
    args=p.parse_args();old=args.old_project.resolve();work=args.workdir.resolve();work.mkdir(parents=True,exist_ok=True)
    spec=importlib.util.spec_from_file_location('original_sales_reference',old/'src/consolidate.py');reference=importlib.util.module_from_spec(spec);spec.loader.exec_module(reference)
    before=time.perf_counter();expected=reference.consolidate(args.input);old_seconds=time.perf_counter()-before
    os.environ['DATA_WORKBENCH_RUNTIME']=str(work/'runtime');store=ProjectStore(work/'projects');pid=store.create('Compatibility '+args.name)['id']
    for path in sorted(args.input.glob('*.csv')):store.add_source(pid,path.name,path)
    recipe=build(store.get(pid)['sources'],old/'config');store.set_recipe(pid,recipe)
    before=time.perf_counter();run=store.start_run(pid,synchronous=True);new_seconds=time.perf_counter()-before
    assert run['state']=='completed',run
    expected_rows={(r['SourceFile'],r['SourceRow']):r for r in expected['rows'] if r['Status']!='File issue'}
    matched=0
    observed={}
    with duckdb.connect(str(store.run_dir(pid,run['id'])/'outputs/data.duckdb'),read_only=True) as con:
        cursor=con.execute('SELECT * FROM result_sales_exports ORDER BY __source_name,__row::BIGINT')
        cols=[c[0] for c in cursor.description]
        for values in cursor.fetchall():
            row=dict(zip(cols,values));ref=expected_rows[(row['__source_name'],int(row['__row']))]
            observed[(row['__source_name'],int(row['__row']))]=row['__status']
            assert row['__status']=={'Clean':'accepted','Needs review':'review','Repeat skipped':'excluded'}[ref['Status']],(row,ref)
            if ref['Status'] in ('Clean','Repeat skipped'):
                for original,field in FIELDS.items():
                    if original=='Sales':assert row[field]==Decimal(ref[original])
                    else:assert str(row[field])==ref[original],(original,row[field],ref[original])
                matched+=1
    actual=run['manifest']['tables'][0]
    assert actual['input']==expected['metrics']['input_rows']
    assert (actual['accepted'],actual['review'],actual['excluded'])==tuple(expected['metrics'][k] for k in ('clean_rows','review_rows','repeated_rows'))
    assert Decimal(actual['totals']['sales']['sum'])==Decimal(expected['metrics']['clean_sales'])
    result={'case':args.name,'passed':True,'old_reference':expected['metrics'],'new_workbench':actual,
        'all_record_dispositions_match':True,'accepted_and_skipped_business_fields_match':matched,'old_python_reference_seconds':round(old_seconds,3),
        'workbench_processing_seconds':round(new_seconds,3),'reference_timing_note':'Python reference timing is not native Excel timing.',
        'review_representation_note':'Workbench represents missing typed values as NULL; raw input bytes and original values remain available.',
        'engine_build':run['manifest']['engine_build']}
    if args.labels:
        labels=json.loads(args.labels.read_text(encoding='utf-8'))
        expected_labels={(r['file'],r['row']):r['status'] for r in labels['labels']}
        assert observed==expected_labels,'Injected-defect labels differ from actual row dispositions'
        result['injected_ground_truth']={'seed':labels['seed'],'all_labels_match':True,
            'known_invalid_accepted':0,'known_valid_rejected':0,'labelled_records':len(expected_labels),
            'expected_counts':labels['expected_counts']}
    if args.native:
        native=json.loads(args.native.read_text(encoding='utf-8-sig'));tables=native['tables']
        clean=tables['CleanMaster'];reviews=tables['NeedsReview']
        assert len(clean)==expected['metrics']['clean_rows']
        assert len(reviews)==expected['metrics']['review_rows']+expected['metrics']['repeated_rows']
        native_map={(r['SourceFile'],int(r['SourceRow'])):r for r in clean}
        for key,r in expected_rows.items():
            if r['Status']!='Clean':continue
            n=native_map[key]
            for field in reference.FIELDS:
                assert Decimal(n[field])==Decimal(r[field]) if field=='Sales' else str(n[field])==r[field],(key,field,n[field],r[field])
        assert sum((Decimal(r['Sales']) for r in clean),Decimal(0))==Decimal(expected['metrics']['clean_sales'])
        result['native_excel']={'all_clean_values_match':True,'clean_rows':len(clean),'review_and_skipped_rows':len(reviews),
                               'refresh_seconds':native['elapsed_seconds'],'version':native['excel_version']}
        if 'peak_excel_process_rss_mb' in native:
            result['native_excel'].update(peak_excel_process_rss_mb=native['peak_excel_process_rss_mb'],memory_scope=native['memory_scope'])
    write_json(ROOT/'evidence'/('compatibility-'+args.name+'.json'),result)
    print(json.dumps(result,indent=2));store.worker.shutdown()


if __name__=='__main__':main()
