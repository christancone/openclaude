import pandas as pd
import orjson
import json

df = pd.read_csv('asset_pages_2026-04-30T04-52-28-156Z.csv', nrows=2)
for idx, row in df.iterrows():
    j = orjson.loads(row['extracted_json'])
    print(f"Row {idx} keys:", j.keys())
    if 'sections' in j:
        for s in j['sections'][:1]:
            print(f"section type: {s.get('type')}")
            print(f"section keys: {list(s.keys())}")
            if 'entities' in s:
                print(f"entity: {s['entities'][0] if s['entities'] else 'none'}")
