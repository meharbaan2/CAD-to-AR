import os

import numpy as np
import trimesh
from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.STEPCAFControl import STEPCAFControl_Reader
from OCC.Core.TDF import TDF_Label, TDF_LabelSequence
from OCC.Core.TDocStd import TDocStd_Document
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopLoc import TopLoc_Location
from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool
from pygltflib import GLTF2, Material

UNIT_SCALE_TO_METERS = 0.001  # mm to meters
LINEAR_DEFLECTION = 0.15
ANGULAR_DEFLECTION = 0.25
FORCE_DOUBLE_SIDED = True


def export_step_hierarchy(step_path: str, glb_path: str):
    """Read a STEP assembly with XCAF and export a GLB scene graph."""
    if not os.path.isfile(step_path):
        raise FileNotFoundError(f"STEP file not found: {step_path}")

    doc = TDocStd_Document("cad2ar-step-import")
    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())

    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)

    status = reader.ReadFile(step_path)
    if status != IFSelect_RetDone:
        raise RuntimeError(f"Failed to read STEP: {step_path}")
    if not reader.Transfer(doc):
        raise RuntimeError(f"Failed to transfer STEP to XCAF document: {step_path}")

    roots = TDF_LabelSequence()
    shape_tool.GetFreeShapes(roots)
    if roots.Length() == 0:
        raise RuntimeError("No free shapes found in STEP document.")

    scene = trimesh.Scene()
    geometry_cache = {}
    node_name_counts = {}
    stats = {
        "assemblies": 0,
        "part_instances": 0,
        "part_definitions": set(),
    }

    def make_unique_name(raw_name: str, fallback_prefix: str) -> str:
        base = (raw_name or "").strip() or fallback_prefix
        count = node_name_counts.get(base, 0) + 1
        node_name_counts[base] = count
        return base if count == 1 else f"{base} #{count}"

    def label_name(label: TDF_Label, fallback_prefix: str) -> str:
        return make_unique_name(label.GetLabelName(), fallback_prefix)

    def label_entry(label: TDF_Label) -> str:
        return label.EntryDump()

    def loc_to_matrix4(loc, unit_scale=1.0):
        trsf = loc.Transformation()
        matrix = np.eye(4, dtype=np.float64)

        matrix[0, 0] = trsf.Value(1, 1)
        matrix[0, 1] = trsf.Value(1, 2)
        matrix[0, 2] = trsf.Value(1, 3)
        matrix[1, 0] = trsf.Value(2, 1)
        matrix[1, 1] = trsf.Value(2, 2)
        matrix[1, 2] = trsf.Value(2, 3)
        matrix[2, 0] = trsf.Value(3, 1)
        matrix[2, 1] = trsf.Value(3, 2)
        matrix[2, 2] = trsf.Value(3, 3)

        translation = trsf.TranslationPart()
        matrix[0, 3] = translation.X() * unit_scale
        matrix[1, 3] = translation.Y() * unit_scale
        matrix[2, 3] = translation.Z() * unit_scale
        return matrix

    def mesh_shape_local(shape):
        """Mesh a shape in local coordinates only."""
        shape_no_loc = shape.Located(TopLoc_Location())
        BRepMesh_IncrementalMesh(
            shape_no_loc,
            LINEAR_DEFLECTION,
            False,
            ANGULAR_DEFLECTION,
            True,
        )

        vertices = []
        faces = []

        explorer = TopExp_Explorer(shape_no_loc, TopAbs_FACE)
        while explorer.More():
            face = explorer.Current()
            face_loc = TopLoc_Location()
            triangulation = BRep_Tool.Triangulation(face, face_loc)

            if triangulation is not None:
                face_transform = loc_to_matrix4(face_loc, UNIT_SCALE_TO_METERS)
                node_offset = len(vertices)

                for i in range(1, triangulation.NbNodes() + 1):
                    node = triangulation.Node(i)
                    point = np.array(
                        [
                            node.X() * UNIT_SCALE_TO_METERS,
                            node.Y() * UNIT_SCALE_TO_METERS,
                            node.Z() * UNIT_SCALE_TO_METERS,
                            1.0,
                        ],
                        dtype=np.float64,
                    )
                    vertices.append((face_transform @ point)[:3])

                for i in range(1, triangulation.NbTriangles() + 1):
                    tri = triangulation.Triangle(i)
                    faces.append(
                        [
                            tri.Value(1) - 1 + node_offset,
                            tri.Value(2) - 1 + node_offset,
                            tri.Value(3) - 1 + node_offset,
                        ]
                    )

            explorer.Next()

        if not vertices or not faces:
            return None

        return trimesh.Trimesh(
            vertices=np.asarray(vertices, dtype=np.float64),
            faces=np.asarray(faces, dtype=np.int64),
            process=False,
        )

    def simple_label_mesh(simple_label: TDF_Label):
        cache_key = label_entry(simple_label)
        if cache_key in geometry_cache:
            return geometry_cache[cache_key]

        shape = shape_tool.GetShape(simple_label)
        if shape.IsNull():
            return None

        mesh = mesh_shape_local(shape)
        if mesh is not None and len(mesh.vertices) > 0:
            geometry_cache[cache_key] = mesh
        return mesh

    def shape_local_matrix(label: TDF_Label) -> np.ndarray:
        shape = shape_tool.GetShape(label)
        if shape.IsNull():
            return np.eye(4, dtype=np.float64)
        return loc_to_matrix4(shape.Location(), UNIT_SCALE_TO_METERS)

    def add_empty_node(label: TDF_Label, parent_node_name: str, fallback_prefix: str, transform=None):
        node_name = label_name(label, fallback_prefix)
        scene.graph.update(
            frame_to=node_name,
            frame_from=parent_node_name,
            matrix=np.eye(4, dtype=np.float64) if transform is None else transform,
        )
        return node_name

    def add_part_instance(instance_label: TDF_Label, simple_label: TDF_Label, parent_node_name: str, transform):
        mesh = simple_label_mesh(simple_label)
        if mesh is None:
            return

        stats["part_instances"] += 1
        stats["part_definitions"].add(label_entry(simple_label))

        node_name = label_name(instance_label, "part")
        scene.add_geometry(
            mesh.copy(),
            node_name=node_name,
            geom_name=f"geom_{label_entry(simple_label).replace(':', '_')}",
            parent_node_name=parent_node_name,
            transform=transform,
        )

    def walk_label(label: TDF_Label, parent_node_name: str):
        if shape_tool.IsReference(label):
            referred = TDF_Label()
            if not shape_tool.GetReferredShape(label, referred):
                return

            instance_transform = loc_to_matrix4(
                shape_tool.GetLocation(label), UNIT_SCALE_TO_METERS
            )

            if shape_tool.IsAssembly(referred):
                stats["assemblies"] += 1
                assembly_node = add_empty_node(
                    label,
                    parent_node_name,
                    "assembly",
                    transform=instance_transform,
                )
                children = TDF_LabelSequence()
                if shape_tool.GetComponents(referred, children):
                    for i in range(1, children.Length() + 1):
                        walk_label(children.Value(i), assembly_node)
                return

            if shape_tool.IsSimpleShape(referred):
                part_transform = instance_transform @ shape_local_matrix(referred)
                add_part_instance(label, referred, parent_node_name, part_transform)
                return

        if shape_tool.IsAssembly(label):
            stats["assemblies"] += 1
            assembly_transform = loc_to_matrix4(
                shape_tool.GetLocation(label), UNIT_SCALE_TO_METERS
            ) @ shape_local_matrix(label)
            assembly_node = add_empty_node(
                label,
                parent_node_name,
                "assembly",
                transform=assembly_transform,
            )
            children = TDF_LabelSequence()
            if shape_tool.GetComponents(label, children):
                for i in range(1, children.Length() + 1):
                    walk_label(children.Value(i), assembly_node)
            return

        if shape_tool.IsSimpleShape(label):
            part_transform = loc_to_matrix4(
                shape_tool.GetLocation(label), UNIT_SCALE_TO_METERS
            ) @ shape_local_matrix(label)
            add_part_instance(label, label, parent_node_name, part_transform)

    print("Processing STEP assembly hierarchy...")
    for i in range(1, roots.Length() + 1):
        walk_label(roots.Value(i), "world")

    print(
        f"Assemblies: {stats['assemblies']}, "
        f"part instances: {stats['part_instances']}, "
        f"unique part definitions: {len(stats['part_definitions'])}"
    )

    if stats["part_instances"] == 0:
        raise RuntimeError("No part meshes were exported.")

    scene.export(glb_path)

    if FORCE_DOUBLE_SIDED:
        force_gltf_double_sided(glb_path)

    return glb_path


def force_gltf_double_sided(glb_path: str):
    gltf = GLTF2().load(glb_path)
    if gltf.materials is None:
        gltf.materials = []
    if len(gltf.materials) == 0:
        gltf.materials.append(Material(name="default"))
    for mat in gltf.materials:
        mat.doubleSided = True
    if gltf.meshes:
        for mesh in gltf.meshes:
            if not mesh.primitives:
                continue
            for prim in mesh.primitives:
                if prim.material is None:
                    prim.material = 0
    gltf.save(glb_path)


def main():
    input_dir = os.getcwd()
    step_files = [f for f in os.listdir(input_dir) if f.lower().endswith((".stp", ".step"))]

    if not step_files:
        print("No STEP files found.")
        return

    for step_file in step_files:
        step_path = os.path.join(input_dir, step_file)
        glb_file = os.path.splitext(step_file)[0] + "_fixed.glb"
        glb_path = os.path.join(input_dir, glb_file)

        print(f"\n{'=' * 60}")
        print(f"Converting: {step_file}")
        print(f"{'=' * 60}")

        try:
            export_step_hierarchy(step_path, glb_path)
            print(f"\nExported: {glb_file}")
        except Exception as exc:
            print(f"\nFailed: {exc}")
            import traceback

            traceback.print_exc()


if __name__ == "__main__":
    main()
