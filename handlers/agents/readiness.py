"""Moved to :mod:`condor.llm.readiness` (ARCH-190).

This alias keeps ``handlers.agents.readiness`` imports (and monkeypatches on
the module's attributes) pointing at the one real module object.
"""

import sys

from condor.llm import readiness as _impl

sys.modules[__name__] = _impl
