# Editing areas & zone geometry

How to add, remove, or reshape the coastal areas the app shows on the map. There
are **two kinds of area**, and which file you edit depends on which kind.

| Kind | What it is | Defined in | Geometry |
|---|---|---|---|
| **Bbox area** | a simple rectangle (minor / ungauged bays: Tampa Bay, Marsh Island, …) | [`seed_areas.py`](seed_areas.py) → the `AREAS` list | a `[W, S, E, N]` box |
| **Zoned bay** | a gauge-fed oyster bay split into salinity-gradient zones (the 6 bays → 18 zones) | [`build_zone_geoms.py`](build_zone_geoms.py) → the `BAYS` dict → `zone_geoms.json` | real OSM/NHD polygon, clipped |

Both end up as `area_type='predefined'` rows in the `areas` table. The synthesis /
HAB-intersect / nearest-station logic is generic over any polygon, so **no schema
or app code changes** are needed to add, remove, or reshape an area.

> **Slugs are the stable key.** Re-running updates a row's geometry *in place* as
> long as the slug is unchanged. Changing a slug = remove the old area + add a new
> one. Keep slugs stable when you only mean to edit geometry.

---

## The re-run sequence

After any edit, from `app/backend` with the venv active:

```bash
# (only if you changed a ZONED bay's geometry/zones)
python ../scripts/build_zone_geoms.py <slug>     # regenerates that bay in zone_geoms.json

python ../scripts/seed_areas.py                   # push areas to the DB (upsert + prune)
python manage.py compute-indicators               # refresh briefings/verdicts
```

`seed_areas.py` is **idempotent** and the **single source of truth** for which
areas exist: it upserts every area in (`AREAS` + the manifest) and then **prunes**
any DB area whose slug is no longer present (see *Remove*, below). The Docker DB
must be up (`oyster_db` on host port 5433).

---

## Change geometry

### A zoned bay (e.g. smooth Galveston's coastline)

Edit [`build_zone_geoms.py`](build_zone_geoms.py):

- **Smoothness / jaggedness** — the `SIMPLIFY_TOL` constant near the top
  (`0.001` ≈ 100 m). Raise it (e.g. `0.003`–`0.005`) to smooth the coastline and
  cut vertex counts; too high gets blocky.
- **Zone boundaries** — each zone in the `BAYS` dict has a `cut_definition` clip
  box `[W, S, E, N]`. Move those lines to shift where upper/mid/lower split.
- **Overall extent / source** — the bay's `fetch` entry (OSM relation id or NHD
  bbox).

Then rebuild *just that bay* and re-seed (other bays untouched):
```bash
python ../scripts/build_zone_geoms.py galveston-bay
python ../scripts/seed_areas.py
```
Fetches are cached under `scripts/.cache/`; delete a bay's cache file to force a
fresh download.

### A bbox area

Edit its `[W, S, E, N]` in the `AREAS` list in [`seed_areas.py`](seed_areas.py)
and re-run `seed_areas.py`. Trivial.

---

## Add an area

### A simple rectangle (easiest)

Add one tuple to `AREAS` in [`seed_areas.py`](seed_areas.py):
```python
("slug", "Display Name", "gulf" | "east_coast",
 [west, south, east, north],
 "One-line description.",
 ["<usgs_site_no>", ...]),   # linked gauges, or [] if none
```
Re-run `seed_areas.py`. (This is exactly how `marsh-island` was added.)

### A new zoned bay (moderate)

The fiddly part is finding a clean geometry source. Add an entry to the `BAYS`
dict in [`build_zone_geoms.py`](build_zone_geoms.py) with a `fetch` and a list of
zones (each: slug, name, `[W,S,E,N]` clip box, linked gauges, description), then:
```bash
python ../scripts/build_zone_geoms.py <new-slug>
python ../scripts/seed_areas.py
```
**Geometry sources** (what we learned building the existing six):
- **OSM relation** (`{"kind": "relation", "id": <rel>}`) — best when the bay has a
  clean `natural=water`/`natural=bay` relation (Mobile, Chesapeake, Pamlico).
- **NHD** (`{"kind": "nhd_bbox", "bbox": (s,w,n,e)}`) — for bays with no clean OSM
  polygon (Barataria, Galveston, Albemarle). It queries **both** NHD layers and
  unions them: **layer 9 (Area)** has the big estuaries (Galveston Bay, Albemarle
  Sound); **layer 12 (Waterbody)** has bay/lake polygons (Barataria). Use a
  **tight** bbox — a wide one trips layer 12's 2000-record cap and drops the bay.
- If a gauge feeds the bay, also add it to [`seed_gauges.py`](seed_gauges.py)
  (with its `nwm_reach_id` for forecasts) and re-run `seed_gauges.py`.

---

## Remove an area

**One-step edit:** delete the tuple from `AREAS` (bbox) or the bay's entry from the
`BAYS` dict (zoned, then rebuild the manifest), and re-run `seed_areas.py`. The
prune step deletes any DB area whose slug is no longer in the seed set, cascading
to that area's indicators and snapshots.

> **Safety guard:** pruning is **skipped if `zone_geoms.json` is missing**, so a
> missing manifest can never wipe the zone areas. If you remove a zoned bay, also
> delete its zones from the manifest (re-run `build_zone_geoms.py` after removing
> the `BAYS` entry, or delete the entries from `zone_geoms.json`) — otherwise the
> seed re-loads them from the manifest and they won't be pruned.

To remove a single ad-hoc area without touching files, a manual SQL delete also
works (it cascades): `DELETE FROM areas WHERE slug = '<slug>';`
