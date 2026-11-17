"""Core3 navmesh parser: synthetic one-tile file with ground and a rooftop island."""

import math
import struct

import numpy as np

from jedai.navigation import navmesh as N


def _write(path, verts, polys):
    """verts: list of (game_x, game_y, height); polys: list of (vert idx list, neis list)."""
    rv = [(x, h, -y) for x, y, h in verts]                       # game -> Recast axes
    hdr = struct.pack("<15i10f", N.DNAV, 7, 0, 0, 0, 0, len(polys), len(rv), 0, 0, 0, 0, 0, 0, len(polys),
                      1.5, 0.5, 0.75, 0, 0, 0, 0, 0, 0, 1.0)
    body = b"".join(struct.pack("<3f", *v) for v in rv)
    for vi, ne in polys:
        vi6 = list(vi) + [0] * (6 - len(vi))
        ne6 = list(ne) + [0] * (6 - len(ne))
        body += struct.pack("<I6H6HHBB", 0, *vi6, *ne6, 1, len(vi), 0)
    tile = hdr + body
    with open(path, "wb") as f:
        f.write(struct.pack("<iii3fffii", N.MSET, 1, 1, 0, 0, 0, 100, 100, 1, 1000))
        f.write(struct.pack("<Ii", 1, len(tile)))
        f.write(tile)


def test_ground_kept_roof_dropped(tmp_path):
    verts = [
        # ground: two quads 0..40 x 0..40 sharing the edge x=20 (internal link)
        (0, 0, 0), (20, 0, 0), (20, 40, 0), (0, 40, 0), (40, 0, 0), (40, 40, 0),
        # roof island at height 8, over x 25..35, y 25..35 (a hole in the ground below)
        (25, 25, 8), (35, 25, 8), (35, 35, 8), (25, 35, 8),
    ]
    polys = [
        ([0, 1, 2, 3], [0, 2, 0, 0]),          # edge 1 (verts 1->2) links to poly index 1 (+1)
        ([1, 4, 5, 2], [0, 0, 0, 1]),          # edge 3 (verts 2->1) links back to poly 0
        ([6, 7, 8, 9], [0, 0, 0, 0]),          # roof: no neighbours
    ]
    p = tmp_path / "naboo_city_test"
    _write(p, verts, polys)
    nm = N.load(str(p))
    assert nm.planet == "naboo"
    assert len(nm.polys) == 3 and len(nm.ground_polys()) == 2
    # y axis: game y north came back positive
    assert math.isclose(nm.bmax[1], 40, abs_tol=1e-3)
    cm = N.rasterise(nm, cell=5.0)
    assert cm.fraction(12, 12) == 1.0
    assert cm.fraction(500, 500) is None
    assert np.nanmax(cm.walk) == 1.0


def test_roof_outside_ground_is_blocked(tmp_path):
    verts = [(0, 0, 0), (40, 0, 0), (40, 20, 0), (0, 20, 0),
             (0, 25, 8), (40, 25, 8), (40, 40, 8), (0, 40, 8)]
    polys = [([0, 1, 2, 3], [0, 0, 0, 0]), ([4, 5, 6, 7], [0, 0, 0, 0])]
    p = tmp_path / "tatooine_city_test"
    _write(p, verts, polys)
    nm = N.load(str(p))
    # equal areas: whichever is "main", the other must be dropped
    cm = N.rasterise(nm)
    a, b = cm.fraction(20, 10), cm.fraction(20, 32)
    assert {a, b} == {0.0, 1.0}
    full = N.rasterise(nm, ground_only=False)
    assert full.fraction(20, 10) == 1.0 and full.fraction(20, 32) == 1.0


def test_dstar_prior_steers_round_unwalkable(tmp_path):
    """With a prior saying x in [10,30) is not walkable, D* plans round it; without, straight."""
    from jedai.navigation.dstar_lite import DStarLite
    from jedai.navigation.grid_db import NavDatabase

    def wall(wx, wz):
        return 0.0 if 10 <= wx < 30 and -40 <= wz < 40 else 1.0

    def plan(prior):
        db = NavDatabase(str(tmp_path / f"nav_{prior is None}.db"))
        d = DStarLite(db, "naboo", prior=prior)
        d.set_start(0, 0)
        d.set_goal(60, 0)
        d.initialise()
        return d.get_planned_path(40)

    straight = plan(None)
    around = plan(wall)
    assert any(10 <= x < 30 and abs(z) < 5 for x, z in straight)
    assert not any(10 <= x < 30 and -40 <= z < 40 for x, z in around)


def _terrace(tmp_path):
    """Ground at height 0 west and north of a terrace at height 3 (x 20..40, y 0..20). The only
    way up is a ramp at its north-east corner (x 30..40, y 20..30)."""
    verts = [(0, 0, 0), (20, 0, 0), (20, 20, 0), (0, 20, 0),                 # 0-3 ground G0
             (20, 0, 3), (40, 0, 3), (40, 20, 3), (20, 20, 3), (30, 20, 3),   # 4-8 terrace T
             (30, 20, 0), (30, 30, 0), (0, 30, 0), (40, 30, 0)]               # 9-12 ground G1, ramp
    polys = [([0, 1, 2, 3], [0, 0, 3, 0]),             # G0: north edge -> G1
             ([4, 5, 6, 8, 7], [0, 0, 4, 0, 0]),       # T: edge 6->8 -> ramp
             ([3, 2, 9, 10, 11], [1, 0, 4, 0, 0]),     # G1: -> G0, -> ramp
             ([8, 6, 12, 10], [2, 0, 0, 3])]           # ramp: -> T, -> G1
    p = tmp_path / "naboo_city_terrace"
    _write(p, verts, polys)
    return N.load(str(p))


def test_route_goes_round_to_the_ramp_for_a_target_on_a_terrace(tmp_path):
    """17 Nov: the delivery NPC was on a terrace 2 m above the bot, 5 m away across its wall."""
    nm = _terrace(tmp_path)
    pts, info = N.route(nm, (18.0, 10.0, 0.0), (25.0, 10.0), other_level=True)
    assert pts is not None, info
    assert info["goal_h"] == 3.0 and info["start_h"] == 0.0
    assert max(y for _x, y in pts) > 20 and max(x for x, _y in pts) > 30      # out north, up the ramp
    assert info["length"] > 30                                                  # not the 7 m through the wall


def test_route_other_level_refuses_a_target_on_our_own_level(tmp_path):
    nm = _terrace(tmp_path)
    pts, info = N.route(nm, (5.0, 25.0, 0.0), (10.0, 10.0), other_level=True)
    assert pts is None and "another level" in info["why"]
    pts, info = N.route(nm, (5.0, 25.0, 0.0), (10.0, 10.0))
    assert pts is not None and info["length"] < 20
