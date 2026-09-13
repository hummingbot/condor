"""Baseline-centered anti-Hebbian eligibility model.

Adaptation of Huang, Luo et al. 2024, Appendix equations 3.2--3.5.
The original reduced rate model is NOT substituted for the connectome. Actual
full-network spike counts supply the rates. u/w represent deviations from each
reconstructed edge's initial efficacy. Bounds and trace constants are declared
model choices; applying the rule to this male reconstruction is a hypothesis.
"""

import math

import numpy as np

PARAMETERS = {
    "trace_kc_seconds": 1.0,
    "trace_dan_seconds": 1.0,
    "memory_decay_seconds": 1800.0,
    "weight_filter_seconds": 0.05,
    "minimum_fraction": 0.1,
    "maximum_fraction": 2.0,
    "maximum_rate_bin_ms": 10.0,
    "source": "https://doi.org/10.1038/s41586-024-07819-w",
    "interpretation": "Centered rate-rule adaptation on existing individual edges, not an exact reproduction of the published nine-unit model. Equal trace constants, efficacy bounds, gain and 50 ms weight filter require calibration.",
}


def advance(
    y_kc, y_dan, u, w, kc_hz, dan_hz, gain, dt_seconds, eta, learning=True, frozen=False
):
    """Mutate traces and efficacy deviations with constant rates for one bin.

    Traces and the passive two-state memory system use closed-form solutions.
    The nonlinear rate/trace products use midpoint traces. No game variables enter.
    """
    if not math.isfinite(dt_seconds) or dt_seconds <= 0 or dt_seconds > 0.0100001:
        raise ValueError("Rate bins must be 0--10 ms")
    h = dt_seconds
    p = PARAMETERS
    ak = math.exp(-h / p["trace_kc_seconds"])
    ad = math.exp(-h / p["trace_dan_seconds"])
    kmid = y_kc * math.sqrt(ak) + kc_hz * (1 - math.sqrt(ak))
    dmid = y_dan * math.sqrt(ad) + dan_hz * (1 - math.sqrt(ad))
    y_kc[:] = y_kc * ak + kc_hz * (1 - ak)
    y_dan[:] = y_dan * ad + dan_hz * (1 - ad)
    if frozen:
        return
    drive = (
        eta * (kc_hz * (gain.T @ dmid) - (gain.T @ dan_hz) * kmid)
        if learning
        else np.zeros_like(u)
    )
    tu = p["memory_decay_seconds"]
    tw = p["weight_filter_seconds"]
    eu = math.exp(-h / tu)
    ew = math.exp(-h / tw)
    c = tu / (tu - tw) * (eu - ew)
    old_u = u.copy()
    u[:] = old_u * eu + drive * tu * (-math.expm1(-h / tu))
    w[:] = w * ew + old_u * c + drive * tu * (-math.expm1(-h / tw) - c)
    # Bounds preserve the reconstructed excitatory sign. Saturation is reported,
    # not hidden or reset when an assay has an unfavorable outcome.
    lo = p["minimum_fraction"] - 1
    hi = p["maximum_fraction"] - 1
    np.clip(u, lo, hi, out=u)
    np.clip(w, lo, hi, out=w)
