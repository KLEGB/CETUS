from __future__ import annotations

import pandas as pd
import numpy as np
import geopandas as gp
import shapely as sp
from collections import namedtuple
from multiprocessing.pool import Pool
import os, psutil
import time

Mesh = namedtuple("Mesh", "edge height")
Vector = namedtuple("Vector", "origin direction")


def get_current_memory_gb() -> int:
    # 获取当前进程内存占用。
    pid = os.getpid()
    p = psutil.Process(pid)
    info = p.memory_full_info()
    return info.uss / 1024. / 1024. / 1024.


def bar(process, msg, dig=0, printed=True):
    q = round(process * 100, dig)
    hint = f"\r[{msg}: {'*' * round(q / 2)} {'_' * (50 - round(q / 2))} {q} %]"
    if printed:
        print(hint, end="")
    return hint


def unit(v: np.ndarray) -> np.ndarray:
    lengthSqure = np.sum([dim * dim for dim in v])
    if lengthSqure == 0:
        return v
    else:
        return v / np.sqrt(lengthSqure)


unitSkyVector = pd.read_csv('UHI/sky_vector.csv').to_numpy()
unitSkyVector = [unit(v) for v in unitSkyVector]


def gridding(shp: gp.GeoDataFrame, gridSize=1000) -> gp.GeoDataFrame:
    shp['x'] = shp.geometry.centroid.x
    shp['y'] = shp.geometry.centroid.y
    xSeries = np.arange(shp['x'].min(), shp['x'].max(), gridSize)
    ySeries = np.arange(shp['y'].min(), shp['y'].max(), gridSize)
    geometry = [sp.Polygon([[x - gridSize * 0.5, y - gridSize * 0.5], [x - gridSize * 0.5, y + gridSize * 0.5],
                            [x + gridSize * 0.5, y + gridSize * 0.5], [x + gridSize * 0.5, y - gridSize * 0.5],
                            [x - gridSize * 0.5, y - gridSize * 0.5]]) for x in xSeries for y in ySeries]
    data = [[x, y] for x in xSeries for y in ySeries]
    data = pd.DataFrame(data, columns=['x', 'y'])
    return gp.GeoDataFrame(data, geometry=geometry, crs=shp.crs)


def GridSVF(skyVector: list[Vector], geometrys: list[sp.Geometry], height: list[float], hint=None) -> list[float]:
    """
    calculate the valid sky vector for a open point on the surrounding buildings with heights
    :param vector:  list[Vector] sky vector towards the sky
    :param geometrys: list[sp.Geometry] boundaries for surrounding buildings
    :param height: list[float] height of surrounding buildings
    :return: valid or not
    """

    def intersect(ray: Vector, bld_mesh: Mesh):
        if np.sum([dim * dim for dim in ray.direction]) != 0:
            sun_ray = sp.force_2d(sp.linestrings([ray.origin, ray.origin + (ray.direction * 50)]))
            intersection = sp.get_coordinates(sp.intersection(bld_mesh.edge, sun_ray), include_z=False)
            if len(intersection) > 0:
                intersection = intersection[0]
                squreDistance = (ray.origin[:2] - intersection)
                squreDistance = squreDistance[0] * squreDistance[0] + squreDistance[1] * squreDistance[1]
                if squreDistance == 0:
                    return True
                elif (bld_mesh.height * bld_mesh.height / squreDistance >
                      ray.direction[2] * ray.direction[2] / (
                              ray.direction[0] * ray.direction[0] + ray.direction[1] * ray.direction[1])):
                    return True
        return False

    def create_mesh(polygon: sp.Polygon, height: float):
        coor = sp.get_coordinates(polygon)
        point_twins = [[coor[i - 1], coor[i]] for i in range(1, len(coor))]
        edge_list = [sp.linestrings(twins) for twins in point_twins]
        return [Mesh(edge, height) for edge in edge_list]

    #
    # poiCenter = sp.points([vector[0][0], vector[0][1]])
    # oriheight = vector[0][2]
    # surrounding = [j for j in range(len(geometrys))
    #                if sp.distance(poiCenter, geometrys[j]) < 200]
    # surrounding = [j for j in surrounding if height > oriheight]
    # skyVector = [unit(v) for v in skyVector]
    if len(height) == 0:
        print(f'\r{hint}empty', end='')
        return [skyVector[0].origin[0], skyVector[0].origin[1], 1.0]
    surrounding = [create_mesh(geo, float(h) - skyVector[0].origin[2]) for geo, h in zip(geometrys, height)]
    blds = [_mesh for meshlist in surrounding for _mesh in meshlist]

    valid = np.array([1] * len(skyVector))

    for i in range(len(skyVector)):
        for bld_mesh in blds:
            if intersect(skyVector[i], bld_mesh):
                valid[i] = 0
                break

    result = [skyVector[0].origin[0], skyVector[0].origin[1], np.sum(valid) / len(valid)]
    del surrounding
    del blds
    del skyVector
    print(f'\r{hint}{result[2]}', end='')
    # if result[2] < 1.0:
    #     print(f'{result}')
    return result


def SVFPOI(shp: gp.GeoDataFrame, heightColumn: str, xmin=None, ymin=None, xmax=None, ymax=None, gridSize=1000,
           refine=True, refineSearchRadius=10000, refineResolution=1000) -> pd.DataFrame:
    """encoded method: (x-xmin)/gridSize,(y-ymin)/gridSize"""
    if "x" not in shp.columns:
        shp['x'] = shp.geometry.centroid.x
        shp['y'] = shp.geometry.centroid.y
    xmin = shp['x'].min() if xmin is None else xmin
    ymin = shp['y'].min() if ymin is None else ymin
    xmax = shp['x'].max() if xmax is None else xmax
    ymax = shp['y'].max() if ymax is None else ymax
    shp['x'] = shp.geometry.centroid.x
    shp['y'] = shp.geometry.centroid.y
    shp.sort_values(by=['x', 'y'], inplace=True)
    shp['xCode'] = np.floor((shp['x'] - xmin + gridSize * 0.5) / gridSize)
    shp['yCode'] = np.floor((shp['y'] - ymin + gridSize * 0.5) / gridSize)
    encodedShp = shp.groupby(['xCode', 'yCode'])
    xSeries = np.arange(xmin, xmax, gridSize)
    ySeries = np.arange(ymin, ymax, gridSize)

    """Base SVF"""
    SVFFrame = []
    prs_pool = Pool(processes=8)
    for i, x in enumerate(xSeries):
        for j, y in enumerate(ySeries):
            try:
                region = encodedShp.get_group((i, j))
                if gridSize < 100:
                    regions = [region]
                    if i > 0:
                        try:
                            regions += [encodedShp.get_group((i - 1, j))]
                        except:
                            pass
                    if j > 0:
                        try:
                            regions += [encodedShp.get_group((i, j - 1))]
                        except:
                            pass
                    if i < len(xSeries) - 1:
                        try:
                            regions += [encodedShp.get_group((i + 1, j))]
                        except:
                            pass
                    if j < len(ySeries) - 1:
                        try:
                            regions += [encodedShp.get_group((i, j + 1))]
                        except:
                            pass
                    region = pd.concat(regions)
                geo = region['geometry']
                height = region[heightColumn].astype(float)
            except:
                region = []
                geo = []
                height = []
            skyVec = [Vector(origin=[x, y, 0], direction=v) for v in unitSkyVector]
            hint = bar((i * len(ySeries) + j) / len(xSeries) / len(ySeries), f'processing', 2, False)
            SVFFrame.append(prs_pool.apply_async(GridSVF, (skyVec, geo, height, hint,)))
            memory = get_current_memory_gb()
            bar((i * len(ySeries) + j) / len(xSeries) / len(ySeries), f'preparing{len(region)}', 2)
            print(f'Memory OCC:{"%.2f" % memory}/16 GB', end='')
            while memory > 15:
                memory = get_current_memory_gb()
                bar((i * len(ySeries) + j) / len(xSeries) / len(ySeries), f'preparing{len(region)}', 2)
                print(f'Memory OCC:{"%.2f" % memory}/16 GB', end='')
                time.sleep(0.5)

    prs_pool.close()
    prs_pool.join()
    for s in range(len(SVFFrame)):
        SVFFrame[s] = SVFFrame[s].get()
    SVFFrame = np.array(SVFFrame)
    np.save('SVFFrame.npy', SVFFrame)
    SVFFrame = pd.DataFrame(SVFFrame, columns=['x', 'y', 'SVF'])

    """Average SVF"""
    if refine:
        SVFFrame = reinforce(SVFFrame, resolution=refineResolution, searchRadius=refineSearchRadius)

    return SVFFrame


def subAOIArea(shp: gp.GeoDataFrame, heightColumn: str = None, xmin=None, ymin=None, xmax=None, ymax=None,
               gridSize=1000, refineResolution=1000, refineSearchRadius=10000,
               refine=True) -> pd.DataFrame:
    """encoded method: (x-xmin)/gridSize,(y-ymin)/gridSize"""
    if "x" not in shp.columns:
        shp['x'] = shp.geometry.centroid.x
        shp['y'] = shp.geometry.centroid.y
    xmin = shp['x'].min() if xmin is None else xmin
    ymin = shp['y'].min() if ymin is None else ymin
    xmax = shp['x'].max() if xmax is None else xmax
    ymax = shp['y'].max() if ymax is None else ymax
    shp['x'] = shp.geometry.centroid.x
    shp['y'] = shp.geometry.centroid.y
    shp.sort_values(by=['x', 'y'], inplace=True)
    shp['xCode'] = np.floor((shp['x'] - xmin + gridSize * 0.5) / gridSize)
    shp['yCode'] = np.floor((shp['y'] - ymin + gridSize * 0.5) / gridSize)
    encodedShp = shp.groupby(['xCode', 'yCode'])
    xSeries = np.arange(xmin, xmax, gridSize)
    ySeries = np.arange(ymin, ymax, gridSize)
    sizes = encodedShp.size()

    """Base Area"""
    AreaValueFrame = []
    for i, x in enumerate(xSeries):
        for j, y in enumerate(ySeries):
            # print(i, j,x,y)
            try:
                si = sizes.loc[(i, j)]
                if si > 0:
                    region = encodedShp.get_group((i, j))
                    # print(region['x'].mean(),region['x'].min(),region['x'].max())
                    # print(x-gridSize*0.5,x+gridSize*0.5)
                    if heightColumn:
                        AreaValueFrame.append([x, y, np.sum(
                            region.geometry.area.to_numpy() * region[heightColumn].astype(
                                float).to_numpy()) / gridSize / gridSize])
                    else:
                        AreaValueFrame.append([x, y, np.sum(region.geometry.area) / gridSize / gridSize])
                else:
                    AreaValueFrame.append([x, y, 0])
                bar((i * len(ySeries) + j) / len(xSeries) / len(ySeries), f'AreaSummary', 2)
            except Exception as e:

                AreaValueFrame.append([x, y, 0])
                bar((i * len(ySeries) + j) / len(xSeries) / len(ySeries), f'Empty!', 2)
                continue

    AreaValueFrame = np.array(AreaValueFrame)
    np.save('AreaValueFrame.npy', AreaValueFrame)
    SVFFrame = pd.DataFrame(AreaValueFrame, columns=['x', 'y', 'area'])

    """Average SVF"""
    if refine:
        SVFFrame = reinforce(SVFFrame, resolution=refineResolution, searchRadius=refineSearchRadius, dataColName='area')

    return SVFFrame


def facadeArea(shp: gp.GeoDataFrame, heightColumn: str = None, xmin=None, ymin=None, xmax=None, ymax=None,
               gridSize=1000,
               refineResolution=1000, refineSearchRadius=10000,
               refine=True) -> pd.DataFrame:
    """encoded method: (x-xmin)/gridSize,(y-ymin)/gridSize"""
    if "x" not in shp.columns:
        shp['x'] = shp.geometry.centroid.x
        shp['y'] = shp.geometry.centroid.y
    xmin = shp['x'].min() if xmin is None else xmin
    ymin = shp['y'].min() if ymin is None else ymin
    xmax = shp['x'].max() if xmax is None else xmax
    ymax = shp['y'].max() if ymax is None else ymax
    shp['x'] = shp.geometry.centroid.x
    shp['y'] = shp.geometry.centroid.y
    shp.sort_values(by=['x', 'y'], inplace=True)
    shp['xCode'] = np.floor((shp['x'] - xmin + gridSize * 0.5) / gridSize)
    shp['yCode'] = np.floor((shp['y'] - ymin + gridSize * 0.5) / gridSize)
    encodedShp = shp.groupby(['xCode', 'yCode'])
    xSeries = np.arange(xmin, xmax, gridSize)
    ySeries = np.arange(ymin, ymax, gridSize)
    sizes = encodedShp.size()

    """Base Area"""
    AreaValueFrame = []
    for i, x in enumerate(xSeries):
        for j, y in enumerate(ySeries):
            # print(i, j,x,y)
            try:
                si = sizes.loc[(i, j)]
                if si > 0:
                    region = encodedShp.get_group((i, j))
                    # print(region['x'].mean(),region['x'].min(),region['x'].max())
                    # print(x-gridSize*0.5,x+gridSize*0.5)
                    if heightColumn:
                        height = [min(scale, 18.0) for scale in region[heightColumn].astype(float)]
                        AreaValueFrame.append([x, y, np.sum(region.geometry.length * height) / gridSize / gridSize])
                    else:
                        AreaValueFrame.append([x, y, np.sum(region.geometry.area) / gridSize / gridSize])
                else:
                    AreaValueFrame.append([x, y, 0])
                bar((i * len(ySeries) + j) / len(xSeries) / len(ySeries), f'AreaSummary', 2)
            except:
                AreaValueFrame.append([x, y, 0])
                bar((i * len(ySeries) + j) / len(xSeries) / len(ySeries), f'Empty!', 2)
                continue

    AreaValueFrame = np.array(AreaValueFrame)
    np.save('AreaValueFrame.npy', AreaValueFrame)
    SVFFrame = pd.DataFrame(AreaValueFrame, columns=['x', 'y', 'area'])

    """Average SVF"""
    if refine:
        SVFFrame = reinforce(SVFFrame, resolution=refineResolution, searchRadius=refineSearchRadius, dataColName='area')

    return SVFFrame


def reinforce(df: pd.DataFrame, resolution=1000, searchRadius=10000, dataColName='SVF'):
    df['r' + dataColName] = df[dataColName]
    df.sort_values(by=['x', 'y'], inplace=True)
    df['xCode'] = np.floor((df['x'] - df['x'].min() + resolution * 0.5) / resolution)
    df['yCode'] = np.floor((df['y'] - df['y'].min() + resolution * 0.5) / resolution)
    df.fillna(0, inplace=True)
    encodedDf = df.groupby(['xCode', 'yCode'])
    sizes = encodedDf.size()
    xSeries = np.arange(df['x'].min(), df['x'].max(), resolution)
    ySeries = np.arange(df['y'].min(), df['y'].max(), resolution)
    for i, _ in enumerate(xSeries):
        for j, _ in enumerate(ySeries):
            subRangeX = np.arange(max(np.ceil(i - searchRadius / resolution), df['xCode'].min()),
                                  min(np.floor(i + searchRadius / resolution), df['xCode'].max()), 1)
            subRangeY = np.arange(max(np.ceil(j - searchRadius / resolution), df['yCode'].min()),
                                  min(np.floor(j + searchRadius / resolution), df['yCode'].max()), 1)
            meansCol = []
            for x, y in zip(subRangeX, subRangeY):
                try:
                    size = sizes.loc[(int(x), int(y))]
                except:
                    continue
                if size > 0:
                    meansCol.append(np.mean(encodedDf.get_group((int(x), int(y)))[dataColName]))
            df['r' + dataColName] = np.mean(meansCol) if len(meansCol) > 0 else df['r' + dataColName]
            bar((i * len(ySeries) + j) / len(xSeries) / len(ySeries), 'reinforcing', 2)
    return df


if __name__ == '__main__':
    prjDir = r'F:\Shanghai_geodatabase'
    files = []
    for f in os.listdir(prjDir):
        if f.endswith('.csv') and f.startswith('plotRatio'):
            files.append(pd.read_csv(os.path.join('F:\Shanghai_geodatabase', f)))
    pd.concat(files).to_csv(r'F:\Shanghai_geodatabase\_summaryplotRatio.csv')
    raise Exception
    '299806.9073,401366.9073,3398285.1330,3521305.1330 [EPSG:32651]'
    xs = np.linspace(299806.9073, 401366.9073, 10)
    ys = np.linspace(3398285.1330, 3521305.1330, 10)
    for i in range(9):
        for j in range(9):
            print(f'Batchs:{i * 9 + j + 1}/81')
            # areaFame = SVFPOI(gp.read_file(prjDir+r'\web_shanghai_final.shp'), 'f_huzhaoeight',
            #                   gridSize=50,xmin=xs[i], ymin=ys[j], xmax=xs[i+1], ymax=ys[j+1],
            #                   refine=False)
            # areaFame.to_csv(prjDir+f'\\areaFame{i*9+j}.csv', index=False)
            plotRatio = subAOIArea(gp.read_file(prjDir+r'\web_shanghai_final.shp'), gridSize=50,
                                   xmin=xs[i], ymin=ys[j], xmax=xs[i+1], ymax=ys[j+1],heightColumn='f_height', refine=False)
            plotRatio.to_csv(prjDir+f'\\plotRatio{i*9+j}.csv', index=False)
            facaAr = facadeArea(gp.read_file(prjDir+r'\web_shanghai_final.shp'),
                                heightColumn='f_height', gridSize=50,xmin=xs[i], ymin=ys[j], xmax=xs[i+1], ymax=ys[j+1], refine=False)
            facaAr.to_csv(prjDir+f'\\facaAr{i * 9 + j}.csv', index=False)

    raise Exception
    # geometry = [sp.Point([x, y]) for x, y in zip(areaFame['x'], areaFame['y'])]
    # gp.GeoDataFrame(areaFame, geometry=geometry, crs=32651).to_file(r'G:\dataset\Shanghai_geodatabase\SVF.shp')
