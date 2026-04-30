import pandas as pd
import orjson
csv_path = 'asset_pages_2026-04-30T04-52-28-156Z.csv'
df_iter = pd.read_csv(csv_path, chunksize=500)
count = 0
for chunk in df_iter:
    for idx, row in chunk.iterrows():
        try:
            ext = orjson.loads(row['extracted_json'])
            meta = ext.get('metadata', {})
            pns = list(meta.get('part_numbers', [])) if meta.get('part_numbers') else []
            sns = list(meta.get('serial_numbers', [])) if meta.get('serial_numbers') else []
            if pns or sns:
                print('pns:', pns, 'sns:', sns)
                count += 1
                if count > 5: break
        except: pass
    if count > 5: break
