"""Known-label sales corruption matrix, distinct from naturally occurring defects."""
import argparse
import csv
import json
import random
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path


def main():
    p=argparse.ArgumentParser();p.add_argument('--projection',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    with args.projection.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    selected=random.Random(42).sample(rows,300)
    fields=['RowID','OrderID','OrderDate','ShipDate','Category','Region','ProductID','Sales']
    expected=[]
    for part in range(5):
        letter='abcde'[part];fmt=['%Y-%m-%d','%m/%d/%Y','%d/%m/%Y','%Y-%m-%d','%Y-%m-%d'][part]
        filename=f'batch_{letter}_seed42.csv'
        with (args.output/filename).open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fields,delimiter=';' if part==2 else ',');writer.writeheader()
            for index in range(part*60,(part+1)*60):
                row=dict(selected[index]);order=datetime.strptime(row['OrderDate'],'%Y-%m-%d')
                row['OrderDate']=order.strftime(fmt);row['ShipDate']=datetime.strptime(row['ShipDate'],'%Y-%m-%d').strftime(fmt)
                kind='valid';status='accepted'
                if index<10:row['OrderDate']='2018-02-30';kind='impossible_date';status='review'
                elif index<20:row['ShipDate']=(order-timedelta(days=1)).strftime(fmt);kind='shipping_before_order';status='review'
                elif index<30:row['Sales']=format(Decimal(row['Sales']),'.5f');kind='fifth_fractional_digit';status='review'
                elif index<40:row['RowID']='';kind='missing_key';status='review'
                elif index<50:row['Sales']='-'+row['Sales'];kind='negative_sales';status='review'
                elif index<60:row['Category']='Unknown';kind='unknown_category';status='review'
                elif index<70:row['Sales']='1,23';kind='malformed_grouping';status='review'
                elif index<80:row['ProductID']='';kind='missing_product';status='review'
                elif index<90:row['OrderDate']='2018-12-01';kind='wrong_date_convention';status='review'
                elif index<100:row['Region']='';kind='missing_region';status='review'
                elif 110<=index<120:kind='conflicting_original';status='review'
                elif 130<=index<160:row['Category']=' '+row['Category'].lower()+' ';row['Region']=row['Region'].upper();kind='valid_case_whitespace'
                elif index==160:row['RowID']='000'+row['RowID'];kind='valid_leading_zero_identifier'
                writer.writerow(row);expected.append({'file':filename,'row':index-part*60+2,'status':status,'kind':kind})
            if part==4:
                for index in range(100,120):
                    row=dict(selected[index]);conflict=index>=110
                    if conflict:row['Sales']=str(Decimal(row['Sales'])+1)
                    writer.writerow(row);expected.append({'file':filename,'row':index-100+62,'status':'review' if conflict else 'excluded','kind':'conflicting_copy' if conflict else 'exact_repeat'})
    control={'seed':42,'expected_counts':{'input':320,'accepted':190,'review':120,'excluded':10},'labels':expected}
    (args.output/'known-labels.json').write_text(json.dumps(control,indent=2))
    print(json.dumps(control['expected_counts']))


if __name__=='__main__':main()
