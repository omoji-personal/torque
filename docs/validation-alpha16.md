# Validation for alpha 16

Alpha 16 adds the delegated approver to connected mode. This file is started during the
build and completed at release.

## Edited a15 tests

Every alpha 15 test passes without edits except one.

- `tests/test_gate_connected.py::test_hook_end_to_end`. Its first line set only
  `TORQUE_CLIENT=acme` and expected the hook to bind the session to Acme. Requirement 9
  makes the gate bind a connected session to a client only from its launch record, so
  `TORQUE_CLIENT` set by hand no longer binds (`tests/test_launch_binding_gate.py`
  covers that case). The line is replaced by what `torque launch` does: write a human
  launch record for the test process and set `TORQUE_CLIENT` and `TORQUE_LAUNCH` from it.
  Before:

  ```python
  monkeypatch.setenv("TORQUE_CLIENT", "acme")
  ```

  After:

  ```python
  from torque import launch
  record = launch.write_launch_record(w, "Acme", "human")
  monkeypatch.setenv("TORQUE_CLIENT", "acme")
  monkeypatch.setenv("TORQUE_LAUNCH", record["id"])
  ```

  The rest of the test, and what it asserts, is unchanged.
