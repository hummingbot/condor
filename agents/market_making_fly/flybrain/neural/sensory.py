"""Display brightness proxy; not a calibrated compound-eye model."""

import numpy as np


def retinal_samples(frame, uv):
    frame = np.asarray(frame)
    if frame.ndim != 3 or frame.shape[2] != 3 or frame.dtype != np.uint8:
        raise ValueError("RGB uint8 frame required")
    h, w = frame.shape[:2]
    x = np.clip((uv[:, 0] * (w - 1)).astype(int), 0, w - 1)
    y = np.clip((uv[:, 1] * (h - 1)).astype(int), 0, h - 1)
    rgb = frame[y, x].astype(np.float32) / 255
    linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    return (linear @ np.asarray([0.2126, 0.7152, 0.0722], dtype=np.float32)).astype(
        np.float32
    )
