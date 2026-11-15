"""
grid_db.py — JedAI navigation cell store (was nav_database.py)
===============================================================
Manages a sparse SQLite grid of 5m cells per planet.
Each cell has one of three states:
  UNEXPLORED (0) — default. Usually no row, but rows with state 0 are created
                   when a region is subdivided around a blocked cell.
  CLEAR      (1) — bot has passed through this cell
  BLOCKED    (2) — bot failed to move into this cell

Adaptive resolution: obstacle-adjacent cells are subdivided to 1m.

Usage:
    db = NavDatabase("data/jedai_nav.db")
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
SUSPECT    = 3   # bumped into once: dear for D*, BLOCKED on a second bump (8 Nov)

# Grid resolutions (metres per cell)
RES_NORMAL   = 5
RES_FINE     = 1   # subdivided near obstacles

STATE_NAMES = {UNEXPLORED: "unexplored", CLEAR: "clear", BLOCKED: "blocked", SUSPECT: "suspect"}


def world_to_cell(world_coord: float, resolution: int) -> int:
    """Convert a world coordinate to a cell index at the given resolution."""
    return int(math.floor(world_coord / resolution))


def cell_to_world_centre(cell_idx: int, resolution: int) -> float:
    """Return the world coordinate at the centre of a cell."""
    return (cell_idx * resolution) + (resolution / 2.0)


class NavDatabase:
    def __init__(self, db_path: str = "jedai_nav.db", resolution: int = RES_NORMAL):
        self.db_path = db_path
        # default cell size for marks and queries (planner.resolution_m).
        # 8 Nov: 2 m, so a bump next to a narrow stair doesn't wall it off.
        self.res = int(resolution)
        self._conn = None
        self._init_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self):
        """
        One connection for the life of this object.

        FIX (harness runs, 7 Nov 2026): this used to open a new connection on
        every call (several per tick from D*). `with conn:` only commits, it
        doesn't close, so file handles piled up until, ~10 minutes into a long
        drive, sqlite failed with "unable to open database file" and the
        whole navigation run crashed - three runs in a row.
        `with self._connect() as conn:` still works: it commits or rolls back.
        """
        if self._conn is None:
            conn = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")   # safe for concurrent access
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.row_factory = sqlite3.Row
            self._conn = conn
        return self._conn

    @contextmanager
    def _tx(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

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

                CREATE TABLE IF NOT EXISTS nav_areas (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    planet      TEXT    NOT NULL,
                    cx          REAL    NOT NULL,
                    cz          REAL    NOT NULL,
                    cy          REAL,
                    radius      REAL    NOT NULL,
                    created     REAL    NOT NULL,
                    status      TEXT    NOT NULL DEFAULT 'pending_review',
                    visits      INTEGER NOT NULL DEFAULT 1,
                    notes       TEXT    NOT NULL DEFAULT ''
                );

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
                   state: int, resolution: int | None = None):
        resolution = resolution or self.res
        cx = world_to_cell(wx, resolution)
        cz = world_to_cell(wz, resolution)
        now = time.time()
        with self._tx() as conn:
            conn.execute("""
                INSERT INTO nav_cells (planet, cx, cz, resolution, state, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(planet, cx, cz, resolution)
                DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at
            """, (planet, cx, cz, resolution, state, now))
            if resolution == RES_NORMAL:
                # FIX: keep any 1m sub-cells in step with their 5m parent.
                # get_state() prefers 1m data, and sub-cells created earlier
                # (when a neighbour was blocked) were never updated, so a cell
                # near an old obstacle ignored every later mark.
                k = RES_NORMAL // RES_FINE
                conn.execute("""
                    UPDATE nav_cells SET state=?, updated_at=?
                    WHERE planet=? AND resolution=?
                      AND cx BETWEEN ? AND ? AND cz BETWEEN ? AND ?
                """, (state, now, planet, RES_FINE, cx * k, cx * k + k - 1, cz * k, cz * k + k - 1))

    def mark_clear(self, planet: str, wx: float, wz: float,
                   resolution: int | None = None):
        resolution = resolution or self.res
        """Mark the cell at (wx, wz) as clear. Safe to call every nav tick."""
        self._set_state(planet, wx, wz, CLEAR, resolution)

    def mark_blocked(self, planet: str, wx: float, wz: float,
                     resolution: int | None = None):
        resolution = resolution or self.res
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
                  resolution: int | None = None) -> int:
        resolution = resolution or self.res
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
                            resolution: int | None = None) -> list[dict]:
        resolution = resolution or self.res
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
        radius_cells = int(math.ceil(max_radius_m / self.res))
        cx = world_to_cell(wx, self.res)
        cz = world_to_cell(wz, self.res)
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
            """, (planet, self.res, CLEAR,
                  cx - radius_cells, cx + radius_cells,
                  cz - radius_cells, cz + radius_cells,
                  cx, cx, cz, cz)).fetchall()

        best = None
        best_score = float('inf')

        for row in rows:
            cell_wx = cell_to_world_centre(row["cx"], self.res)
            cell_wz = cell_to_world_centre(row["cz"], self.res)
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

    # ------------------------------------------------------------------
    # Problem areas ("stuck in a box"): mapped on their own layer, reviewed later
    # ------------------------------------------------------------------

    def clear_blocked_near(self, planet: str, wx: float, wz: float, radius_m: float) -> int:
        """Forget BLOCKED cells (any resolution) within radius_m of (wx, wz). Returns how many."""
        with self._tx() as conn:
            rows = conn.execute("SELECT cx, cz, resolution FROM nav_cells WHERE planet = ? AND state IN (?, ?)",
                                (planet, BLOCKED, SUSPECT)).fetchall()
            gone = []
            for r in rows:
                res = r["resolution"] if hasattr(r, "keys") else r[2]
                cx = r["cx"] if hasattr(r, "keys") else r[0]
                cz = r["cz"] if hasattr(r, "keys") else r[1]
                x, z = cell_to_world_centre(cx, res), cell_to_world_centre(cz, res)
                if (x - wx) ** 2 + (z - wz) ** 2 <= radius_m ** 2:
                    gone.append((planet, cx, cz, res))
            conn.executemany("DELETE FROM nav_cells WHERE planet = ? AND cx = ? AND cz = ? AND resolution = ?", gone)
            return len(gone)

    def clear_layer(self, planet: str) -> int:
        """Forget every cell of one layer (e.g. 'naboo@area2'). Returns how many."""
        with self._tx() as conn:
            cur = conn.execute("DELETE FROM nav_cells WHERE planet = ?", (planet,))
            return cur.rowcount

    def find_area(self, planet: str, x: float, z: float):
        """The area on this planet whose circle contains (x, z), or None."""
        with self._connect() as conn:
            for r in conn.execute("SELECT * FROM nav_areas WHERE planet = ? AND status != 'dismissed'",
                                  (planet,)):
                if math.hypot(r["cx"] - x, r["cz"] - z) <= r["radius"]:
                    return dict(r)
        return None

    def add_area(self, planet: str, x: float, z: float, radius: float, height=None, note: str = "") -> int:
        with self._tx() as conn:
            cur = conn.execute("INSERT INTO nav_areas (planet, cx, cz, cy, radius, created, notes) "
                               "VALUES (?, ?, ?, ?, ?, ?, ?)",
                               (planet, x, z, height, radius, time.time(), note))
            return cur.lastrowid

    def note_area(self, area_id: int, note: str, visit: bool = False) -> None:
        with self._tx() as conn:
            conn.execute("UPDATE nav_areas SET notes = notes || ? , visits = visits + ? WHERE id = ?",
                         (f"[{time.strftime('%Y-%m-%d %H:%M')}] {note}\n", 1 if visit else 0, area_id))

    def set_area_status(self, area_id: int, status: str) -> None:
        with self._tx() as conn:
            conn.execute("UPDATE nav_areas SET status = ? WHERE id = ?", (status, area_id))

    def areas(self, planet: str | None = None) -> list[dict]:
        with self._connect() as conn:
            q = "SELECT * FROM nav_areas" + (" WHERE planet = ?" if planet else "") + " ORDER BY id"
            return [dict(r) for r in conn.execute(q, (planet,) if planet else ())]

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
