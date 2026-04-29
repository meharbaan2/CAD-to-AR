import os
import json

import numpy as np
import trimesh
from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCC.Core.STEPCAFControl import STEPCAFControl_Reader
from OCC.Core.TDF import TDF_Label, TDF_LabelSequence
from OCC.Core.TDocStd import TDocStd_Document
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopLoc import TopLoc_Location
from OCC.Core.XCAFDoc import (
    XCAFDoc_ColorCurv,
    XCAFDoc_ColorGen,
    XCAFDoc_ColorSurf,
    XCAFDoc_ColorTool,
    XCAFDoc_DocumentTool,
)
from pygltflib import GLTF2, Material, PbrMetallicRoughness

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
    color_tool = XCAFDoc_DocumentTool.ColorTool(doc.Main())
    vis_material_tool = XCAFDoc_DocumentTool.VisMaterialTool(doc.Main())
    material_tool = XCAFDoc_DocumentTool.MaterialTool(doc.Main())

    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    reader.SetColorMode(True)
    reader.SetMatMode(True)

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
    node_material_specs = {}
    node_name_counts = {}
    stats = {
        "assemblies": 0,
        "colored_part_instances": 0,
        "material_overrides": 0,
        "part_instances": 0,
        "part_definitions": set(),
    }
    color_types = (XCAFDoc_ColorSurf, XCAFDoc_ColorGen, XCAFDoc_ColorCurv)

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

        mesh = trimesh.Trimesh(
            vertices=np.asarray(vertices, dtype=np.float64),
            faces=np.asarray(faces, dtype=np.int64),
            process=False,
        )
        mesh.fix_normals()
        _ = mesh.vertex_normals
        return mesh

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

    def quantity_color_to_rgba(color: Quantity_Color):
        return np.array(
            [
                int(round(np.clip(color.Red(), 0.0, 1.0) * 255)),
                int(round(np.clip(color.Green(), 0.0, 1.0) * 255)),
                int(round(np.clip(color.Blue(), 0.0, 1.0) * 255)),
                255,
            ],
            dtype=np.uint8,
        )

    def rgba_to_factor(rgba):
        if rgba is None:
            return [1.0, 1.0, 1.0, 1.0]
        return [
            float(np.clip(rgba[0] / 255.0, 0.0, 1.0)),
            float(np.clip(rgba[1] / 255.0, 0.0, 1.0)),
            float(np.clip(rgba[2] / 255.0, 0.0, 1.0)),
            float(np.clip(rgba[3] / 255.0, 0.0, 1.0)),
        ]

    def quantity_rgba_to_array(color_rgba):
        rgb = color_rgba.GetRGB()
        return np.array(
            [
                int(round(np.clip(rgb.Red(), 0.0, 1.0) * 255)),
                int(round(np.clip(rgb.Green(), 0.0, 1.0) * 255)),
                int(round(np.clip(rgb.Blue(), 0.0, 1.0) * 255)),
                int(round(np.clip(color_rgba.Alpha(), 0.0, 1.0) * 255)),
            ],
            dtype=np.uint8,
        )

    def color_from_shape(shape):
        if shape.IsNull():
            return None

        color = Quantity_Color(0.5, 0.5, 0.5, Quantity_TOC_RGB)
        for color_type in color_types:
            if color_tool.GetInstanceColor(shape, color_type, color):
                return quantity_color_to_rgba(color)
        for color_type in color_types:
            if color_tool.GetColor(shape, color_type, color):
                return quantity_color_to_rgba(color)
        return None

    def color_from_label(label: TDF_Label):
        color = Quantity_Color(0.5, 0.5, 0.5, Quantity_TOC_RGB)
        for color_type in color_types:
            if XCAFDoc_ColorTool.GetColor(label, color_type, color):
                return quantity_color_to_rgba(color)
        return None

    def resolve_part_color(instance_label: TDF_Label, simple_label: TDF_Label):
        for candidate in (
            color_from_shape(shape_tool.GetShape(instance_label)),
            color_from_label(instance_label),
            color_from_shape(shape_tool.GetShape(simple_label)),
            color_from_label(simple_label),
        ):
            if candidate is not None:
                return candidate
        return None

    def get_vis_material(shape_or_label):
        try:
            vis_material = vis_material_tool.GetShapeMaterial(shape_or_label)
            if vis_material and not vis_material.IsNull():
                return vis_material
        except Exception:
            return None
        return None

    def parse_vis_material_json(vis_material):
        try:
            raw = vis_material.DumpJson()
            if not raw:
                return None
            return json.loads(raw)
        except Exception:
            return None

    def physical_material_density(shape):
        try:
            return float(material_tool.GetDensityForShape(shape))
        except Exception:
            return 0.0

    def material_profile_from_metadata(vis_material, rgba, part_name, shape):
        rgba_factor = rgba_to_factor(rgba)
        material_color_factor = [1.0, 1.0, 1.0, rgba_factor[3]] if rgba is not None else rgba_factor
        profile = None

        if vis_material is not None:
            profile = {
                "source": "vis_material",
                "label": str(vis_material.RawName() or "").strip() or "Visual Material",
                "baseColorFactor": material_color_factor if rgba is not None else rgba_to_factor(quantity_rgba_to_array(vis_material.BaseColor())),
                "metallicFactor": 0.2,
                "roughnessFactor": 0.6,
                "doubleSided": bool(FORCE_DOUBLE_SIDED or vis_material.IsDoubleSided()),
                "alphaMode": "BLEND" if rgba_factor[3] < 0.999 else "OPAQUE",
            }

            vis_json = parse_vis_material_json(vis_material)
            if isinstance(vis_json, dict):
                text = json.dumps(vis_json).lower()
                if "metal" in text:
                    profile["metallicFactor"] = 0.85
                    profile["roughnessFactor"] = 0.35
                if "glass" in text or "transparent" in text:
                    profile["metallicFactor"] = 0.0
                    profile["roughnessFactor"] = 0.08
                    profile["alphaMode"] = "BLEND"
                    profile["baseColorFactor"][3] = min(profile["baseColorFactor"][3], 0.45)
                if "plastic" in text or "rubber" in text or "ceramic" in text:
                    profile["metallicFactor"] = 0.0
                    profile["roughnessFactor"] = 0.82
            return profile

        density = physical_material_density(shape)
        name = (part_name or "").lower()

        if any(token in name for token in ("winding", "coil", "copper", "cu")):
            return {
                "source": "heuristic",
                "label": "Copper",
                "baseColorFactor": material_color_factor if rgba is not None else [0.82, 0.42, 0.18, 1.0],
                "metallicFactor": 0.9,
                "roughnessFactor": 0.32,
                "doubleSided": FORCE_DOUBLE_SIDED,
                "alphaMode": "OPAQUE",
            }

        if any(token in name for token in ("insulator", "ceramic", "plastic", "rubber", "bushing")):
            return {
                "source": "heuristic",
                "label": "Insulator",
                "baseColorFactor": material_color_factor if rgba is not None else [0.95, 0.95, 0.95, 1.0],
                "metallicFactor": 0.02,
                "roughnessFactor": 0.88,
                "doubleSided": FORCE_DOUBLE_SIDED,
                "alphaMode": "OPAQUE",
            }

        if any(token in name for token in ("core", "steel", "iron", "lamination")) or density > 6.5:
            return {
                "source": "heuristic",
                "label": "Steel Core",
                "baseColorFactor": material_color_factor if rgba is not None else [0.2, 0.22, 0.24, 1.0],
                "metallicFactor": 0.72,
                "roughnessFactor": 0.74,
                "doubleSided": FORCE_DOUBLE_SIDED,
                "alphaMode": "OPAQUE",
            }

        if any(token in name for token in ("cover", "housing", "shell", "case", "enclosure")):
            return {
                "source": "heuristic",
                "label": "Coated Cover",
                "baseColorFactor": material_color_factor if rgba is not None else [0.35, 0.38, 0.41, 1.0],
                "metallicFactor": 0.18,
                "roughnessFactor": 0.62,
                "doubleSided": FORCE_DOUBLE_SIDED,
                "alphaMode": "OPAQUE",
            }

        if rgba is not None:
            brightness = float(np.mean(rgba[:3])) / 255.0
            if brightness < 0.25:
                return {
                    "source": "heuristic",
                    "label": "Dark Technical Finish",
                    "baseColorFactor": material_color_factor,
                    "metallicFactor": 0.35,
                    "roughnessFactor": 0.68,
                    "doubleSided": FORCE_DOUBLE_SIDED,
                    "alphaMode": "OPAQUE",
                }
            if brightness > 0.85:
                return {
                    "source": "heuristic",
                    "label": "Light Matte Finish",
                    "baseColorFactor": material_color_factor,
                    "metallicFactor": 0.04,
                    "roughnessFactor": 0.86,
                    "doubleSided": FORCE_DOUBLE_SIDED,
                    "alphaMode": "OPAQUE",
                }

        return {
            "source": "heuristic",
            "label": "Generic Technical Finish",
            "baseColorFactor": material_color_factor,
            "metallicFactor": 0.12,
            "roughnessFactor": 0.66,
            "doubleSided": FORCE_DOUBLE_SIDED,
            "alphaMode": "OPAQUE",
        }

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
        mesh_copy = mesh.copy()
        rgba = resolve_part_color(instance_label, simple_label)
        vis_material = (
            get_vis_material(instance_label)
            or get_vis_material(shape_tool.GetShape(instance_label))
            or get_vis_material(simple_label)
            or get_vis_material(shape_tool.GetShape(simple_label))
        )
        if rgba is not None and len(mesh_copy.faces) > 0:
            mesh_copy.visual.face_colors = np.tile(rgba, (len(mesh_copy.faces), 1))
            stats["colored_part_instances"] += 1
        mesh_copy.fix_normals()
        _ = mesh_copy.vertex_normals
        node_material_specs[node_name] = material_profile_from_metadata(
            vis_material=vis_material,
            rgba=rgba,
            part_name=node_name,
            shape=shape_tool.GetShape(simple_label),
        )
        stats["material_overrides"] += 1

        scene.add_geometry(
            mesh_copy,
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
        f"colored part instances: {stats['colored_part_instances']}, "
        f"material overrides: {stats['material_overrides']}, "
        f"part instances: {stats['part_instances']}, "
        f"unique part definitions: {len(stats['part_definitions'])}"
    )

    if stats["part_instances"] == 0:
        raise RuntimeError("No part meshes were exported.")

    scene.export(glb_path)

    enrich_gltf_materials(glb_path, node_material_specs, FORCE_DOUBLE_SIDED)

    return glb_path


def enrich_gltf_materials(glb_path: str, node_material_specs: dict, force_double_sided: bool):
    gltf = GLTF2().load(glb_path)
    if gltf.materials is None:
        gltf.materials = []

    material_indices = {}

    def material_key(spec):
        return (
            spec.get("label", ""),
            round(float(spec.get("metallicFactor", 0.0)), 4),
            round(float(spec.get("roughnessFactor", 1.0)), 4),
            tuple(round(float(v), 4) for v in spec.get("baseColorFactor", [1.0, 1.0, 1.0, 1.0])),
            spec.get("alphaMode", "OPAQUE"),
            bool(spec.get("doubleSided", force_double_sided)),
        )

    def ensure_material(spec):
        key = material_key(spec)
        if key in material_indices:
            return material_indices[key]

        material = Material(
            name=spec.get("label", "default"),
            doubleSided=bool(spec.get("doubleSided", force_double_sided)),
            alphaMode=spec.get("alphaMode", "OPAQUE"),
            pbrMetallicRoughness=PbrMetallicRoughness(
                baseColorFactor=spec.get("baseColorFactor", [1.0, 1.0, 1.0, 1.0]),
                metallicFactor=float(spec.get("metallicFactor", 0.0)),
                roughnessFactor=float(spec.get("roughnessFactor", 1.0)),
            ),
        )
        gltf.materials.append(material)
        material_index = len(gltf.materials) - 1
        material_indices[key] = material_index
        return material_index

    if gltf.nodes and gltf.meshes:
        for node in gltf.nodes:
            if node.mesh is None or node.name not in node_material_specs:
                continue
            spec = node_material_specs[node.name]
            material_index = ensure_material(spec)
            mesh = gltf.meshes[node.mesh]
            if not mesh.primitives:
                continue
            for primitive in mesh.primitives:
                primitive.material = material_index

    if len(gltf.materials) == 0:
        gltf.materials.append(
            Material(
                name="default",
                doubleSided=force_double_sided,
                pbrMetallicRoughness=PbrMetallicRoughness(
                    baseColorFactor=[1.0, 1.0, 1.0, 1.0],
                    metallicFactor=0.0,
                    roughnessFactor=0.8,
                ),
            )
        )

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
