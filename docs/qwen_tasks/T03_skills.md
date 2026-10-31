# T03: skill interface + first skills (System 1)

## GOAL
Every in-game action is a Skill with the same shape, so System 2 can choose
between them and the harness can log them all the same way.

Needs T02 (`jedai/harness/state.py`).

## FILES TO WRITE
- `jedai/harness/skill.py`: `Result`, `Skill`, `SkillRegistry`
- `jedai/harness/skills/__init__.py`: `default_registry(runner, state, settings) -> SkillRegistry`
- `jedai/harness/skills/navigation.py`: `NavigateTo`, `Mount`, `Dismount`
- `tests/test_harness_skills.py`
- `config/settings.yaml`: ADD ONLY a new top-level `harness:` section (below). Change nothing else.

## INTERFACE
```python
@dataclass
class Result:
    status: str                 # "ok" | "failed" | "stuck"
    reason: str = ""            # short machine-readable reason, e.g. "no_waypoint"
    data: dict = field(default_factory=dict)   # anything useful (distance, duration...)
    @property
    def ok(self) -> bool

class Skill:
    name: str = "skill"         # unique, snake_case
    def preconditions(self, state: WorldState, **args) -> str | None:
        """None if the skill can run now, else a reason it can't."""
    def run(self, state: WorldState, **args) -> Result:
        """Do it. Must not raise for in-game problems: return failed/stuck instead.
        Must update `state` with anything it learned (e.g. new position)."""

class SkillRegistry:
    def register(self, skill: Skill) -> None     # error on duplicate name
    def get(self, name: str) -> Skill            # KeyError if unknown
    def names(self) -> list[str]
```

## SKILLS
**`NavigateTo`** (`name = "navigate_to"`), args: `x: float, z: float, label: str`
1. Type the waypoint command into the game:
   `runner.keys.type_text(settings.raw["harness"]["waypoint_command"].format(x=..., z=..., label=...))`
   (the waypoint name must be unique per trip: use `label`, e.g. the badge key).
2. Wait `harness.waypoint_settle_s` seconds for the waypoint to appear.
3. `outcome = runner.navigate(target_name=label)`
4. Map the outcome: `"arrived"` → ok; `"no_waypoint"` → failed("no_waypoint");
   anything starting `"crashed"` → failed("crashed") with the text in `data`;
   everything else → stuck(outcome).
5. If `runner.read_position()` returns a position, `state.set_position(settings.planet, x, z)`.

**`Mount`** (`"mount"`): precondition: not already mounted (`state.get("mounted")`).
Press the key in `harness.mount_key`, wait `harness.mount_settle_s`, set `state "mounted" = True`, ok.
If `mount_key` is empty → failed("no_mount_key").

**`Dismount`** (`"dismount"`): precondition: mounted. Call `runner.dismount()`,
set `state "mounted" = False`, ok.

## SETTINGS TO ADD (`config/settings.yaml`, new section at the end)
```yaml
harness:
  waypoint_command: "/waypoint {x:.0f} {z:.0f} {label}"   # Marc: confirm the exact syntax in-game
  waypoint_settle_s: 1.5
  mount_key: ""            # toolbar key that calls the vehicle; empty = no mount skill
  mount_settle_s: 3.0
```

## DONE WHEN
`python -m pytest -q tests` passes, with tests using a **fake runner** (a small
class in the test file with `keys.type_text`, `navigate`, `read_position`,
`dismount`, recording what was called) and `WorldState(":memory:")`:
- NavigateTo types the formatted command, calls navigate with the label, maps each
  outcome ("arrived", "no_waypoint", "stopped", "crashed: X") to the right Result
- NavigateTo updates the state position when the fake runner returns one
- Mount/Dismount preconditions and state changes; Mount with empty key → failed
- Registry: register/get/names, duplicate name raises, unknown name raises KeyError
- `default_registry` contains navigate_to, mount, dismount

## DO NOT
Change `jedai/navigation/*` or any existing setting. Touch only the files listed.
