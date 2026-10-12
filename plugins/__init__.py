"""
Plugin Executor layer.

Importing this package auto-discovers and imports every .py module in this
folder — that's what makes each plugin's functions available for
command_router.py to call directly. Drop a new plugins/<name>.py file in
here and it's picked up automatically on next start.

A module can opt out of auto-import by prefixing its filename with `_`
(e.g. `_helpers.py`) — useful for shared helper code that isn't itself a
plugin and shouldn't be imported for side effects at package-load time.
"""

import importlib
import pkgutil

from logger import log

_loaded = []
_failed = []

for _finder, _module_name, _is_pkg in pkgutil.iter_modules(__path__):
    if _module_name.startswith("_"):
        continue
    try:
        importlib.import_module(f"{__name__}.{_module_name}")
        _loaded.append(_module_name)
    except Exception as e:
        # One broken plugin file must not take the whole bot down — log it
        # and keep loading the rest.
        _failed.append(_module_name)
        log.error(f"plugins: failed to load '{_module_name}': {e}")

log.ok(f"plugins: loaded {len(_loaded)} module(s): {', '.join(sorted(_loaded))}")
if _failed:
    log.warn(f"plugins: {len(_failed)} module(s) failed to load: {', '.join(sorted(_failed))}")
