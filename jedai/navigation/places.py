"""
"Where is the nearest <thing>?"

Two sources, both things a player has:
  * what we already know: travel points (wiki), spots recorded from Marc's
    sessions (terminals.json, clone routes), and spots the bot found itself
    (jedai/data/learned_places.json, added to every time a find succeeds);
  * the game itself: "/tar <name>" targets the nearest thing of that name in
    range and the target bar shows "<Name> (<n>m)". A distance with no
    direction, so the bot reads it from a few different spots and works out
    where the thing must be (locate()).
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from ..data import load_clone_routes, load_terminals, load_world_points

LEARNED = Path(__file__).resolve().parents[1] / "data" / "learned_places.json"

# kind -> "/tar <target>" (the target's name must contain <word>), and the "/find <find>" category
# (/find's categories, from Core3's FindSession menu: cantina, capitol, city, cloningfacility,
#  guild_combat/_commerce/_theater/_university, salon, hotel, medicalcenter, garage, shuttleport,
#  starport, vendor_junk, terminal_bank, terminal_bazaar, terminal_mission, trainer_brawler/_artisan/
#  _scout/_marksman/_entertainer/_medic)
KINDS = {
    "mission": {"target": "mission", "word": "mission", "find": "terminal_mission"},
    "bank": {"target": "bank", "word": "bank", "find": "terminal_bank"},
    "bazaar": {"target": "bazaar", "word": "bazaar", "find": "terminal_bazaar"},
    "ticket": {"target": "ticket", "word": "ticket", "find": None},
    "cloning": {"target": "cloning", "word": "clon", "find": "cloningfacility"},
    "starport": {"target": None, "word": "starport", "find": "starport"},
    "shuttleport": {"target": None, "word": "shuttle", "find": "shuttleport"},
    "medical": {"target": None, "word": "medic", "find": "medicalcenter"},
    "junk": {"target": "junk", "word": "junk", "find": "vendor_junk"},
}
FIND_CATEGORIES = {"cantina", "capitol", "city", "cloningfacility", "guild_combat", "guild_commerce", "guild_theater",
                   "guild_university", "salon", "hotel", "medicalcenter", "garage", "shuttleport", "starport",
                   "vendor_junk", "terminal_bank", "terminal_bazaar", "terminal_mission", "trainer_brawler",
                   "trainer_artisan", "trainer_scout", "trainer_marksman", "trainer_entertainer", "trainer_medic"}


def kind_info(what: str) -> dict:
    """A known kind; a /find category (cantina, trainer_scout, ...); or any name at all (/tar only)."""
    if what in KINDS:
        return KINDS[what]
    w = what.lower()
    if w in FIND_CATEGORIES:
        word = w.split("_")[-1]
        return {"target": word if w.startswith("trainer") else None, "word": word[:5], "find": w}
    return {"target": what, "word": w.split()[0], "find": None}


def parse_position(texts):
    """Numbers after the datapad's 'Position:' label -> (x, z) as read, or None. texts: the boxes on that line, left to right."""
    nums = re.findall(r"[-\u2013\u2014~]?\s*\d[\d,.]*", " ".join(texts).replace("o", "0"))
    vals = []
    for n in nums:
        neg = n.strip()[0] in "-\u2013\u2014~"
        d = re.sub(r"[^\d]", "", n.split(".")[0])
        if d:
            vals.append(-int(d) if neg else int(d))
    if len(vals) < 2:
        return None
    return float(vals[0]), float(vals[-1])


def check_position(read, pos, dist, tol=None):
    """The position read from the datapad against the distance the waypoint shows.
    A lost minus sign is the usual misread, so the four sign choices are tried.
    Returns (x, z) that fits, or None."""
    if read is None or pos is None or dist is None:
        return None
    tol = tol if tol is not None else 25 + 0.05 * dist
    fits = []
    for sx in (1, -1):
        for sz in (1, -1):
            x, z = sx * abs(read[0]), sz * abs(read[1])
            err = abs(math.hypot(x - pos[0], z - pos[1]) - dist)
            if err <= tol:
                fits.append((err + (0 if (x, z) == tuple(read) else 0.01), x, z))
    if not fits:
        return None
    _, x, z = min(fits)
    return x, z


def _learned(path=None) -> dict:
    p = Path(path or LEARNED)
    return json.loads(p.read_text()) if p.exists() else {}


def known(planet: str, kind: str, learned_path=None) -> list[dict]:
    """Every place of this kind we know on the planet: [{"name", "x", "z", "source"}]."""
    out = []
    for t in load_terminals().get(planet, {}).get(kind, []):
        out.append({"name": f"{kind} terminal, {t.get('city', '?')}", "x": t["stand"][0], "z": t["stand"][1], "source": "recorded"})
    if kind in ("starport", "shuttleport", "travel", "ticket"):
        for p in load_world_points().get(planet, {}).get("travel", []):
            if kind in ("travel", "ticket") or p.get("kind") == kind:
                out.append({"name": p["name"], "x": p["x"], "z": p["z"], "source": "wiki"})
    if kind == "cloning":
        for city, r in load_clone_routes().get(planet, {}).items():
            end = r["waypoints"][-1]
            out.append({"name": f"cloning facility, {city}", "x": end[0], "z": end[1], "source": "recorded"})
    for p in _learned(learned_path).get(planet, {}).get(kind, []):
        out.append({"name": p.get("name", kind), "x": p["x"], "z": p["z"], "source": "found"})
    return out


def nearest(planet: str, kind: str, pos, learned_path=None):
    """(place, metres) for the closest known one, or (None, None)."""
    places = known(planet, kind, learned_path)
    if not places or pos is None:
        return None, None
    best = min(places, key=lambda p: math.hypot(p["x"] - pos[0], p["z"] - pos[1]))
    return best, math.hypot(best["x"] - pos[0], best["z"] - pos[1])


def learn(planet: str, kind: str, name: str, stand, at=None, learned_path=None) -> bool:
    """Remember a place we stood at and used. False if we already had one within 10 m."""
    p = Path(learned_path or LEARNED)
    data = _learned(p)
    here = data.setdefault(planet, {}).setdefault(kind, [])
    if any(math.hypot(q["x"] - stand[0], q["z"] - stand[1]) <= 10 for q in known(planet, kind, p)):
        return False
    rec = {"name": name, "x": round(stand[0]), "z": round(stand[1])}
    if at is not None:
        rec["thing_at"] = [round(at[0], 1), round(at[1], 1)]
    here.append(rec)
    p.write_text(json.dumps(data, indent=1))
    return True


def parse_target(text: str):
    """'target: mission terminal (2m)' -> ('mission terminal', 2). None if there's no distance."""
    t = text.lower().replace("target:", " ").replace("target", " ", 1) if text.lower().lstrip().startswith("target") else text.lower()
    m = re.search(r"([a-z][a-z' \-]+?)\s*[\(\[\{]\s*([\dolisgbz.,]+)\s*m\s*[\)\]\}]?", t)
    if not m:
        return None
    # 16 Nov: the bar's "(7m)" reads as "( zm)"; "z" was taken for a 2, so a terminal 7 m off
    # counted as in reach and the browser opened empty ("too far away to 'List Missions'")
    digits = m.group(2).translate(str.maketrans("olisgbz", "0115987")).replace(",", "").split(".")[0]
    if not digits.isdigit():
        return None
    return m.group(1).strip(), int(digits)


def locate(readings, step: float = 1.0):
    """Where the thing is, from [(x, z, metres)] taken at 3+ spots not in a line.
    Returns (x, z, rms error in metres). Coarse grid, then a fine one round the best cell."""
    import numpy as np
    r = np.asarray(readings, float)
    reach = float(r[:, 2].max()) + 5

    def best(cx, cz, half, st):
        xs = np.arange(cx - half, cx + half + st, st)
        zs = np.arange(cz - half, cz + half + st, st)
        gx, gz = np.meshgrid(xs, zs)
        err = np.zeros_like(gx)
        for x, z, d in r:
            err += (np.hypot(gx - x, gz - z) - d) ** 2
        i = np.unravel_index(np.argmin(err), err.shape)
        return float(gx[i]), float(gz[i]), float(np.sqrt(err[i] / len(r)))
    x, z, _ = best(float(r[:, 0].mean()), float(r[:, 1].mean()), reach, max(step, reach / 60))
    return best(x, z, max(3.0, reach / 30), 0.25)


def spread(readings) -> float:
    """How far from a straight line the reading spots are (metres): small = the answer has a mirror twin."""
    import numpy as np
    p = np.asarray(readings, float)[:, :2]
    p = p - p.mean(axis=0)
    return float(np.sqrt(np.linalg.svd(p, compute_uv=False)[-1] ** 2 / len(p)))
