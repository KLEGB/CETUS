"""Bridge Python geometry/ray batches to the bundled MoosasRad executable."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from RAD.solar_geometry import BuildingMesh


MODULE_DIR = Path(__file__).resolve().parent
RAD_BIN_DIR = MODULE_DIR / "bin" / "rad"
MOOSAS_RAD_EXE = RAD_BIN_DIR / "MoosasRad.exe"


def ensure_local_rad_binary() -> None:
    """Fail early when the local MoosasRad executable is not available."""
    if not MOOSAS_RAD_EXE.exists():
        raise FileNotFoundError(f"RAD local ray engine is missing: {MOOSAS_RAD_EXE}")


def _geo_face_block(face_id: str, normal: np.ndarray, loop: np.ndarray) -> str:
    """Serialize one polygon face to the lightweight MoosasRad geo format."""
    lines = [f"f,0,{face_id}", f"fn,{normal[0]:.6f},{normal[1]:.6f},{normal[2]:.6f}"]
    for vertex in loop:
        lines.append(f"fv,{vertex[0]:.6f},{vertex[1]:.6f},{vertex[2]:.6f}")
    lines.append(";")
    return "\n".join(lines)


def scene_meshes_to_geo(meshes: list[BuildingMesh]) -> str:
    """Serialize all facade and roof faces from building meshes."""
    blocks: list[str] = []
    for mesh in meshes:
        for edge_index, edge in enumerate(mesh.edges):
            height = float(mesh.heights[edge_index])
            normal = np.array([mesh.facade_normals[edge_index][0], mesh.facade_normals[edge_index][1], 0.0], dtype=float)
            loop = np.array(
                [
                    [edge[0][0], edge[0][1], 0.0],
                    [edge[1][0], edge[1][1], 0.0],
                    [edge[1][0], edge[1][1], height],
                    [edge[0][0], edge[0][1], height],
                    [edge[0][0], edge[0][1], 0.0],
                ],
                dtype=float,
            )
            blocks.append(_geo_face_block(f"{mesh.building_id}_{edge_index}", normal, loop))
        for roof_index, roof_loop in enumerate(mesh.roof_loops):
            if len(roof_loop) >= 4:
                blocks.append(_geo_face_block(f"{mesh.building_id}_roof_{roof_index}", np.array([0.0, 0.0, 1.0], dtype=float), roof_loop))
    return "\n".join(blocks) + ("\n" if blocks else "")


def rays_to_text(origins: np.ndarray, directions: np.ndarray) -> str:
    """Serialize ray origins and directions for MoosasRad."""
    lines = []
    for origin, direction in zip(origins, directions):
        lines.append(
            f"{origin[0]:.6f},{origin[1]:.6f},{origin[2]:.6f},{direction[0]:.6f},{direction[1]:.6f},{direction[2]:.6f}"
        )
    return "\n".join(lines) + ("\n" if lines else "")


def _parse_output(result_path: Path) -> np.ndarray:
    """Read raw MoosasRad hit rows."""
    rows: list[list[float]] = []
    for line in result_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        rows.append([float(value) for value in stripped.split(",")])
    return np.array(rows, dtype=float) if rows else np.empty((0, 6), dtype=float)


def batch_ray_visibility(meshes: list[BuildingMesh], origins: np.ndarray, directions: np.ndarray) -> np.ndarray:
    """Return True for rays that miss all meshes and therefore see the sky."""
    ensure_local_rad_binary()
    if len(origins) == 0:
        return np.array([], dtype=bool)
    if len(meshes) == 0:
        return np.ones(len(origins), dtype=bool)

    with tempfile.TemporaryDirectory(prefix="rad_engine_", dir=str(MODULE_DIR)) as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        geo_path = temp_dir / "scene.geo"
        ray_path = temp_dir / "rays.i"
        result_path = temp_dir / "rays.o"

        geo_path.write_text(scene_meshes_to_geo(meshes), encoding="utf-8")
        ray_path.write_text(rays_to_text(origins, directions), encoding="utf-8")

        subprocess.run(
            [str(MOOSAS_RAD_EXE), "-g", str(geo_path), "-o", str(result_path), str(ray_path)],
            check=True,
            cwd=str(RAD_BIN_DIR),
            capture_output=True,
            text=True,
        )

        output = _parse_output(result_path)
    if len(output) != len(origins):
        raise RuntimeError(f"MoosasRad output row count mismatch: expected {len(origins)}, got {len(output)}")
    misses = np.all(np.isclose(output[:, :3], -1.0, atol=1e-6), axis=1)
    return misses


ensureLocalRadBinary = ensure_local_rad_binary
sceneMeshesToGeo = scene_meshes_to_geo
raysToText = rays_to_text
batchRayVisibility = batch_ray_visibility


if __name__ == "__main__":
    print({"radExecutable": str(MOOSAS_RAD_EXE.resolve()), "exists": MOOSAS_RAD_EXE.exists()})
