"""Moved to :mod:`condor.llm.openrouter_models` (ARCH-190).

This alias keeps ``handlers.agents.openrouter_models`` imports (and
monkeypatches on the module's attributes) pointing at the one real module.
"""

import sys

from condor.llm import openrouter_models as _impl

sys.modules[__name__] = _impl
