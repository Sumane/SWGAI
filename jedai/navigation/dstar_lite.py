"""
dstar_lite.py — D* Lite Incremental Path Planner for JedAI
===========================================================
D* Lite finds the shortest path from the bot's current position to the goal,
replanning incrementally as new obstacles are discovered.

Key concepts:
  - Works on a grid of 5m cells (matches NavDatabase resolution)
  - Costs: CLEAR=1, UNEXPLORED=3, BLOCKED=inf
  - The bot is the START. The waypoint is the GOAL.
  - When a cell changes state (e.g. newly blocked), call update_cell()
    and the planner repairs only the affected portion of the path.
  - Call next_waypoint() to get the next intermediate world coordinate
    the bot should drive toward.

Reference: Koenig & Likhachev, "D* Lite" (AAAI 2002)

Fixes in v0.21 (see docs/REORGANISATION.md):
  - km is now accumulated from the start position at the last update, as in
    the paper. It was using the position from two updates ago.
  - A node popped from the queue when the search finished was dropped from the
    heap but left in the open set, so it could never be expanded again ("node
    loss"). It's now pushed back.
  - Cells marked in the database by the navigator (not via update_cell) never
    reached the planner's cache ("cache miss"). note_cell() now feeds them in.
  - Traversal costs come from config/settings.yaml (planner section).
"""

import math
import heapq
from .grid_db import (NavDatabase, UNEXPLORED, CLEAR, BLOCKED,
                      RES_NORMAL, world_to_cell, cell_to_world_centre)

# Traversal costs
COST_CLEAR      = 1.0
COST_UNEXPLORED = 3.0
COST_BLOCKED    = float('inf')

# How far around the bot to load from DB each replan (metres)
SEARCH_RADIUS_M = 300.0

# How close to the goal cell before declaring arrival (metres)
GOAL_REACH_M = 8.0




def _heuristic(cx1: int, cz1: int, cx2: int, cz2: int,
               resolution: int = RES_NORMAL) -> float:
    """Octile distance heuristic — better than Euclidean for 8-connected grid."""
    dx = abs(cx1 - cx2)
    dz = abs(cz1 - cz2)
    return resolution * (dx + dz + (math.sqrt(2) - 2) * min(dx, dz))


class DStarLite:
    """
    Incremental replanning path planner.

    Typical usage in nav loop:
        planner = DStarLite(db, planet)
        planner.set_goal(target_wx, target_wz)
        planner.set_start(bot_wx, bot_wz)
        planner.initialise()

        # Each nav tick:
        planner.update_start(bot_wx, bot_wz)   # bot moved
        wp = planner.next_waypoint()            # where to go next
        if wp:
            steer_toward(wp)

        # When obstacle discovered:
        planner.update_cell(obs_wx, obs_wz, BLOCKED)
    """

    def __init__(self, db: NavDatabase, planet: str,
                 resolution: int = RES_NORMAL,
                 cost_clear: float = COST_CLEAR,
                 cost_unexplored: float = COST_UNEXPLORED,
                 search_radius_m: float = SEARCH_RADIUS_M,
                 goal_reach_m: float = GOAL_REACH_M):
        self.db         = db
        self.planet     = planet
        self.resolution = resolution
        self.cost_clear      = cost_clear
        self.cost_unexplored = cost_unexplored
        self.search_radius_m = search_radius_m
        self.goal_reach_m    = goal_reach_m
        self._dirty = False   # vertices changed but path not recomputed yet

        self._g    = {}   # g[node]  = cost from node to goal
        self._rhs  = {}   # rhs[node] = one-step lookahead cost
        self._open = []   # priority queue: (key, node)
        self._open_set = set()

        self._km   = 0.0  # key modifier (accumulates as start moves)
        self._start = None
        self._goal  = None
        self._last_start = None

        # Local cell state cache — avoids hitting DB on every neighbour lookup
        self._cell_states = {}

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    @property
    def goal(self):
        """Goal cell (cx, cz) or None."""
        return self._goal

    def set_goal(self, wx: float, wz: float):
        self._goal = (world_to_cell(wx, self.resolution),
                      world_to_cell(wz, self.resolution))

    def set_start(self, wx: float, wz: float):
        self._start = (world_to_cell(wx, self.resolution),
                       world_to_cell(wz, self.resolution))
        self._last_start = self._start

    def initialise(self):
        """
        Full initialisation. Call once after set_goal + set_start.
        Loads known cells from DB and computes initial path.
        """
        if self._goal is None or self._start is None:
            raise RuntimeError("set_goal and set_start must be called before initialise()")

        self._g.clear()
        self._rhs.clear()
        self._open.clear()
        self._open_set.clear()
        self._km = 0.0

        # Load known cells from DB into local cache
        self._refresh_cell_cache()

        # D* Lite initialises from goal backward
        self._rhs[self._goal] = 0.0
        key = self._calculate_key(self._goal)
        heapq.heappush(self._open, (key, self._goal))
        self._open_set.add(self._goal)

        self._compute_shortest_path()
        print(f"[D*] Initialised — start={self._start} goal={self._goal} "
              f"path_cost={self._g.get(self._start, float('inf')):.1f}")

    def _refresh_cell_cache(self):
        """
        Load DB cells into local cache.
        Covers the full bounding box from start to goal (plus SEARCH_RADIUS_M margin)
        so the planner can see the entire route, not just a bubble around the bot.
        """
        if self._start is None:
            return
        start_wx = cell_to_world_centre(self._start[0], self.resolution)
        start_wz = cell_to_world_centre(self._start[1], self.resolution)

        # If we have a goal, span the full route; otherwise just local bubble
        if self._goal is not None:
            goal_wx = cell_to_world_centre(self._goal[0], self.resolution)
            goal_wz = cell_to_world_centre(self._goal[1], self.resolution)
            r = self.search_radius_m
            min_wx = min(start_wx, goal_wx) - r
            max_wx = max(start_wx, goal_wx) + r
            min_wz = min(start_wz, goal_wz) - r
            max_wz = max(start_wz, goal_wz) + r
        else:
            r = self.search_radius_m
            min_wx, max_wx = start_wx - r, start_wx + r
            min_wz, max_wz = start_wz - r, start_wz + r

        cells = self.db.get_cells_in_region(
            self.planet, min_wx, max_wx, min_wz, max_wz, self.resolution
        )
        for cell in cells:
            self._cell_states[(cell["cx"], cell["cz"])] = cell["state"]

    # ------------------------------------------------------------------
    # D* Lite core
    # ------------------------------------------------------------------

    def _g_val(self, node):
        return self._g.get(node, float('inf'))

    def _rhs_val(self, node):
        return self._rhs.get(node, float('inf'))

    def _calculate_key(self, node):
        g_rhs_min = min(self._g_val(node), self._rhs_val(node))
        h = _heuristic(self._start[0], self._start[1],
                       node[0], node[1], self.resolution)
        return (g_rhs_min + h + self._km, g_rhs_min)

    def _neighbours(self, node):
        """8-connected grid neighbours."""
        cx, cz = node
        for dcx in (-1, 0, 1):
            for dcz in (-1, 0, 1):
                if dcx == 0 and dcz == 0:
                    continue
                yield (cx + dcx, cz + dcz)

    def _state(self, node) -> int:
        return self._cell_states.get(node, UNEXPLORED)

    def _cost(self, state: int) -> float:
        if state == CLEAR:
            return self.cost_clear
        if state == UNEXPLORED:
            return self.cost_unexplored
        return COST_BLOCKED

    def _edge_cost(self, from_node, to_node) -> float:
        """Cost to move from from_node to to_node."""
        to_state = self._state(to_node)
        base = self._cost(to_state)
        if base == float('inf'):
            return float('inf')
        # Diagonal movement costs sqrt(2) more
        if from_node[0] != to_node[0] and from_node[1] != to_node[1]:
            return base * math.sqrt(2)
        return base

    def _update_vertex(self, node):
        if node != self._goal:
            best = float('inf')
            for nb in self._neighbours(node):
                c = self._edge_cost(node, nb) + self._g_val(nb)
                if c < best:
                    best = c
            self._rhs[node] = best

        # Remove from open if present
        self._open_set.discard(node)

        if self._g_val(node) != self._rhs_val(node):
            key = self._calculate_key(node)
            heapq.heappush(self._open, (key, node))
            self._open_set.add(node)

    def _compute_shortest_path(self):
        iterations = 0
        max_iterations = 200000  # safety cap

        while self._open and iterations < max_iterations:
            iterations += 1
            key, node = heapq.heappop(self._open)
            if node not in self._open_set:
                continue  # stale entry

            current_key = self._calculate_key(node)
            start_key = self._calculate_key(self._start)

            if key >= start_key and self._rhs_val(self._start) == self._g_val(self._start):
                # Done. Put the node back so it isn't lost from the queue.
                heapq.heappush(self._open, (key, node))
                break

            self._open_set.discard(node)

            if key < current_key:
                # Key increased — reinsert with updated key
                heapq.heappush(self._open, (current_key, node))
                self._open_set.add(node)
            elif self._g_val(node) > self._rhs_val(node):
                # Locally overconsistent — make consistent
                self._g[node] = self._rhs_val(node)
                for nb in self._neighbours(node):
                    self._update_vertex(nb)
            else:
                # Locally underconsistent — raise g, update neighbours
                self._g[node] = float('inf')
                self._update_vertex(node)
                for nb in self._neighbours(node):
                    self._update_vertex(nb)

        self._dirty = False
        if iterations >= max_iterations:
            print(f"[D*] Warning: hit iteration cap ({max_iterations})")

    def _ensure_consistent(self):
        if self._dirty and self._start is not None and self._goal is not None:
            self._compute_shortest_path()

    # ------------------------------------------------------------------
    # Runtime interface
    # ------------------------------------------------------------------

    def update_start(self, wx: float, wz: float):
        """
        Call every nav tick when the bot moves.
        Updates km modifier and triggers incremental replan if cell changed.
        """
        new_start = (world_to_cell(wx, self.resolution),
                     world_to_cell(wz, self.resolution))

        if new_start == self._start:
            self._ensure_consistent()
            return  # same cell, nothing else to do

        # Paper: km += h(s_last, s_start); s_last = s_start
        if self._last_start is not None:
            self._km += _heuristic(self._last_start[0], self._last_start[1],
                                    new_start[0], new_start[1], self.resolution)
        self._last_start = new_start
        self._start = new_start
        self._compute_shortest_path()

    def note_cell(self, wx: float, wz: float, new_state: int):
        """
        Tell the planner about a cell the navigator already wrote to the DB.
        Updates the cache and affected vertices; the path is recomputed lazily
        on the next update_start / next_waypoint call. No DB write, no logging.
        """
        node = (world_to_cell(wx, self.resolution),
                world_to_cell(wz, self.resolution))
        if self._cell_states.get(node, UNEXPLORED) == new_state:
            return
        self._cell_states[node] = new_state
        if self._goal is None or self._start is None:
            return
        self._update_vertex(node)
        for nb in self._neighbours(node):
            self._update_vertex(nb)
        self._dirty = True

    def update_cell(self, wx: float, wz: float, new_state: int):
        """
        Call when a cell's state changes (e.g. obstacle discovered).
        Triggers incremental replan.
        Also writes to DB.
        """
        node = (world_to_cell(wx, self.resolution),
                world_to_cell(wz, self.resolution))
        old_state = self._cell_states.get(node, UNEXPLORED)

        if old_state == new_state:
            return  # no change

        self._cell_states[node] = new_state

        # Persist to DB
        if new_state == BLOCKED:
            self.db.mark_blocked(self.planet, wx, wz, self.resolution)
        elif new_state == CLEAR:
            self.db.mark_clear(self.planet, wx, wz, self.resolution)

        # Replan — update the changed node and its neighbours
        self._update_vertex(node)
        for nb in self._neighbours(node):
            self._update_vertex(nb)
        self._compute_shortest_path()

        print(f"[D*] Cell updated ({wx:.0f},{wz:.0f}) "
              f"{['unexplored','clear','blocked'][old_state]} → "
              f"{['unexplored','clear','blocked'][new_state]} | "
              f"path_cost={self._g.get(self._start, float('inf')):.1f}")

    def next_waypoint(self) -> tuple[float, float] | None:
        """
        Return the world coordinates of the next cell to move toward,
        or None if no path exists (all routes blocked/unexplored).

        Walks one step along the lowest-cost neighbour from current start.
        """
        if self._start is None or self._goal is None:
            return None
        self._ensure_consistent()

        # Check if already at goal
        start_wx = cell_to_world_centre(self._start[0], self.resolution)
        start_wz = cell_to_world_centre(self._start[1], self.resolution)
        goal_wx  = cell_to_world_centre(self._goal[0], self.resolution)
        goal_wz  = cell_to_world_centre(self._goal[1], self.resolution)
        if math.hypot(goal_wx - start_wx, goal_wz - start_wz) <= self.goal_reach_m:
            return None  # arrived

        # Find the best next step
        best_node = None
        best_cost = float('inf')

        for nb in self._neighbours(self._start):
            edge = self._edge_cost(self._start, nb)
            if edge == float('inf'):
                continue
            total = edge + self._g_val(nb)
            if total < best_cost:
                best_cost = total
                best_node = nb

        if best_node is None or best_cost == float('inf'):
            return None  # no path

        wx = cell_to_world_centre(best_node[0], self.resolution)
        wz = cell_to_world_centre(best_node[1], self.resolution)
        return (wx, wz)

    def has_path(self) -> bool:
        """Return True if a finite-cost path exists from start to goal."""
        if self._start is None:
            return False
        self._ensure_consistent()
        return self._g_val(self._start) < float('inf')

    def path_cost(self) -> float:
        return self._g_val(self._start)

    def cost_to_goal(self, node) -> float:
        """Current cost-to-goal estimate g(node) for a cell index (cx, cz)."""
        self._ensure_consistent()
        return self._g_val(node)

    def get_planned_path(self, max_steps: int = 50) -> list[tuple[float, float]]:
        """
        Return the full planned path as world coordinates (for debug/visualisation).
        """
        if not self.has_path():
            return []

        path = []
        node = self._start
        visited = set()

        for _ in range(max_steps):
            if node == self._goal:
                break
            if node in visited:
                break
            visited.add(node)

            wx = cell_to_world_centre(node[0], self.resolution)
            wz = cell_to_world_centre(node[1], self.resolution)
            path.append((wx, wz))

            best_node = None
            best_cost = float('inf')
            for nb in self._neighbours(node):
                edge = self._edge_cost(node, nb)
                if edge == float('inf'):
                    continue
                total = edge + self._g_val(nb)
                if total < best_cost:
                    best_cost = total
                    best_node = nb

            if best_node is None:
                break
            node = best_node

        return path
