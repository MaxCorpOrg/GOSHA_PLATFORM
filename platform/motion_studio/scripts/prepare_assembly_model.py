#!/usr/bin/env python3
"""Prepare the owner's GOSHA_30092026 assembly for BOTH viewers.

No CAD reconstruction, mirroring or hardware calibration. Source millimetres,
Z up, front -Y; translation (0, 0, -89) retains the existing preview shaft
coordinates. Materialise STL facet colours are preserved at their boundaries.
Requires numpy, scipy, rtree, trimesh==5.1.1, fast-simplification==0.2.0.
The source and generated private assets are deliberately excluded from git.
"""
import argparse
import base64
import hashlib
import json
import struct
from pathlib import Path

import fast_simplification
import numpy as np
import trimesh
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

SOURCE_SHA = "208bb99daed400921ab14fb5c21c1cbb3f1f798b78f952ca11447c59a709372e"
MODEL_NAME = "gosha-30092026-208bb99d"


def read_source(path):
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA:
        raise ValueError("This component map is only for the approved GOSHA_30092026.stl")
    count = struct.unpack_from("<I", data, 80)[0]
    assert count == 204821 and len(data) == 84 + count * 50
    raw = np.frombuffer(data, dtype=np.dtype([
        ("normal", "<f4", (3,)), ("vertices", "<f4", (3, 3)), ("colour", "<u2")
    ]), offset=84, count=count)
    assert b"COLOR=" in data[:80] and np.all(raw["colour"] < 32768)
    vertices, inv = np.unique(np.round(raw["vertices"].reshape(-1, 3), 4), axis=0, return_inverse=True)
    faces = inv.reshape(-1, 3)
    edges = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    graph = coo_matrix((np.ones(len(edges)), (edges[:, 0], edges[:, 1])), shape=(len(vertices), len(vertices)))
    n, labels = connected_components(graph, directed=False)
    assert n == 20, f"Unexpected component count: {n}"
    return raw, labels[faces[:, 0]]


def classify(triangles):
    lo, hi = triangles.reshape(-1, 3).min(0), triangles.reshape(-1, 3).max(0)
    if len(triangles) == 92598:
        return "body"
    if lo[2] > 99:
        return "head"
    side = "positive_x" if (lo[0] + hi[0]) > 0 else "negative_x"
    if lo[2] < 1 or (len(triangles) == 1270 and hi[2] < 24):
        return "foot_" + side
    if lo[2] < 10 and hi[2] <= 49.01:
        return "leg_" + side
    if len(triangles) == 8776:
        return "hand_" + side
    if len(triangles) in (25002, 13650):
        return "arm_" + side
    if len(triangles) == 606:
        return "button_boot"
    if len(triangles) == 516:
        return "power_switch"
    raise ValueError(f"Unclassified component {len(triangles)} {lo} {hi}")


def colour_rgb(code):
    return np.round(np.array([code & 31, (code >> 5) & 31, (code >> 10) & 31]) * 255 / 31).astype(np.uint8)


def linear_rgb(rgb):
    s = rgb.astype(np.float64) / 255
    return np.where(s <= .04045, s / 12.92, ((s + .055) / 1.055) ** 2.4)


def sample_distance(points, target_triangles):
    # Deterministic surface probes, NOT a certified Hausdorff bound.
    ids = np.linspace(0, len(points) - 1, min(4096, len(points)), dtype=int)
    probes = points[ids]
    mesh = trimesh.Trimesh(vertices=target_triangles.reshape(-1, 3), faces=np.arange(target_triangles.size // 3).reshape(-1, 3), process=False)
    # An R-tree tests triangle bounds, unlike nearest centroids which miss large
    # flat CAD faces and overestimate the error beside a finely tessellated edge.
    _, distance, _ = trimesh.proximity.closest_point(mesh, probes)
    return float(distance.max())


def prepare(raw, labels):
    groups = {}
    component_map = []
    for label in np.unique(labels):
        mask = labels == label
        tris = raw["vertices"][mask].astype(np.float64)
        name = classify(tris)
        component_map.append({"part": name, "triangles": len(tris), "bounds": [tris.reshape(-1, 3).min(0).tolist(), tris.reshape(-1, 3).max(0).tolist()]})
        # Lid and antennas remain fixed; only the six existing limb joints move.
        names = np.full(len(tris), name, dtype="U32")
        if name == "body":
            names[tris.mean(1)[:, 2] >= 100] = "head"
        for part in np.unique(names):
            for code in np.unique(raw["colour"][mask][names == part]):
                patch = tris[(names == part) & (raw["colour"][mask] == code)]
                groups.setdefault(part, []).append((int(code), patch))
    output = []
    errors = []
    for name, patches in sorted(groups.items()):
        pieces = []
        for code, tris in patches:
            # Keep CAD coordinates rather than rounded topology coordinates.
            _, ids, inverse = np.unique(np.round(tris.reshape(-1, 3), 4), axis=0, return_index=True, return_inverse=True)
            source_v, source_f = tris.reshape(-1, 3)[ids], inverse.reshape(-1, 3)
            v, f = source_v, source_f
            err = 0.
            if len(f) > 200:
                for ratio in (.24, .40, .65, 1.):
                    if ratio == 1.:
                        v, f = source_v, source_f
                        err = 0.
                        break
                    v, f = fast_simplification.simplify(source_v, source_f, target_count=max(160, int(len(source_f) * ratio)), agg=4., preserve_border=True)
                    err = max(sample_distance(np.concatenate([source_v, tris.mean(1)]), v[f]), sample_distance(np.concatenate([v, v[f].mean(1)]), tris))
                    if err <= .2:
                        break
            errors.append({"part": name, "colour": code, "before": len(source_f), "after": len(f), "sample_max_mm": round(err, 6)})
            mesh = trimesh.Trimesh(vertices=v, faces=f, process=False)
            mesh = trimesh.graph.smooth_shade(mesh, angle=np.radians(35), facet_minarea=50)
            pieces.append((mesh, colour_rgb(code)))
        positions, normals, colours, indices, offset = [], [], [], [], 0
        for mesh, colour in pieces:
            positions.append(mesh.vertices + [0, 0, -89])
            normals.append(mesh.vertex_normals)
            colours.append(np.tile(colour, (len(mesh.vertices), 1)))
            indices.append(mesh.faces + offset)
            offset += len(mesh.vertices)
        # Both outputs use this exact 0.01 mm quantized geometry.
        output.append({"name": name, "p": np.round(np.concatenate(positions) * 100).astype('<i2'),
                       "n": np.clip(np.round(np.concatenate(normals) * 127), -127, 127).astype('i1'),
                       "c": np.concatenate(colours).astype('u1'),
                       "i": np.concatenate(indices).astype('<u4' if offset > 65535 else '<u2')})
    return output, component_map, errors


def export(meshes, components, errors, out):
    out.mkdir(parents=True, exist_ok=True)
    binary = bytearray()
    views, accessors = [], []
    def accessor(array, kind, target):
        while len(binary) % 4:
            binary.append(0)
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": array.nbytes, "target": target})
        binary.extend(array.tobytes())
        entry = {"bufferView": len(views) - 1, "componentType": {"<f4": 5126, "<u4": 5125, "<u2": 5123}[array.dtype.str], "count": len(array), "type": kind}
        if kind == "VEC3":
            entry.update(min=array.min(0).tolist(), max=array.max(0).tolist())
        accessors.append(entry)
        return len(accessors) - 1
    gltf_meshes = []
    for m in meshes:
        gltf_meshes.append({"name": m['name'], "primitives": [{"attributes": {
            "POSITION": accessor((m['p'].astype('<f4') / 100).astype('<f4'), 'VEC3', 34962),
            "NORMAL": accessor((m['n'].astype('<f4') / 127).astype('<f4'), 'VEC3', 34962),
            "COLOR_0": accessor(linear_rgb(m['c']).astype('<f4'), 'VEC3', 34962)},
            "indices": accessor(m['i'].reshape(-1), 'SCALAR', 34963), "material": 0}]})
    manifest = {"source": "GOSHA_30092026.stl", "sha256": SOURCE_SHA, "sourceTriangles": 204821,
                "triangles": sum(m['i'].size // 3 for m in meshes), "translationMm": [0, 0, -89],
                "boundsMm": [np.concatenate([m['p'] for m in meshes]).min(0).astype(float).tolist(), np.concatenate([m['p'] for m in meshes]).max(0).astype(float).tolist()],
                "colours": {str(c): colour_rgb(c).tolist() for c in [4735, 5756, 5947, 7399]},
                "parts": [m['name'] for m in meshes], "components": components, "simplification": errors}
    manifest['boundsMm'] = (np.array(manifest['boundsMm']) / 100).tolist()
    gltf = {"asset": {"version": "2.0", "generator": "Gosha shared assembly pipeline"}, "scene": 0,
            "scenes": [{"nodes": list(range(len(meshes)))}],
            "nodes": [{"name": m['name'], "mesh": i} for i, m in enumerate(meshes)],
            "meshes": gltf_meshes, "materials": [{"name": "Owner STL colours", "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1], "metallicFactor": .25, "roughnessFactor": .48}}],
            "buffers": [{"byteLength": len(binary)}], "bufferViews": views, "accessors": accessors,
            "extras": {"source": manifest['source'], "sha256": SOURCE_SHA, "translationMm": [0, 0, -89]}}
    encoded = json.dumps(gltf, separators=(',', ':')).encode()
    encoded += b' ' * (-len(encoded) % 4)
    binary.extend(b'\0' * (-len(binary) % 4))
    glb = struct.pack('<III', 0x46546C67, 2, 28 + len(encoded) + len(binary)) + struct.pack('<I4s', len(encoded), b'JSON') + encoded + struct.pack('<I4s', len(binary), b'BIN\0') + binary
    (out / (MODEL_NAME + '.glb')).write_bytes(glb)
    packed = bytearray()
    def pack(array):
        while len(packed) % 4:
            packed.append(0)
        ref = {"offset": len(packed), "length": array.size}
        packed.extend(array.tobytes())
        return ref
    meta = {"source": manifest['source'], "sha256": SOURCE_SHA, "meshes": []}
    for m in meshes:
        name = m['name']
        part = 'hands' if name.startswith('hand_') else 'arms' if name.startswith('arm_') else 'legs' if name.startswith(('foot_', 'leg_')) else name if name in ['body', 'head'] else 'fixed'
        meta['meshes'].append({"name": name, "part": part, "positions": pack(m['p']), "normals": pack(m['n']), "colors": pack(m['c']), "indices": pack(m['i']), "indexBytes": m['i'].dtype.itemsize})
    (out / 'mobile-meta.json').write_text(json.dumps(meta, separators=(',', ':')))
    (out / 'mobile-data.base64').write_text(base64.b64encode(packed).decode())
    manifest['glbSha256'] = hashlib.sha256(glb).hexdigest()
    manifest['packedSha256'] = hashlib.sha256(packed).hexdigest()
    (out / (MODEL_NAME + '.json')).write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({"triangles": manifest['triangles'], "glbBytes": len(glb), "mobileBytes": len(packed), "probeMaxMm": max(e['sample_max_mm'] for e in errors), "boundsMm": manifest['boundsMm']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    source, labels = read_source(args.source)
    export(*prepare(source, labels), args.output)
