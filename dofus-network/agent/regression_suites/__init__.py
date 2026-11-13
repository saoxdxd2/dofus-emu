"""Domain-specific regression test suites.

Each module groups related ``test_*`` functions formerly defined in
``regression.py``. ``regression.py`` re-exports them so the external
interface is unchanged.
"""

from .combat import *
from .nav import *
from .quest import *
from .dialog import *
from .proxy import *
from .memory import *

__all__ = []
for _mod in (combat, nav, quest, dialog, proxy, memory):
    for _name in getattr(_mod, "__all__", []):
        __all__.append(_name)
    globals().update({_name: getattr(_mod, _name) for _name in dir(_mod)
                      if _name.startswith("test_")})
