#!/usr/bin/env python3
import argparse
import hashlib
import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np


STL_FILES = {
    "head": "\u5934.stl",
    "body": "\u8eab.stl",
    "arm": "\u81c2.stl",
    "hand": "\u624b.stl",
    "leg": "\u817f.stl",
    "foot": "\u811a.stl",
}

PARTS = [
    ("head", "head", "all"),
    ("body", "body", "all"),
    ("arm_negative_x", "arm", "negative_x"),
    ("arm_positive_x", "arm", "positive_x"),
    ("hand_negative_x", "hand", "negative_x"),
    ("hand_positive_x", "hand", "positive_x"),
    ("leg_negative_x", "leg", "negative_x"),
    ("leg_positive_x", "leg", "positive_x"),
    ("foot_negative_x", "foot", "negative_x"),
    ("foot_positive_x", "foot", "positive_x"),
]

MATERIALS = {
    "head": [0.90, 0.80, 0.70, 1.0],
    "body": [0.72, 0.75, 0.78, 1.0],
    "arm_negative_x": [0.35, 0.58, 0.84, 1.0],
    "arm_positive_x": [0.35, 0.58, 0.84, 1.0],
    "hand_negative_x": [0.88, 0.70, 0.43, 1.0],
    "hand_positive_x": [0.88, 0.70, 0.43, 1.0],
    "leg_negative_x": [0.45, 0.67, 0.50, 1.0],
    "leg_positive_x": [0.45, 0.67, 0.50, 1.0],
    "foot_negative_x": [0.65, 0.55, 0.82, 1.0],
    "foot_positive_x": [0.65, 0.55, 0.82, 1.0],
}


@dataclass
class StlMesh:
    name: str
    path: Path
    vertices: np.ndarray
    face_normals: np.ndarray


@dataclass
class PreparedMesh:
    name: str
    source_file: str
    side: str
    vertex_count: int
    triangle_count: int
    input_triangle_count: int
    positions: np.ndarray
    normals: np.ndarray
    indices: np.ndarray
    bounds_min: list
    bounds_max: list


def read_binary_stl(path: Path, logical_name: str) -> StlMesh:
    data = path.read_bytes()
    if len(data) < 84:
        raise ValueError(f"{path.name}: file is too small for binary STL")

    tri_count = struct.unpack_from("<I", data, 80)[0]
    expected_size = 84 + tri_count * 50
    if expected_size != len(data):
        raise ValueError(
            f"{path.name}: expected binary STL size {expected_size}, got {len(data)}"
        )

    dtype = np.dtype(
        [
            ("normal", "<f4", (3,)),
            ("vertices", "<f4", (3, 3)),
            ("attribute_byte_count", "<u2"),
        ],
        align=False,
    )
    records = np.frombuffer(data, dtype=dtype, offset=84, count=tri_count)
    vertices = np.array(records["vertices"], dtype=np.float32, copy=True)
    normals = np.array(records["normal"], dtype=np.float32, copy=True)

    if not np.isfinite(vertices).all():
        raise ValueError(f"{path.name}: vertex coordinates contain NaN or Inf")
    if not np.isfinite(normals).all():
        raise ValueError(f"{path.name}: STL normals contain NaN or Inf")

    computed = compute_face_normals(vertices)
    normal_lengths = np.linalg.norm(normals.astype(np.float64), axis=1)
    valid = normal_lengths > 1e-20
    if valid.any():
        normals[valid] = (
            normals[valid].astype(np.float64) / normal_lengths[valid, None]
        ).astype(np.float32)
    normals[~valid] = computed[~valid]

    return StlMesh(logical_name, path, vertices, normals)


def compute_face_normals(vertices: np.ndarray) -> np.ndarray:
    edge_a = vertices[:, 1, :].astype(np.float64) - vertices[:, 0, :].astype(np.float64)
    edge_b = vertices[:, 2, :].astype(np.float64) - vertices[:, 0, :].astype(np.float64)
    cross = np.cross(edge_a, edge_b)
    lengths = np.linalg.norm(cross, axis=1)
    if np.any(lengths <= 1e-20):
        bad = int(np.where(lengths <= 1e-20)[0][0])
        raise ValueError(f"degenerate triangle at index {bad}")
    return (cross / lengths[:, None]).astype(np.float32)


def side_mask(vertices: np.ndarray, side: str) -> np.ndarray:
    if side == "all":
        return np.ones(vertices.shape[0], dtype=bool)

    xs = vertices[:, :, 0]
    negative = np.max(xs, axis=1) < 0.0
    positive = np.min(xs, axis=1) > 0.0
    crossing = ~(negative | positive)
    if np.any(crossing):
        first = int(np.where(crossing)[0][0])
        raise ValueError(
            "triangle touches or crosses X=0: "
            f"index={first}, x_min={float(xs[first].min())}, x_max={float(xs[first].max())}"
        )

    if side == "negative_x":
        return negative
    if side == "positive_x":
        return positive
    raise ValueError(f"unknown side {side}")


def unique_rows_float32(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    contiguous = np.ascontiguousarray(values, dtype=np.float32)
    row_type = np.dtype((np.void, contiguous.dtype.itemsize * contiguous.shape[1]))
    packed = contiguous.view(row_type).reshape(-1)
    _, unique_indices, inverse = np.unique(
        packed, return_index=True, return_inverse=True
    )
    order = np.argsort(unique_indices)
    remap = np.empty_like(order)
    remap[order] = np.arange(order.shape[0], dtype=order.dtype)
    positions = contiguous[unique_indices[order]]
    indices = remap[inverse]
    return positions, indices.astype(np.uint32, copy=False)


def prepare_mesh(part_name: str, source: StlMesh, side: str) -> PreparedMesh:
    mask = side_mask(source.vertices, side)
    part_vertices = source.vertices[mask]
    part_face_normals = source.face_normals[mask]
    if part_vertices.shape[0] == 0:
        raise ValueError(f"{part_name}: no triangles after {side} split")

    flat_positions = part_vertices.reshape(-1, 3)
    positions, inverse = unique_rows_float32(flat_positions)
    indices = inverse.reshape(-1, 3)

    repeated_face_normals = np.repeat(part_face_normals, 3, axis=0).astype(np.float64)
    normal_accumulator = np.zeros((positions.shape[0], 3), dtype=np.float64)
    np.add.at(normal_accumulator, inverse, repeated_face_normals)
    normal_lengths = np.linalg.norm(normal_accumulator, axis=1)
    if np.any(normal_lengths <= 1e-20):
        bad = int(np.where(normal_lengths <= 1e-20)[0][0])
        raise ValueError(f"{part_name}: zero accumulated normal at vertex {bad}")
    normals = (normal_accumulator / normal_lengths[:, None]).astype(np.float32)

    bounds_min = positions.min(axis=0).astype(float).tolist()
    bounds_max = positions.max(axis=0).astype(float).tolist()
    return PreparedMesh(
        name=part_name,
        source_file=source.path.name,
        side=side,
        vertex_count=int(positions.shape[0]),
        triangle_count=int(indices.size // 3),
        input_triangle_count=int(part_vertices.shape[0]),
        positions=positions.astype("<f4", copy=False),
        normals=normals.astype("<f4", copy=False),
        indices=indices,
        bounds_min=bounds_min,
        bounds_max=bounds_max,
    )


def pad4(data: bytearray, pad_byte: bytes = b"\x00") -> None:
    while len(data) % 4:
        data += pad_byte


def add_buffer_view(
    blob: bytearray,
    payload: bytes,
    buffer_views: list,
    target: int,
) -> int:
    pad4(blob)
    offset = len(blob)
    blob += payload
    buffer_views.append(
        {"buffer": 0, "byteOffset": offset, "byteLength": len(payload), "target": target}
    )
    return len(buffer_views) - 1


def append_accessor(
    accessors: list,
    buffer_view: int,
    component_type: int,
    count: int,
    accessor_type: str,
    minimum: list | None = None,
    maximum: list | None = None,
) -> int:
    accessor = {
        "bufferView": buffer_view,
        "byteOffset": 0,
        "componentType": component_type,
        "count": count,
        "type": accessor_type,
    }
    if minimum is not None:
        accessor["min"] = minimum
    if maximum is not None:
        accessor["max"] = maximum
    accessors.append(accessor)
    return len(accessors) - 1


def build_gltf(prepared_meshes: list[PreparedMesh]) -> tuple[dict, bytes]:
    bin_blob = bytearray()
    buffer_views = []
    accessors = []
    meshes = []
    nodes = []
    materials = []

    for mesh_index, mesh in enumerate(prepared_meshes):
        material_index = len(materials)
        materials.append(
            {
                "name": f"{mesh.name}_material",
                "pbrMetallicRoughness": {
                    "baseColorFactor": MATERIALS[mesh.name],
                    "metallicFactor": 0.0,
                    "roughnessFactor": 0.85,
                },
            }
        )

        position_view = add_buffer_view(
            bin_blob, mesh.positions.tobytes(order="C"), buffer_views, 34962
        )
        normal_view = add_buffer_view(
            bin_blob, mesh.normals.tobytes(order="C"), buffer_views, 34962
        )

        if mesh.vertex_count <= 65535:
            index_array = mesh.indices.astype("<u2", copy=False)
            component_type = 5123
        else:
            index_array = mesh.indices.astype("<u4", copy=False)
            component_type = 5125
        index_view = add_buffer_view(
            bin_blob, index_array.reshape(-1).tobytes(order="C"), buffer_views, 34963
        )

        position_accessor = append_accessor(
            accessors,
            position_view,
            5126,
            mesh.vertex_count,
            "VEC3",
            minimum=mesh.bounds_min,
            maximum=mesh.bounds_max,
        )
        normal_accessor = append_accessor(
            accessors, normal_view, 5126, mesh.vertex_count, "VEC3"
        )
        index_accessor = append_accessor(
            accessors, index_view, component_type, int(mesh.indices.size), "SCALAR"
        )

        meshes.append(
            {
                "name": mesh.name,
                "primitives": [
                    {
                        "attributes": {
                            "POSITION": position_accessor,
                            "NORMAL": normal_accessor,
                        },
                        "indices": index_accessor,
                        "material": material_index,
                        "mode": 4,
                    }
                ],
                "extras": {
                    "sourceFile": mesh.source_file,
                    "sourceSide": mesh.side,
                    "sourceTriangles": mesh.input_triangle_count,
                    "outputTriangles": mesh.triangle_count,
                    "normalMode": "welded vertex normals averaged from STL face normals",
                },
            }
        )
        nodes.append({"name": mesh.name, "mesh": mesh_index})

    buffer_byte_length = len(bin_blob)
    pad4(bin_blob)

    gltf = {
        "asset": {
            "version": "2.0",
            "generator": "gosha prepare_model.py",
            "extras": {
                "units": "same numeric units as source STL",
                "meshTransforms": "identity",
            },
        },
        "scene": 0,
        "scenes": [{"name": "Gosha", "nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "buffers": [{"byteLength": buffer_byte_length}],
        "bufferViews": buffer_views,
        "accessors": accessors,
    }
    return gltf, bytes(bin_blob)


def write_glb(gltf: dict, bin_blob: bytes, output_path: Path) -> None:
    json_payload = json.dumps(gltf, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    json_chunk = bytearray(json_payload)
    pad4(json_chunk, b" ")

    bin_chunk = bytearray(bin_blob)
    pad4(bin_chunk)

    total_length = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    with output_path.open("wb") as handle:
        handle.write(struct.pack("<4sII", b"glTF", 2, total_length))
        handle.write(struct.pack("<I4s", len(json_chunk), b"JSON"))
        handle.write(json_chunk)
        handle.write(struct.pack("<I4s", len(bin_chunk), b"BIN\x00"))
        handle.write(bin_chunk)


def parse_glb(path: Path) -> tuple[dict, bytes]:
    data = path.read_bytes()
    if len(data) < 20:
        raise ValueError("GLB too small")
    magic, version, total_length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF":
        raise ValueError("GLB magic mismatch")
    if version != 2:
        raise ValueError(f"GLB version mismatch: {version}")
    if total_length != len(data):
        raise ValueError(f"GLB length mismatch: header={total_length}, actual={len(data)}")

    offset = 12
    json_doc = None
    bin_blob = None
    while offset < len(data):
        if offset + 8 > len(data):
            raise ValueError("truncated GLB chunk header")
        chunk_length, chunk_type = struct.unpack_from("<I4s", data, offset)
        offset += 8
        chunk = data[offset : offset + chunk_length]
        if len(chunk) != chunk_length:
            raise ValueError("truncated GLB chunk payload")
        offset += chunk_length
        if chunk_type == b"JSON":
            json_doc = json.loads(chunk.rstrip(b" ").decode("utf-8"))
        elif chunk_type == b"BIN\x00":
            bin_blob = bytes(chunk)

    if json_doc is None:
        raise ValueError("GLB has no JSON chunk")
    if bin_blob is None:
        raise ValueError("GLB has no BIN chunk")
    return json_doc, bin_blob


def accessor_byte_span(gltf: dict, accessor_index: int) -> tuple[int, int, int, str]:
    component_sizes = {5123: 2, 5125: 4, 5126: 4}
    type_counts = {"SCALAR": 1, "VEC3": 3}
    accessor = gltf["accessors"][accessor_index]
    view = gltf["bufferViews"][accessor["bufferView"]]
    component_type = accessor["componentType"]
    accessor_type = accessor["type"]
    byte_offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    byte_length = (
        accessor["count"]
        * component_sizes[component_type]
        * type_counts[accessor_type]
    )
    return byte_offset, byte_length, component_type, accessor_type


def read_accessor_array(gltf: dict, bin_blob: bytes, accessor_index: int) -> np.ndarray:
    byte_offset, byte_length, component_type, accessor_type = accessor_byte_span(
        gltf, accessor_index
    )
    if byte_offset < 0 or byte_offset + byte_length > len(bin_blob):
        raise ValueError(f"accessor {accessor_index} exceeds BIN chunk")
    accessor = gltf["accessors"][accessor_index]
    if component_type == 5126:
        dtype = "<f4"
    elif component_type == 5123:
        dtype = "<u2"
    elif component_type == 5125:
        dtype = "<u4"
    else:
        raise ValueError(f"unsupported component type {component_type}")
    array = np.frombuffer(
        bin_blob, dtype=np.dtype(dtype), count=byte_length // np.dtype(dtype).itemsize, offset=byte_offset
    )
    if accessor_type == "VEC3":
        return array.reshape(accessor["count"], 3)
    return array


def validate_glb(path: Path, expected_meshes: list[PreparedMesh]) -> dict:
    gltf, bin_blob = parse_glb(path)
    expected_names = [mesh.name for mesh in expected_meshes]
    actual_names = [mesh["name"] for mesh in gltf.get("meshes", [])]
    if actual_names != expected_names:
        raise ValueError(f"mesh names mismatch: {actual_names}")
    if len(gltf.get("nodes", [])) != len(expected_meshes):
        raise ValueError("node count does not match mesh count")
    if len(gltf.get("materials", [])) != len(expected_meshes):
        raise ValueError("material count does not match mesh count")
    if gltf["buffers"][0]["byteLength"] > len(bin_blob):
        raise ValueError("buffer byteLength exceeds BIN chunk")

    total_triangles = 0
    total_vertices = 0
    per_mesh = []
    for mesh_index, expected in enumerate(expected_meshes):
        node = gltf["nodes"][mesh_index]
        if node.get("mesh") != mesh_index:
            raise ValueError(f"{expected.name}: node mesh index mismatch")
        for transform_key, identity in [
            ("translation", [0, 0, 0]),
            ("rotation", [0, 0, 0, 1]),
            ("scale", [1, 1, 1]),
            ("matrix", [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]),
        ]:
            if transform_key in node and node[transform_key] != identity:
                raise ValueError(f"{expected.name}: non-identity {transform_key}")

        primitive = gltf["meshes"][mesh_index]["primitives"][0]
        positions = read_accessor_array(gltf, bin_blob, primitive["attributes"]["POSITION"])
        normals = read_accessor_array(gltf, bin_blob, primitive["attributes"]["NORMAL"])
        indices = read_accessor_array(gltf, bin_blob, primitive["indices"])
        if not np.isfinite(positions).all():
            raise ValueError(f"{expected.name}: POSITION contains NaN or Inf")
        if not np.isfinite(normals).all():
            raise ValueError(f"{expected.name}: NORMAL contains NaN or Inf")
        if positions.shape[0] != expected.vertex_count:
            raise ValueError(f"{expected.name}: vertex count mismatch")
        if normals.shape != positions.shape:
            raise ValueError(f"{expected.name}: normal shape mismatch")
        if indices.size != expected.triangle_count * 3:
            raise ValueError(f"{expected.name}: index count mismatch")
        if int(indices.max()) >= positions.shape[0]:
            raise ValueError(f"{expected.name}: index out of range")
        if int(indices.min()) < 0:
            raise ValueError(f"{expected.name}: negative index")

        actual_min = positions.min(axis=0)
        actual_max = positions.max(axis=0)
        if not np.allclose(actual_min, expected.bounds_min):
            raise ValueError(f"{expected.name}: min bounds mismatch")
        if not np.allclose(actual_max, expected.bounds_max):
            raise ValueError(f"{expected.name}: max bounds mismatch")

        total_vertices += int(positions.shape[0])
        total_triangles += int(indices.size // 3)
        per_mesh.append(
            {
                "name": expected.name,
                "source_file": expected.source_file,
                "source_triangles": expected.input_triangle_count,
                "output_triangles": int(indices.size // 3),
                "vertices": int(positions.shape[0]),
                "bounds_min": [float(x) for x in actual_min],
                "bounds_max": [float(x) for x in actual_max],
            }
        )

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "path": str(path),
        "sha256": digest,
        "byte_size": path.stat().st_size,
        "mesh_count": len(expected_meshes),
        "total_vertices": total_vertices,
        "total_triangles": total_triangles,
        "per_mesh": per_mesh,
    }


def resolve_output_path(output_arg: str) -> Path:
    output = Path(output_arg).expanduser()
    if output.suffix.lower() == ".glb":
        return output
    return output / "gosha.glb"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a 10-mesh GLB asset for the Gosha motion editor."
    )
    parser.add_argument("--source-dir", required=True, help="Directory containing source STL files")
    parser.add_argument(
        "--output",
        required=True,
        help="Output GLB path, or output directory where gosha.glb will be written",
    )
    args = parser.parse_args()

    source_dir = Path(args.source_dir).expanduser()
    output_path = resolve_output_path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    loaded = {}
    for logical_name, filename in STL_FILES.items():
        path = source_dir / filename
        if not path.exists():
            raise FileNotFoundError(path)
        loaded[logical_name] = read_binary_stl(path, logical_name)

    prepared = []
    for part_name, source_key, side in PARTS:
        prepared.append(prepare_mesh(part_name, loaded[source_key], side))

    gltf, bin_blob = build_gltf(prepared)
    write_glb(gltf, bin_blob, output_path)
    report = validate_glb(output_path, prepared)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
