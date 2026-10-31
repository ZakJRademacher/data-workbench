import copy
import csv
import json
from datetime import datetime

import pytest
from openpyxl import Workbook

from conftest import add_csv, completed
from workbench.ingest import infer_field, text
from workbench.recipe import migrate, validate
from workbench.common import WorkbenchError


def test_identifiers_and_timestamps_remain_intact(store,tmp_path):
    path=tmp_path/'events.xlsx';book=Workbook();ws=book.active
    ws.append(['CustomerID','Event timestamp','Other code']);ws.append(['001',datetime(2026,1,5,14,35,42),'0009']);book.save(path)
    pid=store.create('Preserve native Excel values')['id'];store.add_source(pid,path.name,path);recipe=store.set_recipe(pid)
    assert recipe['tables'][0]['fields'][0]['type']=='text'
    result=completed(store,pid);data=store.preview(pid,result['id'],'events')
    row=dict(zip(data['columns'],data['rows'][0]));raw=json.loads(row['__raw'])
    assert row['customerid']=='001' and row['event_timestamp']=='2026-01-05T14:35:42'
    assert raw['Event timestamp']=='2026-01-05T14:35:42'
    assert infer_field('Counter',['001','002'])[0]['type']=='text'


def test_source_formats_checks_case_and_decimal_contract(store,tmp_path):
    pid=store.create('Mixed source conventions')['id']
    a=add_csv(store,pid,tmp_path,'a.csv','id,ordered,shipped,category,amount\n001,2026-01-05,2026-01-06,furniture,"1,234.5678"\n002,2026-01-05,2026-01-04,Furniture,12.34\n003,2026-01-05,2026-01-06,Furniture,12.34000\n')
    b=add_csv(store,pid,tmp_path,'b.csv','id,ordered,shipped,category,amount\n004,01/05/2026,01/06/2026,FURNITURE,2.5000\n')
    recipe=store.set_recipe(pid);table=recipe['tables'][0]
    for f in table['fields']:
        f['inferred']=False
        if f['name'] in ('ordered','shipped'):f.update(type='date',date_format='%Y-%m-%d')
        if f['name']=='category':f.update(allowed=['Furniture'],case_insensitive=True)
        if f['name']=='amount':f.update(type='decimal',decimal_places=4,precision_mode='strict',thousands_separator=',')
    recipe['formats']={'US':{'fields':{n:{'date_format':'%m/%d/%Y'} for n in ('ordered','shipped')}}}
    table['sources'][1]['format']='US';table['checks']=[{'left':'shipped','op':'>=','right':'ordered'}]
    store.set_recipe(pid,recipe);result=completed(store,pid)
    m=result['manifest']['tables'][0]
    assert (m['accepted'],m['review'])==(2,2)
    assert m['totals']['amount']['sum']=='1237.067800000000'
    data=store.preview(pid,result['id'],table['name'])
    assert all(row[data['columns'].index('category')]=='Furniture' for row in data['rows'])


def test_v1_migration_preserves_defaults_and_snapshot(store,tmp_path):
    pid=store.create('Legacy recipe')['id'];add_csv(store,pid,tmp_path,'legacy.csv','id,amount\n001,12.3400000000000\n')
    recipe=store.set_recipe(pid);recipe['version']=1;recipe.pop('formats');recipe['tables'][0].pop('checks')
    original=copy.deepcopy(recipe);migrated=migrate(recipe)
    assert original==recipe and migrated['version']==2 and migrated['formats']=={}
    store.set_recipe(pid,recipe);result=completed(store,pid)
    assert result['manifest']['tables'][0]['accepted']==1
    assert result['manifest']['tables'][0]['totals']['amount']['sum']=='12.340000000000'
    with pytest.raises(WorkbenchError):migrate({'version':3})


def test_named_format_validation_and_fractional_integer(store,tmp_path):
    pid=store.create('Format validation')['id'];add_csv(store,pid,tmp_path,'rows.csv','id,amount\n001,2\n')
    recipe=store.set_recipe(pid)
    recipe['formats']={'bad':{'fields':{'amount':{'sql':'arbitrary'}}}}
    with pytest.raises(WorkbenchError,match='parsing only'):store.set_recipe(pid,recipe)


def test_inferred_numeric_value_with_later_leading_zero_goes_to_review(store,tmp_path):
    pid=store.create('Full-column validation')['id']
    content='counter,value\n'+''.join(f'{i},plain\n' for i in range(1,220))+'0009,plain\n'
    add_csv(store,pid,tmp_path,'rows.csv',content);recipe=store.set_recipe(pid)
    recipe['tables'][0]['duplicate_policy']='keep_all';store.set_recipe(pid,recipe)
    result=completed(store,pid)
    assert result['manifest']['tables'][0]['review']==1


def test_whitespace_normalization_including_embedded_multiline_boundaries(store,tmp_path):
    pid=store.create('Whitespace')['id']
    add_csv(store,pid,tmp_path,'whitespace.csv','id,value\n001,"\n,  \n(41.9, -87.6)\t"\n002,"\u00a0content\u00a0"\n')
    store.set_recipe(pid);result=completed(store,pid);data=store.preview(pid,result['id'],'whitespace')
    index=data['columns'].index('value')
    assert [r[index] for r in data['rows']]==[',  \n(41.9, -87.6)','content']
