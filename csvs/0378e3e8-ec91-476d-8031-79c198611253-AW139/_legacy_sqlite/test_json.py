import pandas as pd
import orjson
import json

df = pd.read_csv('asset_pages_2026-04-30T04-52-28-156Z.csv', nrows=2)
for idx, row in df.iterrows():
    j = orjson.loads(row['extracted_json'])
    print(json.dumps(j, indent=2)[:500])
    print("---")
