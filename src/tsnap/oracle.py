"""Sidtrace register-grid oracle via ``docker cp`` (namespace-independent).

Stock ``pysidtracker.run_sidtrace`` bind-mounts the tune, which fails when the
daemon runs in a different mount namespace than the caller. ``docker cp`` streams
over the docker API instead, reusing pysidtracker's pure trace/grid parsers.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pysidtracker.oracle import (  # pylint: disable=no-name-in-module
    SIDTRACE_IMAGE,
    SidtraceUnavailable,
    read_sidtrace,
    sidtrace_grid,
)

_TRACE_NAME = "trace.csv.zst"

VOLUME_REG = 0x18
DRIVER_VOLUME = 0x0F


def change_stream(writes, reg_count=32):
    """Ordered ``(reg, val)`` register-*changing* writes from a cold SID start.

    Mirrors sidtrace's log: the SID powers up at zero with the PSID driver
    pre-seeding ``$D418=$0F``, so a write equal to the current value is elided.
    """
    st = [0] * reg_count
    if VOLUME_REG < reg_count:
        st[VOLUME_REG] = DRIVER_VOLUME
    out = []
    for r, v in writes:
        if 0 <= r < reg_count and st[r] != v:
            st[r] = v
            out.append((r, v))
    return out


def sidtrace_change_stream(csv_path, *, chip=0, reg_count=32):
    """sidtrace's register-change stream, minus its leading PSID-driver write."""
    rows = read_sidtrace(Path(csv_path))
    out = [(row.reg, row.value) for row in rows if row.chip == chip and 0 <= row.reg < reg_count]
    if out and out[0] == (VOLUME_REG, DRIVER_VOLUME):
        out = out[1:]
    return out


_DOCKER_TIMEOUT = 180


def _docker(args, docker="docker", timeout=_DOCKER_TIMEOUT):
    try:
        return subprocess.run(
            [docker, *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise SidtraceUnavailable(f"{docker} not found: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise SidtraceUnavailable(f"docker {args[0]} timed out after {timeout}s") from exc
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.decode("utf-8", "replace") if exc.stderr else ""
        raise SidtraceUnavailable(f"docker {args[0]} failed: {err.strip()}") from exc


def render_sidtrace(tune_path, out_path, *, seconds=60, image=SIDTRACE_IMAGE, docker="docker"):
    """Render ``tune_path`` under the sidtrace container to ``out_path`` via ``docker cp``.

    A throwaway container carries an anonymous ``/work`` volume; the tune is
    copied in, the render runs, and the ``.csv.zst`` is copied out (no bind
    mount, so the daemon's filesystem namespace is irrelevant).
    """
    tune_path, out_path = Path(tune_path), Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    created = _docker(
        [
            "create",
            "-w",
            "/work",
            "--entrypoint",
            "sidtrace",
            image,
            _TRACE_NAME,
            tune_path.name,
            f"-t{seconds}",
        ],
        docker=docker,
    )
    cid = created.stdout.decode().strip()
    try:
        _docker(["cp", str(tune_path), f"{cid}:/work/{tune_path.name}"], docker=docker)
        _docker(["start", "-a", cid], docker=docker)
        _docker(["cp", f"{cid}:/work/{_TRACE_NAME}", str(out_path)], docker=docker)
    finally:
        _docker(["rm", "-f", cid], docker=docker)
    if not out_path.exists():
        raise SidtraceUnavailable(f"sidtrace produced no output for {tune_path.name}")
    return out_path


def oracle_grid(
    tune_path,
    *,
    oracle_cache,
    frames=None,
    cycles_per_frame=None,
    seconds=60,
    image=SIDTRACE_IMAGE,
    chip=0,
    reg_count=25,
    force=False,
):
    """Per-frame reference register grid for ``tune_path`` from the sidtrace oracle.

    Drop-in for ``pysidtracker.oracle_grid`` using the ``docker cp`` renderer;
    the compressed trace is cached at ``oracle_cache/<stem>.csv.zst``.
    """
    tune_path, oracle_cache = Path(tune_path), Path(oracle_cache)
    csv_path = oracle_cache / f"{tune_path.stem}.csv.zst"
    if force or not csv_path.exists():
        render_sidtrace(tune_path, csv_path, seconds=seconds, image=image)
    grid = sidtrace_grid(
        read_sidtrace(csv_path),
        chip=chip,
        reg_count=reg_count,
        cycles_per_frame=cycles_per_frame,
    )
    return grid[:frames] if frames else grid
