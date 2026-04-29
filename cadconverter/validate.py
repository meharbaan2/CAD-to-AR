from dataclasses import dataclass
from pathlib import Path

from pygltflib import GLTF2


@dataclass(frozen=True)
class ValidationReport:
    path: Path
    node_count: int
    mesh_count: int
    material_count: int
    primitive_count: int
    colored_primitive_count: int
    normal_primitive_count: int
    named_node_count: int

    @property
    def has_colors(self) -> bool:
        return self.primitive_count > 0 and self.colored_primitive_count == self.primitive_count

    @property
    def has_normals(self) -> bool:
        return self.primitive_count > 0 and self.normal_primitive_count == self.primitive_count

    @property
    def has_materials(self) -> bool:
        return self.material_count > 0

    @property
    def ok(self) -> bool:
        return self.primitive_count > 0 and self.has_colors and self.has_normals and self.has_materials


def validate_glb(path) -> ValidationReport:
    glb_path = Path(path)
    gltf = GLTF2().load(str(glb_path))

    primitives = [
        primitive
        for mesh in (gltf.meshes or [])
        for primitive in (mesh.primitives or [])
    ]

    return ValidationReport(
        path=glb_path,
        node_count=len(gltf.nodes or []),
        mesh_count=len(gltf.meshes or []),
        material_count=len(gltf.materials or []),
        primitive_count=len(primitives),
        colored_primitive_count=sum(
            1 for primitive in primitives if getattr(primitive.attributes, "COLOR_0", None) is not None
        ),
        normal_primitive_count=sum(
            1 for primitive in primitives if getattr(primitive.attributes, "NORMAL", None) is not None
        ),
        named_node_count=sum(1 for node in (gltf.nodes or []) if bool(node.name)),
    )
