# meticulous-watcher
A supervisory service that will keep an eye on the backend and handle software  updates

## Checking out

This repository has a submodule. Clone with `--recursive`, or run
`git submodule update --init` in an existing clone:

```
git clone --recursive git@github.com:MeticulousHome/meticulous-watcher.git
```

`log_redactor/` is [meticulous-log-redactor](https://github.com/MeticulousHome/meticulous-log-redactor),
shared with `meticulous-backend`. It strips personal data — SSIDs, MACs, IPv6
addresses, credentials, timezone — out of the journal before a bug report leaves
the device, and both services run the same rules with the same per-device key so
the same value gets the same token in either one. Without the submodule,
`log_collector` fails to import and no bug report can be built.

See `log_redactor/REDACTION_SPEC.md` for what is redacted and why. A rule change
lands in that repository first, then here, then in the backend — that order is
enforced by `meticulous-machine`, because the watcher filters last and must never
be missing a rule the backend already has.

## Tests

```
pytest -v
```

`pytest.ini` covers both this repo's `tests/` and the submodule's own suite, so a
bad submodule bump fails here. `tests/test_log_integration.py` needs
`systemd-python` and a machine with a journal; it does not run on a dev host.
