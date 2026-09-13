"""State allocation for the retained connectome; native integration is separate."""

import numpy as np


class Brain:
    def __init__(self, path, dt=0.1):
        if dt != 0.1:
            raise ValueError("This audited kernel supports only dt=0.1 ms.")
        a = np.load(path)
        for k in [
            "ptr",
            "post",
            "weight",
            "ids",
            "retina",
            "uv",
            "lamina",
            "sugar",
            "superclass",
        ]:
            setattr(self, k, a[k])
        n = len(self.ids)
        for k, dtype in [
            ("ptr", np.int64),
            ("post", np.int32),
            ("weight", np.float32),
            ("ids", np.int64),
            ("retina", np.int32),
            ("lamina", np.int32),
            ("sugar", np.int32),
        ]:
            x = getattr(self, k)
            if x.ndim != 1 or x.dtype != dtype or not x.flags.c_contiguous:
                raise ValueError(f"Invalid native graph array: {k}")
        if (
            n < 1
            or self.ptr.shape != (n + 1,)
            or self.ptr[0] != 0
            or self.ptr[-1] != len(self.post)
            or np.any(np.diff(self.ptr) < 0)
            or len(self.weight) != len(self.post)
        ):
            raise ValueError("Invalid CSR graph")
        if not np.isfinite(self.weight).all():
            raise ValueError("Nonfinite synaptic weight")
        for x in [self.post, self.retina, self.lamina, self.sugar]:
            if np.any(x < 0) or np.any(x >= n):
                raise ValueError("Graph index out of bounds")
        if (
            self.uv.shape != (len(self.retina), 2)
            or not np.isfinite(self.uv).all()
            or np.any(self.uv < 0)
            or np.any(self.uv > 1)
        ):
            raise ValueError("Invalid receptor UV coordinates")
        self.dt = dt
        self.n = len(self.ids)
        self.cursor = 0
        self.v = np.full(self.n, -52, dtype=np.float32)
        self.g = np.zeros(self.n, dtype=np.float32)
        self.drive = np.zeros(self.n, dtype=np.float32)
        self.refractory = np.zeros(self.n, dtype=np.int16)
        self.queue = np.zeros((int(round(1.8 / dt)) + 1, self.n), dtype=np.int32)
        self.queue_count = np.zeros(self.queue.shape[0], dtype=np.int32)
        self.counts = np.zeros(self.n, dtype=np.int32)
        self.luminance = np.zeros(len(self.retina), dtype=np.float32)
        self.active = np.zeros(self.n, dtype=np.int32)
        self.active_flag = np.zeros(self.n, dtype=np.uint8)
        initial = np.unique(np.r_[self.retina, self.lamina, self.sugar])
        self.active[: len(initial)] = initial
        self.active_flag[initial] = 1
        self.nactive = np.asarray([len(initial)], dtype=np.int32)
        self.total_spikes = 0
        self.sim_ms = 0


class NativeBrain(Brain):
    def __init__(self, path, dt=0.1):
        super().__init__(path, dt)
        self.previous_drive = np.zeros(self.n, dtype=np.float32)
        self.last = np.full(self.n, -1, dtype=np.int64)
