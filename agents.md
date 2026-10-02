# Project instructions

Whenever you create a Git commit, push the committed branch to GitHub in the same task. If the push fails, report the failure explicitly; do not force-push or discard changes to bypass it.

Keep the simulation simple. Trust internal objects and configuration values supplied by our own code. Do not add defensive type, range, empty-value, or finite-number checks for those values, or tests solely for those checks, unless explicitly requested. Validate external/untrusted input at its entry boundary when needed; do not repeat that validation inside machine and item classes.
