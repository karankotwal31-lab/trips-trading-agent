# Owner review — P006

1. Review the patch and apply it on an isolated branch. No approval artifacts are included.
2. Run the three commands in proposal.json. Review POSIX single-host deployment and shared durable state requirements.
3. Review the included regression-suite registration and pinned inventory addition.
4. Have the owner reapprove the executable build and test manifest through the existing approval process. The agent has not regenerated those artifacts.
5. Run the full safety gate on the resulting exact commit and review all manifest changes.
6. Complete strategy, broker, data, host and owner authorization gates independently. P006 grants no live authority.

Files affected: four execution modules, tests/test_execution_layer.py, new tests/test_launch_boundary_regressions.py, and the suite runner/inventory. No frozen v0.6 file, financial limit, owner key or configuration is changed.
