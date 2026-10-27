"""
nav_database.py — JedAI Navigation Cell Store
==============================================
Manages a sparse SQLite grid of 5m cells per planet.
Each cell has one of three states:
  UNEXPLORED (0) — default, absence of a row
  CLEAR      (1) — bot has passed through this cell at vehicle speed
  BLOCKED    (2) — wall hug triggered with this cell between bot and target

Adaptive resolution: obstacle-adjacent cells can be subdivided to 1m.

Usage:
    db = NavDatabase("jedai_nav.db")
    db.mark_clear("naboo", -4856.0, 4177.0)
    db.mark_blocked("naboo", -4829.0, 4151.0)
    state = db.get_state("naboo", -4829.0, 4151.0)
"""

import sqlite3
import time
import math
from contextlib import contextmanager

# Cell states
UNEXPLORED = 0
CLEAR      = 1
BLOCKED    = 2

# Grid resolutions (metres per cell)
RES_NORMAL   = 5
RES_FINE     = 1   # subdivided near obstacles

STATE_NAMES = {UNEXPLORED: "unexplored", CLEAR: "clear", BLOCKED: "blocked"}


def world_to_cell(world_coord: float, resolution: int) -> int:
    """Convert a world coordinate to a cell index at the given resolution."""
    return int(math.floor(world_coord / resolution))


def cell_to_world_centre(cell_idx: int, resolution: int) -> float:
    """Return the world coordinate at the centre of a cell."""
    return (cell_idx * resolution) + (resolution / 2.0)


class NavDatabase:
    def __init__(self, db_path: str = "jedai_nav.db"):
        self.db_path = db_path
        self._init_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")   # safe for concurrent access
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _tx(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        with self._tx() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS nav_cells (
                    planet      TEXT    NOT NULL,
                    cx          INTEGER NOT NULL,
                    cz          INTEGER NOT NULL,
                    resolution  INTEGER NOT NULL DEFAULT 5,
                    state       INTEGER NOT NULL DEFAULT 1,
                    updated_at  REAL    NOT NULL,
                    PRIMARY KEY (planet, cx, cz, resolution)
                );

                CREATE INDEX IF NOT EXISTS idx_nav_cells_planet_state
                    ON nav_cells (planet, state);

                CREATE INDEX IF NOT EXISTS idx_nav_cells_region
                    ON nav_cells (planet, cx, cz);

                CREATE TABLE IF NOT EXISTS nav_runs (
                    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    planet      TEXT    NOT NULL,
                    start_x     REAL,
                    start_z     REAL,
                    target_x    REAL,
                    target_z    REAL,
                    started_at  REAL    NOT NULL,
                    completed   INTEGER NOT NULL DEFAULT 0
                );
            """)

    # ------------------------------------------------------------------
    # Core state operations
    # ------------------------------------------------------------------

    def _set_state(self, planet: str, wx: float, wz: float,
                   state: int, resolution: int = RES_NORMAL):
        cx = world_to_cell(wx, resolution)
        cz = world_to_cell(wz, resolution)
        with self._tx() as conn:
            conn.execute("""
                INSERT INTO nav_cells (planet, cx, cz, resolution, state, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(planet, cx, cz, resolution)
                DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at
            """, (planet, cx, cz, resolution, state, time.time()))

    def mark_clear(self, planet: str, wx: float, wz: float,
                   resolution: int = RES_NORMAL):
        """Mark the cell at (wx, wz) as clear. Safe to call every nav tick."""
        self._set_state(planet, wx, wz, CLEAR, resolution)

    def mark_blocked(self, planet: str, wx: float, wz: float,
                     resolution: int = RES_NORMAL):
        """
        Mark the cell at (wx, wz) as blocked and subdivide surrounding cells
        to fine resolution for more precise replanning near the obstacle.
        """
        self._set_state(planet, wx, wz, BLOCKED, resolution)
        # Subdivide the 5x5 area around the blocked cell into 1m cells
        # so D* Lite can find narrow gaps if they exist
        if resolution == RES_NORMAL:
            self._subdivide_region(planet, wx, wz, radius_cells=2)

    def get_state(self, planet: str, wx: float, wz: float,
                  resolution: int = RES_NORMAL) -> int:
        """
        Return the state of the cell containing (wx, wz).
        Returns UNEXPLORED if no row exists.
        Prefers fine-resolution data if available.
        """
        cx = world_to_cell(wx, resolution)
        cz = world_to_cell(wz, resolution)
        # Check fine resolution first if it exists
        if resolution == RES_NORMAL:
            fine_state = self._get_fine_state(planet, wx, wz)
            if fine_state is not None:
                return fine_state
        with self._connect() as conn:
            row = conn.execute("""
                SELECT state FROM nav_cells
                WHERE planet=? AND cx=? AND cz=? AND resolution=?
            """, (planet, cx, cz, resolution)).fetchone()
            return row["state"] if row else UNEXPLORED

    def _get_fine_state(self, planet: str, wx: float, wz: float) -> int | None:
        """Return fine-resolution state if a 1m cell exists at this position."""
        cx = world_to_cell(wx, RES_FINE)
        cz = world_to_cell(wz, RES_FINE)
        with self._connect() as conn:
            row = conn.execute("""
                SELECT state FROM nav_cells
                WHERE planet=? AND cx=? AND cz=? AND resolution=?
            """, (planet, cx, cz, RES_FINE)).fetchone()
            return row["state"] if row else None

    def _subdivide_region(self, planet: str, wx: float, wz: float,
                          radius_cells: int = 2):
        """
        For each 5m cell within radius_cells of (wx, wz), ensure 1m subcells
        exist. Inherits parent cell state (unexplored cells become unexplored
        1m cells, clear cells propagate clear). Does not overwrite existing
        fine data.
        """
        centre_cx = world_to_cell(wx, RES_NORMAL)
        centre_cz = world_to_cell(wz, RES_NORMAL)
        now = time.time()
        rows_to_insert = []

        with self._connect() as conn:
            for dcx in range(-radius_cells, radius_cells + 1):
                for dcz in range(-radius_cells, radius_cells + 1):
                    parent_cx = centre_cx + dcx
                    parent_cz = centre_cz + dcz
                    # Get parent state
                    row = conn.execute("""
                        SELECT state FROM nav_cells
                        WHERE planet=? AND cx=? AND cz=? AND resolution=5
                    """, (planet, parent_cx, parent_cz)).fetchone()
                    parent_state = row["state"] if row else UNEXPLORED

                    # Generate 1m subcells for this 5m cell
                    base_wx = parent_cx * RES_NORMAL
                    base_wz = parent_cz * RES_NORMAL
                    for fx in range(RES_NORMAL):
                        for fz in range(RES_NORMAL):
                            fine_wx = base_wx + fx
                            fine_wz = base_wz + fz
                            fcx = world_to_cell(fine_wx, RES_FINE)
                            fcz = world_to_cell(fine_wz, RES_FINE)
                            rows_to_insert.append(
                                (planet, fcx, fcz, RES_FINE, parent_state, now)
                            )

        with self._tx() as conn:
            conn.executemany("""
                INSERT OR IGNORE INTO nav_cells
                    (planet, cx, cz, resolution, state, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, rows_to_insert)

    # ------------------------------------------------------------------
    # Bulk queries for D* Lite planner
    # ------------------------------------------------------------------

    def get_cells_in_region(self, planet: str,
                            min_wx: float, max_wx: float,
                            min_wz: float, max_wz: float,
                            resolution: int = RES_NORMAL) -> list[dict]:
        """
        Return all known cells within a world-coordinate bounding box.
        Used by D* Lite to seed its local graph.
        """
        min_cx = world_to_cell(min_wx, resolution)
        max_cx = world_to_cell(max_wx, resolution)
        min_cz = world_to_cell(min_wz, resolution)
        max_cz = world_to_cell(max_wz, resolution)
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT cx, cz, state, resolution FROM nav_cells
                WHERE planet=? AND resolution=?
                  AND cx BETWEEN ? AND ?
                  AND cz BETWEEN ? AND ?
            """, (planet, resolution, min_cx, max_cx, min_cz, max_cz)).fetchall()
            return [dict(r) for r in rows]

    def get_nearest_clear(self, planet: str, wx: float, wz: float,
                          max_radius_m: float = 200.0,
                          target_wx: float = None,
                          target_wz: float = None) -> tuple[float, float] | None:
        """
        Find the nearest CLEAR cell to (wx, wz) within max_radius_m.
        If target coords are supplied, prefer cells that are closer to the
        target than the current position (i.e. cells that make progress).
        Returns world coordinates of cell centre, or None.
        """
        radius_cells = int(math.ceil(max_radius_m / RES_NORMAL))
        cx = world_to_cell(wx, RES_NORMAL)
        cz = world_to_cell(wz, RES_NORMAL)
        current_dist_to_target = None
        if target_wx is not None and target_wz is not None:
            current_dist_to_target = math.hypot(target_wx - wx, target_wz - wz)

        with self._connect() as conn:
            rows = conn.execute("""
                SELECT cx, cz FROM nav_cells
                WHERE planet=? AND resolution=? AND state=?
                  AND cx BETWEEN ? AND ?
                  AND cz BETWEEN ? AND ?
                ORDER BY ((cx - ?) * (cx - ?) + (cz - ?) * (cz - ?)) ASC
                LIMIT 50
            """, (planet, RES_NORMAL, CLEAR,
                  cx - radius_cells, cx + radius_cells,
                  cz - radius_cells, cz + radius_cells,
                  cx, cx, cz, cz)).fetchall()

        best = None
        best_score = float('inf')

        for row in rows:
            cell_wx = cell_to_world_centre(row["cx"], RES_NORMAL)
            cell_wz = cell_to_world_centre(row["cz"], RES_NORMAL)
            dist_from_bot = math.hypot(cell_wx - wx, cell_wz - wz)

            if dist_from_bot > max_radius_m:
                continue

            # If we have a target, prefer cells that make progress
            if current_dist_to_target is not None and target_wx is not None:
                dist_to_target = math.hypot(target_wx - cell_wx, target_wz - cell_wz)
                # Only consider cells that are closer to target than we are
                if dist_to_target >= current_dist_to_target:
                    continue
                score = dist_to_target  # closer to target = better
            else:
                score = dist_from_bot   # fallback: just nearest

            if score < best_score:
                best_score = score
                best = (cell_wx, cell_wz)

        return best

    # ------------------------------------------------------------------
    # Run tracking
    # ------------------------------------------------------------------

    def start_run(self, planet: str, start_x: float, start_z: float,
                  target_x: float, target_z: float) -> int:
        with self._tx() as conn:
            cur = conn.execute("""
                INSERT INTO nav_runs (planet, start_x, start_z, target_x, target_z,
                                      started_at, completed)
                VALUES (?, ?, ?, ?, ?, ?, 0)
            """, (planet, start_x, start_z, target_x, target_z, time.time()))
            return cur.lastrowid

    def complete_run(self, run_id: int):
        with self._tx() as conn:
            conn.execute("""
                UPDATE nav_runs SET completed=1 WHERE run_id=?
            """, (run_id,))

    # ------------------------------------------------------------------
    # Stats / debug
    # ------------------------------------------------------------------

    def get_stats(self, planet: str) -> dict:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT state, resolution, COUNT(*) as cnt
                FROM nav_cells WHERE planet=?
                GROUP BY state, resolution
            """, (planet,)).fetchall()
        stats = {}
        for row in rows:
            key = f"res{row['resolution']}_{STATE_NAMES[row['state']]}"
            stats[key] = row["cnt"]
        return stats

    def print_stats(self, planet: str):
        stats = self.get_stats(planet)
        print(f"\n[NavDB] {planet} cell stats:")
        for k, v in sorted(stats.items()):
            print(f"   {k}: {v}")
