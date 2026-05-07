# Test fixtures

## Synthetic fixtures (generated per-test)

`build_synthetic.py` exports two builders the test suite uses through
pytest fixtures (defined in `tests/conftest.py`):

| Fixture            | Builder                  | Shape                                                    |
|--------------------|--------------------------|----------------------------------------------------------|
| `synthetic_csv`    | `build_synthetic_csv`    | 10 pages, 4 documents, 1 component (PN-12345 / SN-A1)    |
| `ocr_variance_csv` | `build_ocr_variance_csv` | 6 pages, deliberately dirty (empty/malformed JSON, smart quotes, non-ASCII, unknown doc_type, is_blank) |

Both write into the test's `tmp_path` and embed a fresh random `asset_id` so
the test cleanup fixture (`_clean_asset_after_test`) can sweep up afterwards.

### Inspecting the fixtures locally

```bash
# From sparengine-export/ — generates the two CSVs in this directory
python -m tests.fixtures.build_synthetic
```

That writes `synthetic_pages.csv` and `ocr_variance.csv` here with a fixed
asset_id (`test-fixture-…000001`) so the output is byte-stable. **The CSVs
are not tracked in git** — the generator is the spec; the on-disk files are
for human inspection only.

## Real-archetype fixtures (looked up at runtime)

These fixtures don't generate data — they look for a real CSV under
`<repo>/csvs/` and skip the test if one isn't present.

| Fixture                | Glob                                                 | Use                                  |
|------------------------|------------------------------------------------------|--------------------------------------|
| `helicopter_full_csv`  | `csvs/*-AW139/*.csv`                                 | full-pipeline regression on AW139    |
| `engine_only_csv`      | `csvs/*-CFM56*/*.csv` ∪ `csvs/*-engine*/*.csv` ∪ …   | engine-only regression               |

Drop a CSV at the matching path and the regression suite picks it up
automatically. Without one, those tests `pytest.skip(...)` — they don't fail.

## Why no committed CSV fixtures?

- They drift. A small change to the schema requires regenerating the file
  AND updating the assertion that depends on it; we'd rather have one source
  of truth (the generator).
- They balloon in git. The synthetic CSV is ~6 KB; a real-archetype CSV is
  50–200 MB.
- They hide intent. Reading `build_synthetic_csv` tells you exactly what the
  shape is; reading a CSV does not.
