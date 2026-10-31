"""Explicit compatibility recipe; no input repairs or inferred business fields."""
import csv
import re
from pathlib import Path

FIELDS = {'RowID':'row_id','OrderID':'order_id','OrderDate':'order_date','ShipDate':'ship_date',
          'Category':'category','Region':'region','ProductID':'product_id','Sales':'sales'}
CATEGORIES = ['Furniture','Office Supplies','Technology']
REGIONS = ['Central','East','South','West']


def header_key(value):
    return re.sub(r'[ _-]','',value.strip().lower())


def build(sources, config):
    with (Path(config)/'column-mapping.csv').open(encoding='utf-8-sig',newline='') as f:
        aliases={header_key(row['Alias']):row['Canonical'] for row in csv.DictReader(f)}
    with (Path(config)/'file-formats.csv').open(encoding='utf-8-sig',newline='') as f:
        formats=list(csv.DictReader(f))
    fields=[]
    for original,name in FIELDS.items():
        field={'name':name,'type':'date' if original.endswith('Date') else 'decimal' if original=='Sales' else 'text',
               'required':True,'trim':True,'missing':[],'mapping':{},'allowed':[],'inferred':False}
        if original.endswith('Date'):field.update(date_format='%Y-%m-%d',date_exact=True)
        if original=='Sales':field.update(min='0',decimal_separator='.',thousands_separator=',',decimal_places=4,precision_mode='strict',unsigned=True)
        if original in ('Category','Region'):
            field.update(allowed=CATEGORIES if original=='Category' else REGIONS,case_insensitive=True)
        fields.append(field)
    assignments=[];named={}
    for source in sources:
        matches=[fmt for fmt in formats if source['filename'].lower().startswith(fmt['Prefix'].lower())]
        if len(matches)!=1:raise ValueError('Expected exactly one explicitly configured format for '+source['filename'])
        fmt=matches[0];name=fmt['Prefix']
        date={'yyyy-MM-dd':'%Y-%m-%d','MM/dd/yyyy':'%m/%d/%Y','dd/MM/yyyy':'%d/%m/%Y'}[fmt['DateFormat']]
        named[name]={'fields':{field:{'date_format':date,'date_exact':True} for field in ('order_date','ship_date')}}
        assignments.append({'id':source['id'],'format':name,'mapping':{FIELDS[aliases[header_key(h)]]:h for h in source['headers']},'ignored_columns':[]})
    return {'version':2,'formats':named,'tables':[{'name':'sales_exports','fields':fields,'key':['row_id'],
         'duplicate_policy':'drop_exact','sources':assignments,'checks':[{'left':'ship_date','op':'>=','right':'order_date','message':'ShipDate precedes OrderDate'}]}],'views':[]}
