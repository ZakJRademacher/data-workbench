import json
from datetime import datetime

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


def test_timestamp_rounding_and_extra_missing_markers_from_assigned_format(store, tmp_path):
    pid = store.create('Opt-in parsing')['id']
    first = add_csv(store, pid, tmp_path, 'first.csv',
                    'id,region,amount,happened\nA,NA,1.235,2026-10-01T23:00:00-04:00\n'
                    'B,West,-2.345,2026-10-03T09:45:30.123456Z\n')
    add_csv(store, pid, tmp_path, 'second.csv',
            'id,region,amount,happened\nC,NA,5.00,2026-10-04 09:45:30\n'
            'D,West,10.00,2026-02-30T09:45:30Z\n')
    recipe = store.set_recipe(pid)
    table = recipe['tables'][0]
    fields = {f['name']: f for f in table['fields']}
    fields['amount'].update(type='decimal', decimal_places=2, precision_mode='round', inferred=False)
    fields['happened'].update(type='timestamp', inferred=False)
    recipe['formats'] = {'First export': {'fields': {'region': {'missing': ['NA']}}}}
    next(s for s in table['sources'] if s['id'] == first['id'])['format'] = 'First export'
    store.set_recipe(pid, recipe)
    result = completed(store, pid)
    assert result['manifest']['tables'][0]['review'] == 1
    grid = store.preview(pid, result['id'], table['name'])
    rows = {row['id']: row for row in (dict(zip(grid['columns'], r)) for r in grid['rows'])}
    assert rows['A']['region'] is None and rows['C']['region'] == 'NA'
    assert (str(rows['A']['amount']), str(rows['B']['amount'])) == ('1.24', '-2.35')
    assert datetime.fromisoformat(str(rows['A']['happened'])) == datetime(2026, 10, 2, 3)
    assert datetime.fromisoformat(str(rows['B']['happened'])).microsecond == 123456


def test_later_values_are_included_in_type_suggestions(store, tmp_path):
    pid = store.create('Representative sample')['id']
    add_csv(store, pid, tmp_path, 'sample.csv',
            'sequence,value\n' + ''.join(f'{i},12\n' for i in range(1100)) + '1100,not numeric\n')
    table = store.set_recipe(pid)['tables'][0]
    assert next(field for field in table['fields'] if field['name'] == 'value')['type'] == 'text'


def test_csv_recovery_keeps_values_and_holds_malformed_records(store, tmp_path):
    pid = store.create('Partial but traceable')['id']
    source = add_csv(store, pid, tmp_path, 'rows.csv', 'id,amount\nA,2\nB\nC,3,extra\nD,4\n')
    store.configure_source(pid, source['id'], {**source['settings'], 'header_row': 1, 'row_errors': 'review'})
    store.set_recipe(pid)
    result = completed(store, pid)
    table = result['manifest']['tables'][0]
    assert (table['accepted'], table['review']) == (2, 2)
    assert result['manifest']['malformed_rows_held'] == 2
    review = store.preview(pid, result['id'], table['name'], 'review')
    assert {int(row[review['columns'].index('__row')]) for row in review['rows']} == {3, 4}
    assert any('extra' in row[review['columns'].index('__raw')] for row in review['rows'])


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
