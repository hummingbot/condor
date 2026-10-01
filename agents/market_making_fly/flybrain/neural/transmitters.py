"""Declared neurotransmitter-sign proxy used in DOOMFLY graph preparation."""

import numpy as np


def transmitter_signs(transmitters, ambiguous_sign=1):
    """Declared coarse fast-transmission assumption; never deletes unknown edges.

    ACh +; GABA, glutamate, histamine -. A co-transmitter combination with only
    one fast sign uses that sign; conflicting, missing and modulator-only cells
    use the explicit sensitivity parameter. This is NOT receptor physiology.
    """
    if ambiguous_sign not in (-1, 1):
        raise ValueError("Ambiguous edges must remain active with sign +1 or -1.")
    signs, uncertain = [], []
    for value in transmitters:
        tokens = set(str(value).lower().split(","))
        fast = ({1} if "acetylcholine" in tokens else set()) | (
            {-1} if tokens & {"gaba", "glutamate", "histamine"} else set()
        )
        ambiguous = len(fast) != 1
        signs.append(ambiguous_sign if ambiguous else next(iter(fast)))
        uncertain.append(ambiguous)
    return np.asarray(signs, dtype=np.int8), np.asarray(uncertain, dtype=bool)
