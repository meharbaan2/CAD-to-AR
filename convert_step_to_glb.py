import os
import numpy as np
import trimesh

from OCC.Core.STEPCAFControl import STEPCAFControl_Reader
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.XCAFApp import XCAFApp_Application
from OCC.Core.TDocStd import TDocStd_Document
from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool

from OCC.Core.TDF import TDF_LabelSequence, TDF_Tool
from OCC.Core.TDataStd import TDataStd_Name

from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.BRep import BRep_Tool
from OCC.Core.TopLoc import TopLoc_Location
from OCC.Core.gp import gp_Pnt
from OCC.Core.TCollection import TCollection_AsciiString, TCollection_ExtendedString

from pygltflib import GLTF2, Material


UNIT_SCALE_TO_METERS = 0.001
LINEAR_DEFLECTION = 0.15
ANGULAR_DEFLECTION = 0.25
FORCE_DOUBLE_SIDED = True


def label_entry(label) -> str:
    try:
        asc = TCollection_AsciiString()
        TDF_Tool.Entry(label, asc)
        return asc.ToCString()
    except Exception:
        return "unknown_entry"


def label_name(label) -> str:
    try:
        ext = TCollection_ExtendedString()
        if TDataStd_Name.Get(label, ext):
            s = ext.ToExtString()
            if s:
                return s
    except Exception:
        pass
    return label_entry(label)


def loc_to_matrix4(loc: TopLoc_Location, unit_scale: float = 1.0) -> np.ndarray:
    trsf = loc.Transformation()
    m = np.eye(4, dtype=np.float64)

    m[0, 0] = trsf.Value(1, 1); m[0, 1] = trsf.Value(1, 2); m[0, 2] = trsf.Value(1, 3)
    m[1, 0] = trsf.Value(2, 1); m[1, 1] = trsf.Value(2, 2); m[1, 2] = trsf.Value(2, 3)
    m[2, 0] = trsf.Value(3, 1); m[2, 1] = trsf.Value(3, 2); m[2, 2] = trsf.Value(3, 3)

    t = trsf.TranslationPart()
    m[0, 3] = t.X() * unit_scale
    m[1, 3] = t.Y() * unit_scale
    m[2, 3] = t.Z() * unit_scale
    return m


def mat_info(m: np.ndarray):
    pos = (float(m[0,3]), float(m[1,3]), float(m[2,3]))
    R = m[:3,:3]
    det = float(np.linalg.det(R))
    return pos, det


def mesh_shape_to_trimesh(shape, linear_defl: float, angular_defl: float, unit_scale: float) -> trimesh.Trimesh:
    BRepMesh_IncrementalMesh(shape, linear_defl, False, angular_defl, True)

    vertices = []
    faces = []

    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = exp.Current()
        loc = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation(face, loc)

        if triangulation:
            trsf = loc.Transformation()
            n_nodes = triangulation.NbNodes()
            n_tris = triangulation.NbTriangles()
            node_offset = len(vertices)

            for i in range(1, n_nodes + 1):
                p = triangulation.Node(i)
                pt = gp_Pnt(p.X(), p.Y(), p.Z())
                pt.Transform(trsf)
                vertices.append([pt.X() * unit_scale, pt.Y() * unit_scale, pt.Z() * unit_scale])

            for i in range(1, n_tris + 1):
                tri = triangulation.Triangle(i)
                faces.append([
                    tri.Value(1) - 1 + node_offset,
                    tri.Value(2) - 1 + node_offset,
                    tri.Value(3) - 1 + node_offset
                ])

        exp.Next()

    if not vertices or not faces:
        raise RuntimeError("No triangulation produced for shape.")

    mesh = trimesh.Trimesh(
        vertices=np.asarray(vertices, dtype=np.float64),
        faces=np.asarray(faces, dtype=np.int64),
        process=False
    )
    mesh.rezero()
    try:
        mesh.fix_normals()
    except Exception:
        pass
    return mesh


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


def export_step_occurrence_shapes(step_path: str, glb_path: str):
    app = XCAFApp_Application.GetApplication()
    doc = TDocStd_Document("MDTV-XCAF")
    app.NewDocument("MDTV-XCAF", doc)

    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    reader.SetColorMode(True)
    reader.SetLayerMode(True)

    status = reader.ReadFile(step_path)
    if status != IFSelect_RetDone:
        raise RuntimeError(f"Failed to read STEP: {step_path}")

    ok = reader.Transfer(doc)
    if not ok:
        raise RuntimeError(f"Failed to transfer STEP into XDE document: {step_path}")

    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())

    roots = TDF_LabelSequence()
    shape_tool.GetFreeShapes(roots)

    # Collect all subshape labels (occurrence-ish) under each root
    all_labels = TDF_LabelSequence()
    for i in range(1, roots.Length() + 1):
        root = roots.Value(i)
        subs = TDF_LabelSequence()
        shape_tool.GetSubShapes(root, subs)
        for j in range(1, subs.Length() + 1):
            all_labels.Append(subs.Value(j))
        all_labels.Append(root)

    scene = trimesh.Scene()
    seen = set()
    mesh_cache = {}  # cache by label entry in this mode

    exported = 0

    for i in range(1, all_labels.Length() + 1):
        lbl = all_labels.Value(i)
        entry = label_entry(lbl)
        if entry in seen:
            continue
        seen.add(entry)

        # Occurrence shape (often already carries correct absolute location!)
        occ_shape = shape_tool.GetShape(lbl)
        if occ_shape.IsNull():
            continue

        occ_loc = occ_shape.Location()
        world = loc_to_matrix4(occ_loc, unit_scale=UNIT_SCALE_TO_METERS)

        # strip so we don't double-apply it during meshing
        base_shape = occ_shape.Located(TopLoc_Location())

        # mesh
        if entry not in mesh_cache:
            try:
                mesh_cache[entry] = mesh_shape_to_trimesh(base_shape, LINEAR_DEFLECTION, ANGULAR_DEFLECTION, UNIT_SCALE_TO_METERS)
            except Exception:
                continue

        name = label_name(lbl)
        node_name = f"occ_{entry}_{name}".replace(" ", "_").replace(":", "_")
        scene.add_geometry(mesh_cache[entry], node_name=node_name, transform=world)
        exported += 1

        # Debug: show whether rotation exists (det should be ~1)
        pos, det = mat_info(world)
        print(f"[OCC] {name} entry={entry} pos=({pos[0]:.6f},{pos[1]:.6f},{pos[2]:.6f}) det(R)={det:.6f}")

    if len(scene.geometry) == 0:
        raise RuntimeError("No geometry added (occurrence-shape export).")

    print(f"\n[INFO] Occurrence shapes exported: {exported}")

    scene.export(glb_path)

    if FORCE_DOUBLE_SIDED:
        force_gltf_double_sided(glb_path)

    return glb_path


def main():
    input_dir = os.getcwd()
    print(f"Scanning for STEP files in: {input_dir}")

    step_files = [f for f in os.listdir(input_dir) if f.lower().endswith((".stp", ".step"))]
    if not step_files:
        print("No STEP files found in this directory.")
        return

    for step_file in step_files:
        step_path = os.path.join(input_dir, step_file)
        glb_file = os.path.splitext(step_file)[0] + ".glb"
        glb_path = os.path.join(input_dir, glb_file)

        print(f"\nConverting (occurrence-shape mode): {step_file} → {glb_file}")
        try:
            export_step_occurrence_shapes(step_path, glb_path)
            print(f"✅ Converted: {step_file} → {glb_file}")
        except Exception as e:
            print(f"❌ Failed: {step_file}\n   Reason: {e}")

    print("\nAll conversions completed.")


if __name__ == "__main__":
    main()
