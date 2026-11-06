# Tasks for the local Qwen agent

> **T01–T06 are done** (built by Claude, 6 Nov 2026, in `jedai/harness/`). They stay here as
> examples of the task format. New tasks get the next numbers.

Each `Txx_*.md` file is one self-contained job. Point Qwen at:

1. `docs/qwen_tasks/00_CONTEXT.md` (always: the project rules)
2. the one task file you want done

and tell it: "Do this task. Follow 00_CONTEXT.md. Stop when DONE WHEN is met."

## Order

| Task | What | Needs |
|---|---|---|
| T01 | Travel points + badge locations → JSON | nothing |
| T02 | World state store (SQLite) | nothing |
| T03 | Skill interface + first skills | T02 |
| T04 | Skill event log | T02, T03 |
| T05 | Rule-based System 2: badge collector | T01, T02, T03 |
| T06 | Supervisor + `run_harness.py` | T02–T05 |

T01 and T02 can run at the same time.

## Workflow for each task

```bash
cd ~/dev/SWGAI
git checkout master && git checkout -b qwen/T01   # one branch per task
# ... Qwen works ...
python -m pytest -q tests                         # everything must still pass
git add -A && git commit -m "T01: <summary>"
git bundle create ~/qwen_T01.bundle master..qwen/T01
```

Send the bundle (or the `git diff master` output) to Claude for review before merging.
