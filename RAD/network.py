"""Point radiation network: sky exposure, one SW reflection, refreshed LW emission.

All flux channels are incident W/m2, not energies or pre-absorbed heat sources.
Finite diffuse patch exchange is reciprocal, visibility-tested and row bounded.
"""
from __future__ import annotations
import hashlib,json,os,platform,shutil,struct,subprocess,tempfile
from pathlib import Path
import numpy as np
from scipy.sparse import coo_matrix,csr_matrix,eye,diags
from scipy.sparse.linalg import factorized
from scipy.spatial import cKDTree
from shapely.geometry import Point,Polygon
from shapely import constrained_delaunay_triangles
from .workflow import prepare_buildings,_sample_indices
from .solar_geometry import load_building_meshes,facade_sample_heights
from .weather_epw import load_sun_vectors
SIGMA=5.670374419e-8
ROOT=Path(__file__).resolve().parent

class Visibility:
    """One BVH process for all infinite sky rays and finite surface segments."""
    def __init__(self, triangles):
        source=ROOT/'transport_engine/main.go'
        binary=source.parent/('transport.exe' if os.name=='nt' else 'transport')
        if not binary.exists() or binary.stat().st_mtime<source.stat().st_mtime:
            go=os.environ.get('CETUS_GO') or shutil.which('go')
            if not go:raise RuntimeError('Go 1.22+ or CETUS_GO required to build RAD transport')
            subprocess.run([go,'build','-o',str(binary),'.'],cwd=source.parent,check=True,capture_output=True)
        self.tmp=tempfile.TemporaryDirectory(prefix='rad_scene_')
        scene=Path(self.tmp.name)/'scene.json';scene.write_text(json.dumps(triangles,separators=(',',':')))
        self.errors=tempfile.TemporaryFile()
        self.p=subprocess.Popen([str(binary),str(scene)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.errors,
            env={**os.environ,'GOMAXPROCS':os.environ.get('CETUS_RAD_WORKERS','4')})
    def query(self, origins, directions, distances=1e12):
        rays=np.column_stack([origins,directions,np.broadcast_to(distances,(len(origins),))]).astype('<f8')
        output=[]
        for start in range(0,len(rays),50000):
            batch=rays[start:start+50000];self.p.stdin.write(struct.pack('<I',len(batch)));self.p.stdin.write(batch.tobytes());self.p.stdin.flush()
            data=bytearray()
            while len(data)<len(batch):
                part=self.p.stdout.read(len(batch)-len(data))
                if not part:raise RuntimeError('RAD visibility service exited')
                data.extend(part)
            output.append(np.frombuffer(data,dtype=np.uint8).astype(bool))
        return np.concatenate(output) if output else np.zeros(0,dtype=bool)
    def close(self):
        if self.p.poll() is None:self.p.stdin.close();self.p.wait(timeout=30)
        self.p.stdout.close();self.errors.close();self.tmp.cleanup()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()


def optical_models(n,options=None):
    from ENERGY.surface import build_surface_input
    models=[build_surface_input(b,n.climate[0],options=options)[0] for b in n.buildings]
    for model in models:
        for s in model['surfaces']:
            if not all(np.isfinite(s[k]) and 0<=s[k]<=1 for k in ('reflectance','transmittance','emissivity')) or s['reflectance']+s['transmittance']>1+1e-12:
                raise ValueError('Nonphysical surface optical parameters before RAD computation')
    return models

def optical_signature(n,options=None):
    data=[]
    for b,m in zip(n.buildings,optical_models(n,options)):
        data.append([b.wwr,b.StoryHeight,b.height,b._perimeter,[(s['name'],s['area_m2'],s['reflectance'],s['transmittance'],s['emissivity']) for s in m['surfaces']]])
    if n._shp:data.append(hashlib.sha256(Path(n._shp).read_bytes()).hexdigest())
    if n._epw:data.append(hashlib.sha256(Path(n._epw).read_bytes()).hexdigest())
    return hashlib.sha256(json.dumps(data,sort_keys=True,allow_nan=False).encode()).hexdigest()


def reciprocal_exchange(points,normals,areas,sky,owners,visibility,radius):
    """Symmetric area conductance Cij=Ai*Fij, no same-point/backside/blocked links."""
    tree=cKDTree(points);ii=[];jj=[];cc=[]
    for begin in range(0,len(points),256):
        neighborhoods=tree.query_ball_point(points[begin:begin+256],radius)
        for i,neighbors in enumerate(neighborhoods,begin):
            j=np.asarray(neighbors,dtype=int);j=j[j>i]
            if not len(j):continue
            delta=points[j]-points[i];distance=np.linalg.norm(delta,axis=1)
            valid=distance>1e-3;j=j[valid];delta=delta[valid];distance=distance[valid]
            unit=delta/distance[:,None]
            ci=unit@normals[i];cj=-np.einsum('ij,ij->i',unit,normals[j]);valid=(ci>1e-8)&(cj>1e-8)
            j=j[valid];distance=distance[valid];unit=unit[valid];ci=ci[valid];cj=cj[valid]
            if not len(j):continue
            clear=visibility.query(np.repeat(points[i:i+1],len(j),axis=0),unit,distance)
            j=j[clear];distance=distance[clear];ci=ci[clear];cj=cj[clear]
            ii.extend([i]*len(j));jj.extend(j.tolist());cc.extend((areas[i]*areas[j]*ci*cj/(np.pi*distance**2)).tolist())
    i=np.asarray(ii,dtype=int);j=np.asarray(jj,dtype=int);c=np.asarray(cc)
    raw=np.bincount(np.r_[i,j],weights=np.r_[c,c],minlength=len(points))
    capacity=areas*np.clip(1-sky,0,1)
    scale=np.minimum(1,capacity/np.maximum(raw,1e-30))
    c*=np.minimum(scale[i],scale[j])  # symmetric limiter preserves reciprocity
    C=coo_matrix((np.r_[c,c],(np.r_[i,j],np.r_[j,i])),shape=(len(points),len(points))).tocsr();C.eliminate_zeros()
    F=C.multiply((1/areas)[:,None]).tocsr()
    return C,F,dict(candidate_visible_pairs=len(c),limited_points=int(np.sum(scale<1)),raw_max_view_sum=float(np.max(raw/areas)))


class RadiationNetwork:
    version='cetus-rad-network-8'
    def __init__(self,points,normals,areas,owners,groups,sky,exchange,group_exchange,direct,reflected,hoy,meta):
        self.points=points;self.normals=normals;self.areas=areas;self.owners=owners;self.groups=groups
        self.sky=sky;self.exchange=exchange;self.group_exchange=group_exchange
        self.direct=direct;self.reflected=reflected;self.hoy=np.asarray(hoy,int);self.meta=meta
        self.longwave=np.zeros_like(direct);self.current=np.zeros((len(points),3))
        self.group_area=np.bincount(groups,weights=areas,minlength=group_exchange.shape[0])
        self.group_sky=np.bincount(groups,weights=areas*sky,minlength=len(self.group_area))/self.group_area
        self.view_sum=np.asarray(group_exchange.sum(axis=1)).ravel()
        self.far=np.maximum(0,1-self.group_sky-self.view_sum)
        self.lookup={int(h):i for i,h in enumerate(self.hoy)};self.path=None;self._lw_solve=None;self._eps_group=None
    def assert_current(self,n,options=None):
        exposed=float(n.site.AreaGround+n.site.AreaRoad+n.site.AreaLandscape+n.site.AreaWater)
        if not np.isclose(exposed,self.group_area[-1],rtol=1e-8,atol=1e-6):raise ValueError('Exposed area changed after RAD; rebuild network')
        if options is None:options=self.meta.get('energy_options')
        if self.meta['optical_signature']!=optical_signature(n,options):
            raise ValueError('Building optics/WWR/geometry changed after RAD; rerun radSimulation after sampleInstance')
    def frame(self,hoy):
        """Return an independent N×3 flux snapshot at a computed HOY."""
        h=self.lookup[int(hoy)]
        return np.column_stack([self.direct[:,h],self.reflected[:,h],self.longwave[:,h]])
    def aggregate(self,values):
        if values.ndim==1:return np.bincount(self.groups,weights=self.areas*values,minlength=len(self.group_area))/self.group_area
        return np.stack([self.aggregate(values[:,h]) for h in range(values.shape[1])],axis=1)
    def solar(self,hoy):
        ids=np.array([self.lookup[int(h)] for h in np.atleast_1d(hoy)])
        return self.aggregate(self.direct[:,ids]+self.reflected[:,ids])
    def emission(self,models,temperatures,ground):
        E=np.zeros(len(self.group_area));ground_eps=self.meta['ground_emissivity']
        E[-1]=ground_eps*SIGMA*sum(w*(t+273.15)**4 for w,t in ground)
        for b,(m,temp) in enumerate(zip(models,temperatures)):
            surfaces={s['name']:s for s in m['surfaces']}
            for offset,names in [(0,('wall','window')),(1,('roof','skylight'))]:
                a=sum(surfaces[k]['area_m2'] for k in names if k in surfaces)
                if a:E[2*b+offset]=sum(surfaces[k]['area_m2']*surfaces[k]['emissivity']*SIGMA*(temp[k]+273.15)**4 for k in names if k in surfaces)/a
        return E
    def longwave_boundary(self,models,temperatures,ground,sky_ir,air):
        E=self.emission(models,temperatures,ground)
        env=self.group_sky*sky_ir+self.far*SIGMA*(air+273.15)**4
        if self._lw_solve is None:
            eps=np.zeros(len(self.group_area));eps[-1]=self.meta['ground_emissivity']
            for b,m in enumerate(models):
                for offset,names in [(0,('wall','window')),(1,('roof','skylight'))]:
                    surfaces=[s for s in m['surfaces'] if s['name'] in names]
                    a=sum(s['area_m2'] for s in surfaces)
                    eps[2*b+offset]=sum(s['area_m2']*s['emissivity'] for s in surfaces)/max(a,1e-30)
            self._eps_group=eps
            self._lw_solve=factorized((eye(len(eps))-diags(1-eps)@self.group_exchange).tocsc())
        # Grey diffuse LW radiosity includes reflected IR; SW remains one bounce.
        J=self._lw_solve(E+(1-self._eps_group)*env)
        incoming=self.group_exchange@J
        hours=[]
        for b,m in enumerate(models):
            boundary=[]
            for s in m['surfaces']:
                g=2*b+(s['kind'] in ('roof','skylight'))
                pair=0 if s['kind'] in ('window','skylight') and self.meta['ignore_window_longwave'] else incoming[g]
                boundary.append(float(env[g]+pair) if s['kind']!='floor' else 0.)
            hours.append(boundary)
        return hours,float(incoming[-1]+env[-1]),J,incoming
    def refresh(self,hoy,E,models=None,sky_ir=0.,air=20.):
        """Clear ALL last-step LW before emitting; SW channels never recalculated."""
        self.current[:,2]=0
        received=self.exchange@E[self.groups]
        self.current[:,0]=self.direct[:,self.lookup[int(hoy)]]
        self.current[:,1]=self.reflected[:,self.lookup[int(hoy)]]
        self.current[:,2]=received
        self.longwave[:,self.lookup[int(hoy)]]=received
        self.meta['longwave_updates']=self.meta.get('longwave_updates',0)+1
        return received
    def save(self,path=None):
        path=Path(path or self.path);path.parent.mkdir(parents=True,exist_ok=True)
        F=self.exchange;G=self.group_exchange
        temporary=path.with_suffix('.writing.npz')
        np.savez_compressed(temporary,protocol=np.array(self.version),points_m=self.points,normals=self.normals,area_m2=self.areas,
            building_index=self.owners,group=self.groups,sky_view=self.sky,hoy=self.hoy,
            flux_w_m2=np.stack([self.direct,self.reflected,self.longwave],axis=-1).astype(np.float32),
            F_data=F.data,F_indices=F.indices,F_indptr=F.indptr,G_data=G.data,G_indices=G.indices,G_indptr=G.indptr,
            metadata=np.array(json.dumps(self.meta,allow_nan=False)))
        with open(temporary,'rb') as stream:os.fsync(stream.fileno())
        import zipfile
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:raise RuntimeError('RAD cache CRC verification failed')
        temporary.replace(path)
        self.path=str(path.resolve());return path
    @classmethod
    def load(cls,path):
        with np.load(path,allow_pickle=False) as z:
            if str(z['protocol'])!=cls.version:raise ValueError('Unsupported RAD network version')
            n=len(z['points_m']);ng=len(z['G_indptr'])-1
            F=csr_matrix((z['F_data'],z['F_indices'],z['F_indptr']),shape=(n,n));G=csr_matrix((z['G_data'],z['G_indices'],z['G_indptr']),shape=(ng,ng))
            flux=z['flux_w_m2'];net=cls(z['points_m'],z['normals'],z['area_m2'],z['building_index'],z['group'],z['sky_view'],F,G,flux[:,:,0],flux[:,:,1],z['hoy'],json.loads(str(z['metadata'])))
            net.longwave=flux[:,:,2].copy();net.path=str(Path(path).resolve());return net


def build_network(n,sky_matrix,*,sample=.3,radius_m=200.,ground_reflectance=.2,ground_emissivity=.2,
                  ignore_window_longwave=False,options=None,seed=42,show_progress=True):
    if not 0<sample<=1 or radius_m<=0 or not 0<=ground_reflectance<=1 or not 0<=ground_emissivity<=1:
        raise ValueError('Invalid radiation sampling, radius or ground optical parameters')
    if not n.buildings or not n.climate:raise ValueError('Initialize buildings and weather before RAD')
    if not all(b.archetype for b in n.buildings):raise ValueError('Run sampleInstance before radSimulation')
    gdf=prepare_buildings(Path(n._shp),'index',n._height_field)
    if not gdf.crs or not gdf.crs.is_projected or any(a.unit_name.lower() not in ('metre','meter') for a in gdf.crs.axis_info[:2]):
        raise ValueError('Radiation distance/area requires a projected CRS in metres')
    if len(gdf)!=len(n.buildings):raise ValueError('Geometry/building inventory mismatch')
    models=optical_models(n,options);meshes=load_building_meshes(gdf,'index',n._height_field)
    pts=[];norm=[];areas=[];owners=[];groups=[];triangles=[];rho=[]
    for b,(mesh,m,geometry) in enumerate(zip(meshes,models,gdf.geometry)):
        surfaces={s['name']:s for s in m['surfaces']}
        reflect={}
        for offset,names in [(0,('wall','window')),(1,('roof','skylight'))]:
            total=sum(surfaces[k]['area_m2'] for k in names if k in surfaces)
            reflect[offset]=sum(surfaces[k]['area_m2']*surfaces[k]['reflectance'] for k in names if k in surfaces)/max(total,1e-12)
        polygons=list(geometry.geoms) if geometry.geom_type=='MultiPolygon' else [geometry]
        heights=facade_sample_heights(n.buildings[b].height,n.buildings[b].StoryHeight)
        for polygon in polygons:
            for ring in [polygon.exterior,*polygon.interiors]:
                xy=np.asarray(ring.coords);ccw=ring.is_ccw
                for a,c in zip(xy[:-1],xy[1:]):
                    edge=c-a;length=np.linalg.norm(edge)
                    if length<1e-8:continue
                    outward=np.array([edge[1],-edge[0]])/length
                    if not ccw:outward=-outward
                    if ring!=polygon.exterior:outward=-outward
                    normal=np.r_[outward,0.]
                    for z in heights:
                        pts.append(np.r_[(a+c)/2+outward*.03,z]);norm.append(normal);areas.append(length*n.buildings[b].height/len(heights));owners.append(b);groups.append(2*b);rho.append(reflect[0])
                    v=[[*a,0],[*c,0],[*c,n.buildings[b].height],[*a,n.buildings[b].height]]
                    triangles.extend([[v[0],v[1],v[2]],[v[0],v[2],v[3]]])
            point=polygon.representative_point();pts.append([point.x,point.y,n.buildings[b].height+.03]);norm.append([0,0,1]);areas.append(polygon.area);owners.append(b);groups.append(2*b+1);rho.append(reflect[1])
            for t in constrained_delaunay_triangles(polygon).geoms:
                if polygon.covers(t):triangles.append([[x,y,n.buildings[b].height] for x,y in list(t.exterior.coords)[:3]])
    bounds=gdf.total_bounds;side=max(1,int(round(100*sample)))
    dx,dy=(bounds[2]-bounds[0])/side,(bounds[3]-bounds[1])/side
    union=gdf.geometry.union_all();ground=[]
    for y in bounds[1]+(np.arange(side)+.5)*dy:
        for x in bounds[0]+(np.arange(side)+.5)*dx:
            if not union.covers(Point(x,y)):ground.append([x,y,.03])
    if not ground:raise ValueError('No ground sample remains; increase sample')
    geometric_ground_area=(bounds[2]-bounds[0])*(bounds[3]-bounds[1])-union.area
    # Representative point areas close the SAME exposed-area ledger used by UHI.
    ground_area=float(n.site.AreaGround+n.site.AreaRoad+n.site.AreaLandscape+n.site.AreaWater)
    for p in ground:
        pts.append(p);norm.append([0,0,1]);areas.append(ground_area/len(ground));owners.append(-1);groups.append(2*len(models));rho.append(ground_reflectance)
    pts,norm,areas,owners,groups,rho=map(np.asarray,(pts,norm,areas,owners,groups,rho));owners=owners.astype(int);groups=groups.astype(int)
    hoy=np.concatenate([c.HOY.astype(int) for c in n.climate]);sky_values=np.asarray(sky_matrix,dtype=float)[:,hoy]*1000
    rays=load_sun_vectors();rays=rays/np.linalg.norm(rays,axis=1)[:,None]
    from WEA.epw import TREGENZA_COEFFICIENTS,TREGENZA_PATCHES_PER_ROW
    omega=np.repeat(TREGENZA_COEFFICIENTS,TREGENZA_PATCHES_PER_ROW)
    exact_buildings=_sample_indices(len(models),sample,seed);exact=(owners<0)|np.isin(owners,exact_buildings)
    direct=np.zeros((len(pts),len(hoy)),dtype=np.float32);sky=np.zeros(len(pts))
    if show_progress:print(f'RAD network: {len(pts)} points; ground {side}x{side}, retained {len(ground)}; exact buildings {len(exact_buildings)}/{len(models)}',flush=True)
    with Visibility(triangles) as service:
        ids=np.flatnonzero(exact)
        for begin in range(0,len(ids),256):
            idx=ids[begin:begin+256];cos=np.maximum(0,norm[idx]@rays.T)
            vis=service.query(np.repeat(pts[idx],len(rays),axis=0),np.tile(rays,(len(idx),1))).reshape(len(idx),len(rays))
            response=cos*vis;direct[idx]=(response@sky_values).astype(np.float32);sky[idx]=np.clip(response@(omega/np.pi),0,1)
        # Retain previous sample meaning: exact building fraction, remaining exposure interpolated.
        categories=np.argmax(norm@np.array([[1,0,0],[-1,0,0],[0,1,0],[0,-1,0],[0,0,1]]).T,axis=1)
        for kind in range(5):
            src=np.flatnonzero(exact&(owners>=0)&(categories==kind));dest=np.flatnonzero(~exact&(categories==kind))
            if not len(dest):continue
            if not len(src):raise ValueError('No exact samples for an orientation; increase sample')
            dist,near=cKDTree(pts[src]).query(pts[dest],k=min(3,len(src)))
            if dist.ndim==1:dist=dist[:,None];near=near[:,None]
            weight=1/np.maximum(dist,1e-6)**2;weight/=weight.sum(axis=1)[:,None]
            direct[dest]=np.sum(direct[src[near]]*weight[:,:,None],axis=1);sky[dest]=np.sum(sky[src[near]]*weight,axis=1)
        if show_progress:print('RAD network: sky exposure complete; constructing finite, blocked surface exchanges',flush=True)
        C,F,info=reciprocal_exchange(pts,norm,areas,sky,owners,service,radius_m)
    # Static reflected shortwave uses PRIMARY irradiation only: exactly one bounce.
    reflected=np.asarray(F@(rho[:,None]*direct),dtype=np.float32)
    ng=2*len(models)+1;co=C.tocoo();Cg=coo_matrix((co.data,(groups[co.row],groups[co.col])),shape=(ng,ng)).tocsr()
    group_area=np.bincount(groups,weights=areas,minlength=ng)
    G=Cg.multiply((1/np.maximum(group_area,1e-30))[:,None]).tocsr()
    meta=dict(protocol=RadiationNetwork.version,energy_options=options or {},optical_signature=optical_signature(n,options),sample=sample,radius_m=radius_m,
        ground_grid_side=side,ground_points=len(ground),ground_reflectance=ground_reflectance,ground_emissivity=ground_emissivity,
        ground_area_m2=ground_area,geometric_ground_area_m2=geometric_ground_area,exact_buildings=exact_buildings.tolist(),building_count=len(models),ignore_window_longwave=ignore_window_longwave,
        shortwave_reflection_passes=1,longwave_updates=0,channels=['primary_shortwave','one_bounce_shortwave','surface_longwave'],units='W/m2',
        sky='145-patch input; check sky-matrix provenance',longwave='Grey diffuse radiosity; window non-sky absorption optionally omitted as explicit sink',**info)
    meta['max_view_sum']=float(np.max(np.asarray(F.sum(axis=1)).ravel()))
    meta['sw_reflection_sent_wh']=float(np.sum(areas[:,None]*rho[:,None]*direct))
    meta['sw_reflection_received_wh']=float(np.sum(areas[:,None]*reflected))
    meta['sw_reflection_escape_wh']=meta['sw_reflection_sent_wh']-meta['sw_reflection_received_wh']
    if show_progress:print(f'RAD network: {F.nnz//2} links, one SW reflection complete',flush=True)
    return RadiationNetwork(pts,norm,areas,owners,groups,sky,F,G,direct,reflected,hoy,meta)
