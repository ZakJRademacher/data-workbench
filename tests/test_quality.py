import json

import pytest
from openpyxl import Workbook, load_workbook

from workbench.common import WorkbenchError
from conftest import add_csv, completed


def test_quality_rules_profiles_and_zero_vs_missing(store, tmp_path):
    pid=store.create('Quality rules')['id']
    add_csv(store,pid,tmp_path,'checks.csv','id,region,amount\n001,W,0\n002,W,\n003,X,5\n004,W,-1\n005,W,NA\n006,W,20\n')
    recipe=store.set_recipe(pid)
    for f in recipe['tables'][0]['fields']:
        if f['name']=='amount': f.update(type='decimal',min='0',max='10',missing=['NA'])
        if f['name']=='region': f.update(allowed=['West'],mapping={'W':'West'})
    store.set_recipe(pid,recipe)
    result=completed(store,pid)
    summary=result['manifest']['tables'][0]
    assert (summary['accepted'],summary['review'])==(3,3)
    assert summary['totals']['amount']=={'sum':'0E-12','populated':1}
    profile=next(f for f in result['manifest']['profiles'] if f['field']=='amount')
    assert profile['missing']==2
    recipe['tables'][0]['fields'][2].update(min='10',max='0')
    with pytest.raises(WorkbenchError,match='Minimum'):
        store.set_recipe(pid,recipe)


def test_null_sentinel_text_is_preserved(store,tmp_path):
    pid=store.create('Literal values')['id']
    add_csv(store,pid,tmp_path,'literal.csv','id,value\n001,__WORKBENCH_NEVER_NULL__\n002,NULL\n003,NA\n')
    store.set_recipe(pid)
    result=completed(store,pid)
    rows=store.preview(pid,result['id'],'literal')['rows']
    columns=store.preview(pid,result['id'],'literal')['columns']
    values=[r[columns.index('value')] for r in rows]
    assert values==['__WORKBENCH_NEVER_NULL__','NULL','NA']


def test_excel_displayed_zero_padding_and_provenance(store,tmp_path):
    path=tmp_path/'padded.xlsx'
    book=Workbook();ws=book.active
    ws.append(['customer_id','amount']);ws.append([12,5])
    ws['A2'].number_format='00000'
    book.save(path)
    pid=store.create('Padded identifiers')['id']
    store.add_source(pid,path.name,path)
    store.set_recipe(pid)
    result=completed(store,pid)
    data=store.preview(pid,result['id'],'padded')
    assert data['rows'][0][data['columns'].index('customer_id')]=='00012'
    path=store.run_dir(pid,result['id'])/'outputs'/'Review.xlsx'
    output=load_workbook(path,read_only=True)
    assert output['Clean padded']['A4'].value=='00012'
    assert output['Clean padded']['A4'].data_type=='s'
    assert output['Clean padded']['A4'].number_format=='@'
    output.close()


def test_row_limit_fails_without_completion_manifest(store,tmp_path,monkeypatch):
    pid=store.create('Limit')['id']
    add_csv(store,pid,tmp_path,'rows.csv','id,value\n001,a\n002,b\n003,c\n')
    store.set_recipe(pid)
    monkeypatch.setattr('workbench.engine.MAX_ROWS',2)
    result=store.start_run(pid,synchronous=True)
    assert result['state']=='failed' and 'manifest' not in result
    assert 'one-million-row limit' in result['message']


def test_explicit_table_boundary_excludes_footer_from_selected_input(store,tmp_path):
    pid=store.create('Table boundary')['id']
    source=add_csv(store,pid,tmp_path,'rows.csv','id,amount\n001,5\n002,6\nTotal,11\n')
    store.configure_source(pid,source['id'],{**source['settings'],'end_row':3})
    store.set_recipe(pid)
    result=completed(store,pid)
    assert result['manifest']['input_rows']==2
    assert result['manifest']['tables'][0]['totals']['amount']['sum']=='11'
