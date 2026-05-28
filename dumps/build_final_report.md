# CL650-6134 build final report

Generated: 2026-05-19T09:39:44.375975+00:00
asset_id: `62368985-01a6-4f6d-b6de-932775401d76`

## Node counts

| Label | Count |
|---|---|
| `Page` | 16482 |
| `Document` | 661 |
| `Form1` | 151 |
| `CRS` | 276 |
| `WorkPackage` | 206 |
| `JobCard` | 1065 |
| `NonRoutineCard` | 35 |
| `Repair` | 1 |
| `Modification` | 289 |
| `STC` | 352 |
| `BorescopeReport` | 0 |
| `NDTReport` | 0 |
| `DentBuckleEntry` | 3 |
| `Stamp` | 19403 |
| `PartNumber` | 14624 |
| `SerialNumber` | 4101 |
| `CertificateNumber` | 1503 |
| `Component` | 18161 |
| `Event` | 29013 |
| `Finding` | 200 |
| `PriorityItem` | 2092 |
| `Person` | 1351 |
| `MaintenanceOrganization` | 0 |
| `AirworthinessDirective` | 1170 |
| `ServiceBulletin` | 1481 |
| `ATAChapter` | 1681 |
| `Date` | 1837 |
| **TOTAL nodes (asset-scoped)** | 128632 |
| **TOTAL edges (asset-scoped)** | 319930 |

## Acceptance test cases

### PASS — NiCad battery (EASA single-item header_fields variant)

- Page UID: `c608f9ce-aade-468c-9e46-1a5a5686899f`

| Check | Result | Detail |
|---|---|---|
| value | PASS | form1::00029737 |
| pn | PASS | found 024453-000 |
| sn | PASS | found 090520021095E |
| status | PASS | got='OVERHAULED' want='OVERHAULED' |
| signer | PASS | got='Sklyarenko Natalya' want='Sklyarenko Natalya' |
| cert | PASS | got='EASA.145.0321' want='EASA.145.0321' |
| date | PASS | got='2019-08-27' want='2019-08-27' |

Actual properties:
```
  v: form1::00029737
  tn: 00029737
  pn: 024453-000
  sn: 090520021095E
  st: OVERHAULED
  signer: Sklyarenko Natalya
  cert: EASA.145.0321
  date: 2019-08-27
  issuer: JSC VTS
```

### PASS — Bombardier window-shade batch cert (TCCA, multi-SN cell)

- Page UID: `aea06e12-ed91-419c-b6d1-1b84ba8bd548`

| Check | Result | Detail |
|---|---|---|
| value | PASS | form1::170001777769 - 1 |
| pn | PASS | found 604DX2529029AND001 |
| sns | PASS | found all of ['4005', '4006', '4007'] |
| status | PASS | got='NEW' want='NEW' |
| signer | PASS | got='Michael Jernigan' want='Michael Jernigan' |
| date | PASS | got='2019-07-01' want='2019-07-01' |

Actual properties:
```
  v: form1::170001777769 - 1
  tn: 170001777769 - 1
  pn: 604DX2529029AND001
  sn: 4005, 4006, 4007
  st: NEW
  signer: Michael Jernigan
  cert: B0462730 / 12 - 58
  date: 2019-07-01
  issuer: Bombardier Inc.
P.O Box 6087, station Centre-Ville
Montreal, Quebec, Canada,
H3C 3G9
```

### PASS — ITT valve (FAA 8130-3, multi-PN block 8)

- Page UID: `82b0d5f3-6b09-48d4-a853-a939357499c0`

| Check | Result | Detail |
|---|---|---|
| value | PASS | form1::283704 |
| pns | PASS | found all of ['AV16B2177-3', '601-62900-7'] |
| sn | PASS | found AA441157 |
| status | PASS | got='REPAIRED' want='REPAIRED' |
| signer | PASS | got='Olivia Mora' want='Olivia Mora' |
| cert | PASS | got='BV4R090M' want='BV4R090M' |
| date | PASS | got='2019-10-22' want='2019-10-22' |

Actual properties:
```
  v: form1::283704
  tn: 283704
  pn: AV16B2177-3
  sn: AA441157
  st: REPAIRED
  signer: Olivia Mora
  cert: BV4R090M
  date: 2019-10-22
  issuer: ITT AEROSPACE CONTROLS
28150 INDUSTRY DRIVE, VALENCIA, CA 91355
```

## Phase 7.5 verification summary

- findings examined: 200
- closed false positive: 40
- still open: 160

## Known limitations

- Form 1 entity extraction relies on `entities[].location_context` because the OCR vintage doesn't populate `header_fields`. Edge-case Form 1s where the OCR didn't tag `Block 3` / `Block 14` location contexts will be missed.
- Phase 7 runs the mechanical 9-step baseline; a judgement-driven Phase 7 would produce far fewer high-quality findings.
- The asset has no `asset_profile.json` — Phase 2 stamps a Challenger 650 / MSN 6134 / TypeCertificate CL-600-2B16 directly without per-profile validation.
- No PartFamily / sibling-overhaul propagation in this run.