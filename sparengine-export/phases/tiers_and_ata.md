# UNIVERSAL TIER STRUCTURE + ATA CHAPTER MAPPING

Reference file. Load when running phases 3, 4, 6.

---

## Tier list (every component belongs to exactly one)

```
AIRCRAFT_CENTER   - the asset node itself (always exactly one per dossier)
ENGINE            - powerplant assemblies, modules, LLPs, accessories
ROTOR_SYSTEM      - helicopter only: main rotor, tail rotor, hubs, blades, swashplates
TRANSMISSION      - helicopter only: MGB, IGB, TGB, drive shafts, freewheel units
PROPELLER         - propellers, hubs, blades, governors, spinners
LANDING_GEAR      - MLG, NLG, actuators, shock struts, wheels, brakes, tires
AIRFRAME          - structural components, control surfaces, doors, dent and buckle scope
AVIONICS          - communication, navigation, surveillance, flight controls electronics
APU               - auxiliary power unit and accessories (only if asset has one)
SYSTEMS           - hydraulic, pneumatic, fuel, electrical, ECS, fire protection, ice and rain
INTERIOR          - cabin, cockpit, emergency equipment, furnishings (only when relevant)
```

For component-only dossiers (engine alone, propeller alone, etc.), set `tier = AIRCRAFT_CENTER` for the root component and use the relevant tier for any subcomponents in its assembly records.

The set of tiers to instantiate as `TIER_GROUP` nodes comes from `asset_profile.expected_tiers`. Do not invent tiers; do not skip ones in the profile.

---

## ATA chapter → tier mapping

```
21 Air conditioning              → SYSTEMS
22 Auto flight                   → AVIONICS
23 Communications                → AVIONICS
24 Electrical power              → SYSTEMS
25 Equipment / furnishings       → INTERIOR
26 Fire protection               → SYSTEMS
27 Flight controls               → AIRFRAME (mechanical) or AVIONICS (FBW)
28 Fuel                          → SYSTEMS
29 Hydraulic power               → SYSTEMS
30 Ice and rain protection       → SYSTEMS
31 Indicating / recording        → AVIONICS
32 Landing gear                  → LANDING_GEAR
33 Lights                        → SYSTEMS
34 Navigation                    → AVIONICS
35 Oxygen                        → SYSTEMS
36 Pneumatic                     → SYSTEMS
38 Water / waste                 → SYSTEMS
45 Central maintenance system    → AVIONICS
49 APU                           → APU
51-57 Structures / doors / fuselage / nacelles / stabilisers / windows / wings → AIRFRAME
61 Propellers                    → PROPELLER
62 Main rotor                    → ROTOR_SYSTEM
63 Main rotor drive              → TRANSMISSION
64 Tail rotor                    → ROTOR_SYSTEM
65 Tail rotor drive              → TRANSMISSION
66 Folding blades / pylon        → ROTOR_SYSTEM
67 Rotors flight control         → ROTOR_SYSTEM
71-80 Power plant / engine       → ENGINE
```

When ATA is missing on a page but the component is clearly in a system, infer the chapter from the description. When ATA conflict (page says ATA72 but description says "landing gear") → trust the description, log the conflict.

---

## Asset detection signals (Phase 2 confirmation)

```
AIRCRAFT (full):
  registration, msn, type designation in headers, full ATA spread (21..80),
  multiple logbook types (airframe + engine + propeller / rotor)
  asset_kind = AIRCRAFT
  subtype    = FIXED_WING_JET | FIXED_WING_TURBOPROP | FIXED_WING_PISTON | HELICOPTER

ENGINE-only:
  dominant esn, engine model in titles, doc types dominated by easa_form_one /
  shop_visit_report / engine_llp_status_sheet / engine_logbook, no airframe_logbook,
  no registration entity
  asset_kind = ENGINE
  subtype    = TURBOFAN | TURBOJET | TURBOPROP | TURBOSHAFT | PISTON

PROPELLER-only:
  propeller model dominant, blade SNs, governor records, no engine_logbook,
  no airframe records
  asset_kind = PROPELLER

LANDING_GEAR_ASSEMBLY:
  MLG/NLG part numbers, actuator records, shock strut records, no airframe TSN/CSN,
  position-specific (LH/RH/NLG), no full ATA spread
  asset_kind = LANDING_GEAR_ASSEMBLY

APU-only:
  APU model, APU logbook, APU shop reports
  asset_kind = APU

ROTOR_SYSTEM / GEARBOX:
  main rotor head, tail rotor, swashplate, MGB / IGB / TGB
  asset_kind = ROTOR_SYSTEM | GEARBOX

COMPONENT (catch-all):
  single PN/SN dominates, narrow scope
  asset_kind = COMPONENT
```

Helicopter detection: if any of MGB / IGB / TGB / main rotor / tail rotor entities appear → `subtype = HELICOPTER`.
