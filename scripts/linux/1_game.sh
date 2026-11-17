#!/usr/bin/env bash
# 1 of 3: start SWGEmu on its own display (:2). Log in and stand in Theed before step 3.
cd "$(dirname "$0")/../.." || exit 1
exec bash scripts/linux/start_swg.sh "$@"
