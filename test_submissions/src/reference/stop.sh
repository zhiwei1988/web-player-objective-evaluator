#!/usr/bin/env bash
# Reference submission cleanup. start.sh execs python3 http.server in the
# foreground, so evaluator.sh kills it via process group; this hook is here
# only to satisfy the optional contract surface.
exit 0
