from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import trimesh
import numpy as np
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.TopAbs import TopAbs_SHELL, TopAbs_SOLID
from OCC.Core.TopExp import TopExp_Explorer

from .reverse import glb_to_faceted_step, glb_to_reconstructed_step


@dataclass(frozen=True)
class ReadBackReport:
    ok: bool
    roots: int
    shapes: int
    shells: int
    solids: int


@dataclass(frozen=True)
class SelfTestCase:
    source: Path
    mode: str
    output: Path
    conversion_ok: bool
    readback: ReadBackReport
    free_edges: int
    solids: int
    fallback: bool

    @property
    def ok(self) -> bool:
        if not self.conversion_ok or not self.readback.ok:
            return False
        if self.mode == "advanced":
            return self.free_edges <= 20 or self.fallback
        return True


@dataclass(frozen=True)
class ReconstructionAssessment:
    source: Path
    mode: str
    output: Path
    output_size: int
    report: object
    readback: ReadBackReport

    @property
    def ok(self) -> bool:
        return self.report.ok and self.readback.ok

    @property
    def size_mb(self) -> float:
        return self.output_size / (1024 * 1024)


@dataclass(frozen=True)
class PrimitiveSelfTestCase:
    name: str
    output: Path
    report: object
    readback: ReadBackReport
    expected_analytic: str

    @property
    def ok(self) -> bool:
        if not self.report.ok or not self.readback.ok:
            return False
        if self.expected_analytic == "sphere":
            return self.report.analytic_sphere_count >= 1 and self.readback.solids >= 1
        if self.expected_analytic == "cone":
            return self.report.analytic_cone_count >= 1 and self.readback.solids >= 1
        if self.expected_analytic == "hole":
            return self.report.clean_hole_candidate_count >= 1 and self.report.holed_planar_region_count >= 1
        return False


def _count_subshapes(shape, kind) -> int:
    explorer = TopExp_Explorer()
    explorer.Init(shape, kind)
    count = 0
    while explorer.More():
        count += 1
        explorer.Next()
    return count


def read_step_back(path: Path) -> ReadBackReport:
    reader = STEPControl_Reader()
    status = reader.ReadFile(str(path))
    if status != IFSelect_RetDone:
        return ReadBackReport(False, reader.NbRootsForTransfer(), 0, 0, 0)

    reader.TransferRoots()
    shapes = reader.NbShapes()
    if shapes < 1:
        return ReadBackReport(False, reader.NbRootsForTransfer(), shapes, 0, 0)

    shape = reader.Shape(1)
    shells = _count_subshapes(shape, TopAbs_SHELL)
    solids = _count_subshapes(shape, TopAbs_SOLID)
    return ReadBackReport(True, reader.NbRootsForTransfer(), shapes, shells, solids)


def _default_glbs(directory: Path) -> list[Path]:
    preferred = [
        directory / "python half_fixed.glb",
        directory / "transormer assembled_fixed.glb",
    ]
    found = [path for path in preferred if path.is_file()]
    if found:
        return found
    return sorted(directory.glob("*.glb"))


def _resolve_inputs(inputs: list[str] | None, directory: Path) -> list[Path]:
    if not inputs:
        return _default_glbs(directory)

    paths: list[Path] = []
    for raw_input in inputs:
        path = Path(raw_input)
        if path.is_dir():
            paths.extend(sorted(item for item in path.iterdir() if item.suffix.lower() in {".glb", ".gltf"}))
        else:
            paths.append(path)
    return paths


def _convert(source: Path, mode: str, output: Path):
    if mode == "faceted":
        return glb_to_faceted_step(source, output)
    if mode == "reconstructed":
        return glb_to_reconstructed_step(source, output, mode_name="reconstructed")
    if mode == "advanced":
        return glb_to_reconstructed_step(
            source,
            output,
            reconstruct_cylinders=True,
            reconstruct_spheres=True,
            reconstruct_cones=True,
            make_solids=True,
            mode_name="advanced",
        )
    raise ValueError(f"Unknown self-test mode: {mode}")


def run_selftest(
    inputs: list[str] | None = None,
    *,
    directory=".",
    modes: tuple[str, ...] = ("advanced", "reconstructed"),
    keep_outputs: bool = False,
    output_dir: str | None = None,
) -> list[SelfTestCase]:
    root = Path(directory).resolve()
    sources = _resolve_inputs(inputs, root)
    if not sources:
        raise FileNotFoundError(f"No GLB/GLTF files found for self-test in {root}")

    for source in sources:
        if not source.is_file():
            raise FileNotFoundError(source)

    cases: list[SelfTestCase] = []

    def run_into(target_dir: Path):
        for source in sources:
            for mode in modes:
                output = target_dir / f"{source.stem}_{mode}_selftest.step"
                report = _convert(source, mode, output)
                readback = read_step_back(output)
                cases.append(
                    SelfTestCase(
                        source=source,
                        mode=mode,
                        output=output,
                        conversion_ok=report.ok,
                        readback=readback,
                        free_edges=report.sewn_free_edge_count,
                        solids=report.topology_solid_count,
                        fallback=report.analytic_cylinder_fallback,
                    )
                )

    if keep_outputs:
        target_dir = Path(output_dir).resolve() if output_dir else root / "selftest_outputs"
        target_dir.mkdir(parents=True, exist_ok=True)
        run_into(target_dir)
    else:
        with TemporaryDirectory(prefix="cadconverter_selftest_") as temp_dir:
            run_into(Path(temp_dir))

    return cases


def run_reconstruction_assessment(
    inputs: list[str] | None = None,
    *,
    directory=".",
    modes: tuple[str, ...] = ("faceted", "reconstructed", "advanced"),
    keep_outputs: bool = False,
    output_dir: str | None = None,
) -> list[ReconstructionAssessment]:
    root = Path(directory).resolve()
    sources = _resolve_inputs(inputs, root)
    if not sources:
        raise FileNotFoundError(f"No GLB/GLTF files found for reconstruction assessment in {root}")

    for source in sources:
        if not source.is_file():
            raise FileNotFoundError(source)

    cases: list[ReconstructionAssessment] = []

    def run_into(target_dir: Path):
        for source in sources:
            for mode in modes:
                output = target_dir / f"{source.stem}_{mode}_assessment.step"
                report = _convert(source, mode, output)
                readback = read_step_back(output)
                output_size = output.stat().st_size if output.is_file() else 0
                cases.append(
                    ReconstructionAssessment(
                        source=source,
                        mode=mode,
                        output=output,
                        output_size=output_size,
                        report=report,
                        readback=readback,
                    )
                )

    if keep_outputs:
        target_dir = Path(output_dir).resolve() if output_dir else root / "reconstruction_assessment"
        target_dir.mkdir(parents=True, exist_ok=True)
        run_into(target_dir)
    else:
        with TemporaryDirectory(prefix="cadconverter_assessment_") as temp_dir:
            run_into(Path(temp_dir))

    return cases


def _write_primitive_glbs(target_dir: Path) -> list[tuple[str, Path, str]]:
    holed_plane = trimesh.Trimesh(
        vertices=np.asarray(
            [
                [-1.0, -1.0, 0.0],
                [1.0, -1.0, 0.0],
                [1.0, 1.0, 0.0],
                [-1.0, 1.0, 0.0],
                [-0.35, -0.35, 0.0],
                [0.35, -0.35, 0.0],
                [0.35, 0.35, 0.0],
                [-0.35, 0.35, 0.0],
            ],
            dtype=np.float64,
        ),
        faces=np.asarray(
            [
                [0, 1, 5],
                [0, 5, 4],
                [1, 2, 6],
                [1, 6, 5],
                [2, 3, 7],
                [2, 7, 6],
                [3, 0, 4],
                [3, 4, 7],
            ],
            dtype=np.int64,
        ),
        process=False,
    )

    primitives = [
        ("sphere", trimesh.creation.icosphere(subdivisions=3, radius=1.0), "sphere"),
        ("cone", trimesh.creation.cone(radius=1.0, height=2.0, sections=64), "cone"),
        (
            "frustum",
            trimesh.creation.cone(radius=1.0, radius_top=0.4, height=2.0, sections=64),
            "cone",
        ),
        ("holed_plane", holed_plane, "hole"),
    ]

    outputs = []
    for name, mesh, expected in primitives:
        path = target_dir / f"{name}.glb"
        mesh.export(path)
        outputs.append((name, path, expected))
    return outputs


def run_primitive_selftest(
    *,
    keep_outputs: bool = False,
    output_dir: str | None = None,
) -> list[PrimitiveSelfTestCase]:
    cases: list[PrimitiveSelfTestCase] = []

    def run_into(target_dir: Path):
        for name, source, expected in _write_primitive_glbs(target_dir):
            output = target_dir / f"{name}_advanced.step"
            report = glb_to_reconstructed_step(
                source,
                output,
                reconstruct_holes=expected == "hole",
                reconstruct_spheres=True,
                reconstruct_cones=True,
                make_solids=True,
                mode_name="advanced",
            )
            cases.append(
                PrimitiveSelfTestCase(
                    name=name,
                    output=output,
                    report=report,
                    readback=read_step_back(output),
                    expected_analytic=expected,
                )
            )

    if keep_outputs:
        target_dir = Path(output_dir).resolve() if output_dir else Path("primitive_selftest_outputs").resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        run_into(target_dir)
    else:
        with TemporaryDirectory(prefix="cadconverter_primitives_") as temp_dir:
            run_into(Path(temp_dir))

    return cases
