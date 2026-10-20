"""Geometry utilities used by RAD sky-visibility and radiation workflows."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

import geopandas as gpd
import numpy as np
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import nearest_points


def vector_length(vec: np.ndarray) -> float:
    """Return Euclidean vector length."""
    return float(np.sqrt(np.dot(vec, vec)))


def projected_weight(ray: np.ndarray, unit: np.ndarray | None = None) -> float:
    """Return the positive front-side cosine projection onto a surface normal."""
    if unit is None:
        unit = np.array([0.0, 0.0, 1.0], dtype=float)
    ray_unit = ray / max(vector_length(ray), 1e-12)
    return float(max(0.0,np.dot(ray_unit, unit)))


@dataclass
class BuildingMesh:
    """Lightweight extruded building mesh used by the RAD ray workflow."""

    building_id: str
    area_m2: float
    perimeter_m: float
    bounds: tuple[float, float, float, float]
    boundary: LineString
    centroid_xy: np.ndarray
    edges: np.ndarray
    heights: np.ndarray
    edge_lengths: np.ndarray
    facade_normals: np.ndarray
    roof_loops: list[np.ndarray]

    @classmethod
    def from_geometry(cls, building_id: str, geometry: Polygon | MultiPolygon, height: float) -> "BuildingMesh":
        """Build facade edges and roof loops from a footprint and height."""
        polygons: list[Polygon] = []
        if isinstance(geometry, Polygon):
            polygons = [geometry]
        elif isinstance(geometry, MultiPolygon):
            polygons = list(geometry.geoms)
        else:
            raise TypeError(f"Unsupported geometry type for building {building_id}: {geometry.geom_type}")

        edges: list[np.ndarray] = []
        heights: list[float] = []
        edge_lengths: list[float] = []
        facade_normals: list[np.ndarray] = []
        roof_loops: list[np.ndarray] = []
        total_area = 0.0
        centroid_weighted_x = 0.0
        centroid_weighted_y = 0.0
        total_perimeter = 0.0
        minx, miny, maxx, maxy = geometry.bounds

        for polygon in polygons:
            coords = np.asarray(polygon.exterior.coords, dtype=float)
            roof_loops.append(np.column_stack([coords[:, 0], coords[:, 1], np.full(len(coords), float(height), dtype=float)]))
            ccw = bool(polygon.exterior.is_ccw)
            for index in range(1, len(coords)):
                start = coords[index - 1][:2]
                end = coords[index][:2]
                direction = end - start
                edge_length = float(np.linalg.norm(direction))
                if edge_length <= 1e-12:
                    continue
                unit_direction = direction / edge_length
                if ccw:
                    outward = np.array([unit_direction[1], -unit_direction[0]], dtype=float)
                else:
                    outward = np.array([-unit_direction[1], unit_direction[0]], dtype=float)
                edges.append(np.array([start, end], dtype=float))
                heights.append(float(height))
                edge_lengths.append(edge_length)
                facade_normals.append(outward)
            area = float(polygon.area)
            centroid = polygon.centroid
            total_area += area
            centroid_weighted_x += centroid.x * area
            centroid_weighted_y += centroid.y * area
            total_perimeter += float(polygon.exterior.length)

        if total_area <= 0:
            raise ValueError(f"Building {building_id} has non-positive footprint area.")
        centroid_xy = np.array([centroid_weighted_x / total_area, centroid_weighted_y / total_area], dtype=float)
        return cls(
            building_id=str(building_id),
            area_m2=total_area,
            perimeter_m=total_perimeter,
            bounds=(minx, miny, maxx, maxy),
            boundary=geometry.boundary,
            centroid_xy=centroid_xy,
            edges=np.array(edges, dtype=float),
            heights=np.array(heights, dtype=float),
            edge_lengths=np.array(edge_lengths, dtype=float),
            facade_normals=np.array(facade_normals, dtype=float),
            roof_loops=roof_loops,
        )

    def could_intersect(self, point_xy: np.ndarray, radius: float) -> bool:
        """Quick bounding-box test for whether a point buffer may hit this mesh."""
        minx, miny, maxx, maxy = self.bounds
        return not (
            maxx < point_xy[0] - radius
            or minx > point_xy[0] + radius
            or maxy < point_xy[1] - radius
            or miny > point_xy[1] + radius
        )

    def intersects_ray(self, point: np.ndarray, ray: np.ndarray) -> bool:
        """Return whether a 3D ray is blocked by any facade segment."""
        if (ray[0] * ray[0] + ray[1] * ray[1]) <= 1e-12:
            return False
        for index, edge in enumerate(self.edges):
            p2 = edge[0]
            p2v = edge[1]
            edge_direction = p2v - p2
            edge_length = np.linalg.norm(edge_direction)
            if edge_length <= 1e-12:
                continue
            v2 = edge_direction / edge_length
            distance = self.intersection_t(point[:2], ray[:2], p2, v2)
            if distance is None:
                continue
            along_edge = np.dot((ray[:2] * distance - (p2 - point[:2])), v2)
            if along_edge <= 0 or along_edge >= edge_length:
                continue
            relative_height = self.heights[index] - point[2]
            if abs(relative_height) <= 1e-12:
                continue
            if ((distance * ray[2] / relative_height) ** 2) > (ray[0] * ray[0] + ray[1] * ray[1]):
                return True
        return False

    @staticmethod
    def intersection_t(p1: np.ndarray, v1: np.ndarray, p2: np.ndarray, v2: np.ndarray) -> float | None:
        """Return positive 2D ray intersection distance or None."""
        if abs(np.dot(v1, v2)) ** 2 > 0.99:
            return None
        denominator = v2[0] * v1[1] - v1[0] * v2[1]
        if abs(denominator) <= 1e-12:
            return None
        t = (v2[0] * (p2[1] - p1[1]) - v2[1] * (p2[0] - p1[0])) / denominator
        return float(t) if t > 0.1 else None


def load_building_meshes(gdf: gpd.GeoDataFrame, id_field: str, height_field: str) -> list[BuildingMesh]:
    """Convert a building GeoDataFrame to RAD meshes."""
    meshes: list[BuildingMesh] = []
    for _, row in gdf.iterrows():
        geometry = row.geometry
        height = float(row[height_field])
        building_id = str(row[id_field])
        meshes.append(BuildingMesh.from_geometry(building_id, geometry, height))
    return meshes


def visible_sky_mask(point: np.ndarray, sky_vectors: np.ndarray, neighbors: Iterable[BuildingMesh]) -> np.ndarray:
    """Compute a binary sky-visibility mask for one sample point."""
    visibility = np.ones(len(sky_vectors), dtype=float)
    for idx, vector in enumerate(sky_vectors):
        for neighbor in neighbors:
            if neighbor.intersects_ray(point, vector):
                visibility[idx] = 0.0
                break
    return visibility


def candidate_neighbors(
    target: BuildingMesh,
    meshes: list[BuildingMesh],
    point_xy: np.ndarray,
    point_z: float,
) -> list[BuildingMesh]:
    """Select nearby taller meshes that may block a target sample point."""
    candidates: list[tuple[float, BuildingMesh]] = []
    for mesh in meshes:
        if mesh.building_id == target.building_id:
            continue
        if np.max(mesh.heights) <= point_z:
            continue
        distance = float(np.linalg.norm(mesh.centroid_xy - point_xy))
        if distance <= 150.0:
            candidates.append((distance, mesh))

    if not candidates:
        return []

    candidates.sort(key=lambda item: item[0])
    nearest_five = {mesh.building_id: mesh for _, mesh in candidates[:5]}
    within_fifty = {mesh.building_id: mesh for distance, mesh in candidates if distance <= 50.0}

    merged = nearest_five.copy()
    merged.update(within_fifty)
    return list(merged.values())


def facade_sample_heights(building_height: float, floor_height: float) -> np.ndarray:
    """Return one facade sample height per represented floor."""
    if floor_height <= 0:
        raise ValueError("floor_height must be positive.")
    level_count = max(1, int(math.ceil(building_height / floor_height)))
    heights = []
    for idx in range(level_count):
        sample_height = min((idx + 0.5) * floor_height, building_height - 0.1)
        heights.append(max(sample_height, min(building_height * 0.5, 0.1)))
    return np.array(heights, dtype=float)


def bbox_midpoint_sample_points(target: BuildingMesh) -> list[dict[str, np.ndarray | float]]:
    """Generate cardinal facade sample points from footprint bounding-box midpoints."""
    minx, miny, maxx, maxy = target.bounds
    bbox_midpoints = [
        np.array([(minx + maxx) * 0.5, maxy], dtype=float),  # north
        np.array([maxx, (miny + maxy) * 0.5], dtype=float),  # east
        np.array([(minx + maxx) * 0.5, miny], dtype=float),  # south
        np.array([minx, (miny + maxy) * 0.5], dtype=float),  # west
    ]
    samples: list[dict[str, np.ndarray | float]] = []
    seen: set[tuple[int, int]] = set()
    for midpoint_xy in bbox_midpoints:
        nearest_on_boundary, _ = nearest_points(target.boundary, Point(float(midpoint_xy[0]), float(midpoint_xy[1])))
        point_xy = np.array([nearest_on_boundary.x, nearest_on_boundary.y], dtype=float)
        outward = midpoint_xy - point_xy
        if np.linalg.norm(outward) <= 1e-9:
            outward = point_xy - target.centroid_xy
        if np.linalg.norm(outward) <= 1e-9:
            continue
        outward = outward / np.linalg.norm(outward)
        key = (round(float(point_xy[0]), 3), round(float(point_xy[1]), 3))
        if key in seen:
            continue
        seen.add(key)
        samples.append({"point_xy": point_xy, "normal": np.array([outward[0], outward[1], 0.0], dtype=float)})
    return samples


def facade_sample_points(
    target: BuildingMesh,
    floor_height: float,
    offset: float = 0.1,
) -> list[dict[str, np.ndarray | float]]:
    """Generate full 3D facade sample points for a building mesh."""
    sample_heights = facade_sample_heights(float(np.max(target.heights)), floor_height)
    horizontal_samples = bbox_midpoint_sample_points(target)
    samples: list[dict[str, np.ndarray | float]] = []
    for horizontal_sample in horizontal_samples:
        point_xy = np.array(horizontal_sample["point_xy"], dtype=float)
        facade_normal = np.array(horizontal_sample["normal"], dtype=float)
        shifted_xy = point_xy + facade_normal[:2] * offset
        for z in sample_heights:
            samples.append(
                {
                    "point": np.array([shifted_xy[0], shifted_xy[1], z], dtype=float),
                    "normal": facade_normal,
                    "weight": 1.0,
                }
            )
    return samples


vectorLength = vector_length
projectedWeight = projected_weight
loadBuildingMeshes = load_building_meshes
visibleSkyMask = visible_sky_mask
candidateNeighbors = candidate_neighbors
facadeSampleHeights = facade_sample_heights
bboxMidpointSamplePoints = bbox_midpoint_sample_points
facadeSamplePoints = facade_sample_points


if __name__ == "__main__":
    print({"unitProjection": projectedWeight(np.array([0.0, 0.0, 1.0], dtype=float))})
