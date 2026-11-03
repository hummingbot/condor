"""Moved to :mod:`condor.llm.custom_models` (ARCH-190).

This alias keeps ``handlers.agents.custom_models`` imports (and monkeypatches
on the module's attributes) pointing at the one real module object.
"""

import sys

from condor.llm import custom_models as _impl

sys.modules[__name__] = _impl
