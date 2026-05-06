import pandas as pd
import orjson
import sys

df = pd.read_csv('asset_pages_2026-04-30T04-52-28-156Z.csv', nrows=2)
for idx, row in df.iterrows():
    print(f"Row {idx}:")
    try:
        j = orjson.loads(row['extracted_json'])
        print(list(j.keys()))
        if 'content' in j:
             print('content keys:', list(j['content'].keys()))
             if 'entities' in j['content']:
                 print('first entity:', j['content']['entities'][0] if j['content']['entities'] else 'no entities')
    except Exception as e:
        print("JSON parse error:", e)
