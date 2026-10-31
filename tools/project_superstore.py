"""Explicit eight-field adapter for the comparable sales-contract workload."""
import argparse
import csv
from datetime import datetime
from pathlib import Path

FIELDS = {'RowID':'Row ID','OrderID':'Order ID','OrderDate':'Order Date','ShipDate':'Ship Date',
          'Category':'Category','Region':'Region','ProductID':'Product ID','Sales':'Sales'}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.source.open(encoding='utf-8-sig',newline='') as source,args.output.open('w',encoding='utf-8-sig',newline='') as target:
        reader=csv.DictReader(source);writer=csv.DictWriter(target,list(FIELDS));writer.writeheader()
        for row in reader:
            projected={new:row[old] for new,old in FIELDS.items()}
            for name in ('OrderDate','ShipDate'):
                projected[name]=datetime.strptime(projected[name],'%m/%d/%Y').strftime('%Y-%m-%d')
            writer.writerow(projected)
    print('Selected eight existing business fields; declared MDY dates serialized as ISO. No business values invented.')

if __name__=='__main__':main()
