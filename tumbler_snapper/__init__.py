"""tumbler-snapper: lossless SID -> universal accumulator/table tracker decompiler."""

from __future__ import annotations

from . import residual, sidreg
from .residual import Residual, apply, diff

__version__ = "0.0.1"
__all__ = ["sidreg", "residual", "Residual", "diff", "apply"]
