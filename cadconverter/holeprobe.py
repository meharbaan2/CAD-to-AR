import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from .reverse import glb_to_reconstructed_step
from .selftest import read_step_back


_MARKER = "CADCONVERTER_HOLE_PROBE_JSON:"
_STAGE_MARKER = "CADCONVERTER_HOLE_PROBE_STAGE:"


@dataclass(frozen=True)
class HoleProbeResult:
    ok: bool
    source: Path
    output: Path
    returncode: int | None
    timed_out: bool
    stdout: str
    stderr: str
    payload: dict
    stages: tuple[str, ...] = ()

    @property
    def crashed(self) -> bool:
        return self.returncode is not None and self.returncode != 0 and not self.payload


def _suppress_windows_crash_dialogs():
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        sem_failcriticalerrors = 0x0001
        sem_nogpfaultbox = 0x0002
        sem_noopenfileerrorbox = 0x8000
        ctypes.windll.kernel32.SetErrorMode(
            sem_failcriticalerrors | sem_nogpfaultbox | sem_noopenfileerrorbox
        )
    except Exception:
        pass


def _stage(name: str) -> None:
    print(_STAGE_MARKER + name, flush=True)


def _child_probe(
    source: Path,
    output: Path,
    mode: str,
    *,
    sew: bool,
    readback_enabled: bool,
    max_hole_loop_points: int | None,
) -> int:
    _suppress_windows_crash_dialogs()
    use_advanced = mode == "advanced"
    _stage("start")
    _stage("convert_start")
    report = glb_to_reconstructed_step(
        source,
        output,
        reconstruct_holes=True,
        max_hole_loop_points=max_hole_loop_points,
        reconstruct_cylinders=use_advanced,
        reconstruct_spheres=use_advanced,
        reconstruct_cones=use_advanced,
        make_solids=use_advanced,
        mode_name=f"{mode}-holes",
        sew=sew,
    )
    _stage("convert_done")
    if readback_enabled:
        _stage("readback_start")
        readback = read_step_back(output)
        _stage("readback_done")
    else:
        readback = None
    payload = {
        "ok": report.ok and (readback.ok if readback else True),
        "source": str(source),
        "output": str(output),
        "mode": report.mode,
        "readOk": readback.ok if readback else None,
        "shells": readback.shells if readback else None,
        "solids": readback.solids if readback else None,
        "faces": report.exported_face_count,
        "planarRegions": report.planar_region_count,
        "holedPlanarRegions": report.holed_planar_region_count,
        "holeCandidates": report.hole_candidate_count,
        "cleanHoleCandidates": report.clean_hole_candidate_count,
        "holeLoops": report.hole_loop_count,
        "freeEdges": report.sewn_free_edge_count,
        "sewingEnabled": report.sewing_enabled,
        "analyticCylinderFallback": report.analytic_cylinder_fallback,
    }
    print(_MARKER + json.dumps(payload, sort_keys=True))
    return 0 if payload["ok"] else 1


def _parse_payload(stdout: str) -> dict:
    for line in reversed(stdout.splitlines()):
        if line.startswith(_MARKER):
            return json.loads(line[len(_MARKER) :])
    return {}


def _parse_stages(stdout: str) -> tuple[str, ...]:
    return tuple(
        line[len(_STAGE_MARKER) :]
        for line in stdout.splitlines()
        if line.startswith(_STAGE_MARKER)
    )


def run_hole_probe(
    source,
    *,
    output=None,
    mode: str = "reconstructed",
    timeout: int = 180,
    keep_output: bool = False,
    sew: bool = True,
    readback: bool = True,
    max_hole_loop_points: int | None = 48,
) -> HoleProbeResult:
    source = Path(source).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if mode not in {"reconstructed", "advanced"}:
        raise ValueError("Hole probe mode must be 'reconstructed' or 'advanced'")

    def run_with_output(output_path: Path) -> HoleProbeResult:
        command = [
            sys.executable,
            "-m",
            "cadconverter.holeprobe",
            "--child",
            str(source),
            str(output_path),
            "--mode",
            mode,
        ]
        if max_hole_loop_points is not None:
            command.extend(["--max-hole-loop-points", str(max_hole_loop_points)])
        if not sew:
            command.append("--no-sew")
        if not readback:
            command.append("--skip-readback")
        try:
            completed = subprocess.run(
                command,
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            payload = _parse_payload(completed.stdout)
            stages = _parse_stages(completed.stdout)
            return HoleProbeResult(
                ok=bool(payload.get("ok")),
                source=source,
                output=output_path,
                returncode=completed.returncode,
                timed_out=False,
                stdout=completed.stdout,
                stderr=completed.stderr,
                payload=payload,
                stages=stages,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            return HoleProbeResult(
                ok=False,
                source=source,
                output=output_path,
                returncode=None,
                timed_out=True,
                stdout=stdout,
                stderr=exc.stderr or "",
                payload={},
                stages=_parse_stages(stdout),
            )

    if output:
        output_path = Path(output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        return run_with_output(output_path)

    if keep_output:
        output_path = source.with_name(f"{source.stem}_{mode}_hole_probe.step")
        return run_with_output(output_path)

    with TemporaryDirectory(prefix="cadconverter_hole_probe_") as temp_dir:
        return run_with_output(Path(temp_dir) / f"{source.stem}_{mode}_hole_probe.step")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="cadconverter.holeprobe")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("source")
    parser.add_argument("output")
    parser.add_argument("--mode", choices=("reconstructed", "advanced"), default="reconstructed")
    parser.add_argument("--max-hole-loop-points", type=int, default=48)
    parser.add_argument("--no-sew", action="store_true")
    parser.add_argument("--skip-readback", action="store_true")
    args = parser.parse_args(argv)

    if not args.child:
        parser.error("holeprobe is an internal helper; use 'cadconverter probe-holes'")

    return _child_probe(
        Path(args.source),
        Path(args.output),
        args.mode,
        sew=not args.no_sew,
        readback_enabled=not args.skip_readback,
        max_hole_loop_points=args.max_hole_loop_points if args.max_hole_loop_points >= 0 else None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
