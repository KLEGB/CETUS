"""V9.1 diffuse spectral transport: emission + exactly one reflection.
The finite-patch view factors are reused; no extra geometric ray tracing.
"""
import numpy as np
from UHI.spectral import blackbody,resolve_material,surface_spec,atmosphere,SIGMA

class SpectralNetwork:
    def __init__(self,net,models,config,ground_defaults,weights):
        if net.meta.get('ignore_window_longwave'):raise ValueError('Spectral longwave requires ignore_window_longwave=False')
        self.net=net;self.config=config;self.bands=config['bands'];self.weights=np.asarray(weights)
        groups=[];areas=[];eps=[];rho=[];tau=[];carriers=[];self.slices=[];self.face_names=[]
        for i,m in enumerate(models):
            start=len(groups)
            for s in m['surfaces']:
                groups.append(2*i+int(s['kind'] in ('roof','skylight')));areas.append(0 if s['kind']=='floor' else s['area_m2'])
                self.face_names.append(s['name'])
                if s['kind']=='floor':
                    e=r=t=np.zeros(len(self.bands))
                else:
                    e,r,t=resolve_material(surface_spec(config,s['name'],i),s['emissivity'],self.bands,config['reference_temperature_k'])
                    if s['emissivity']<=0:raise ValueError('Positive carrier emissivity required')
                    s['longwave_bands']=[dict(lower_um=b['lower_um'],upper_um=b['upper_um'],emissivity=float(v)) for b,v in zip(self.bands,e)]
                eps.append(e);rho.append(r);tau.append(t);carriers.append(max(s['emissivity'],1e-30))
            self.slices.append((start,len(groups)))
        self.groups=np.array(groups);self.areas=np.array(areas);self.eps=np.array(eps);self.rho=np.array(rho);self.tau=np.array(tau);self.carriers=np.array(carriers)
        self.geps=[];self.grho=[];self.gtau=[]
        for name,d in zip(('ground','land','water'),ground_defaults):
            e,r,t=resolve_material(surface_spec(config,name),float(d),self.bands,config['reference_temperature_k'])
            self.geps.append(e);self.grho.append(r);self.gtau.append(t)
        self.geps=np.array(self.geps);self.grho=np.array(self.grho);self.gtau=np.array(self.gtau)
        self.group_eps=self.aggregate(self.eps);self.group_rho=self.aggregate(self.rho);self.group_tau=self.aggregate(self.tau)
        self.group_eps[-1]=self.weights@self.geps;self.group_rho[-1]=self.weights@self.grho;self.group_tau[-1]=self.weights@self.gtau
        self.last=None;self.solvers=None
        if config.get('reflection_mode','one_bounce')=='radiosity':
            from scipy.sparse import eye,diags
            from scipy.sparse.linalg import factorized
            self.solvers=[factorized((eye(len(net.group_area))-diags(self.group_rho[:,k])@net.group_exchange).tocsc()) for k in range(len(self.bands))]
        net.meta['longwave_9_1']={'bands':self.bands,'reflection_passes':('converged' if self.solvers else 1),'sky_mode':config.get('sky_mode','prescribed'),
          'emissivity_rule':'Planck-weighted integral < 1; spectral coefficients need not sum to < 1',
          'truncation':'Second reflected flux is an explicit unresolved boundary ledger, not surface heat',
          'material_config':config,'planck_interpolation_step_k':.05}
    def aggregate(self,values):
        return np.stack([np.bincount(self.groups,weights=self.areas*values[:,k],minlength=len(self.net.group_area))/self.net.group_area for k in range(len(self.bands))],axis=-1)
    def temperature_array(self,temperatures):
        return np.array([temperatures[i][name] for i,(start,end) in enumerate(self.slices) for name in self.face_names[start:end]])
    def response(self,temperatures,ambient_c,sky_ir):return SpectralResponse(self,temperatures,ambient_c,sky_ir)
    def audit(self,temperatures,ground,air,actual_building_lw):
        """Radiative identity + independent comparison with Go surface net exchange."""
        x=self.last;net=self.net
        # This identity is exact for the explicitly truncated transport.
        emitted=np.sum(net.group_area[:,None]*x['emission'],axis=0)
        env_in=np.sum(net.group_area[:,None]*x['environment'],axis=0)
        absorbed=np.sum(net.group_area[:,None]*self.group_eps*x['incident'],axis=0)
        escaped=np.sum(net.group_area[:,None]*(1-net.view_sum[:,None])*x['outgoing'],axis=0)
        second=np.sum(net.group_area[:,None]*self.group_rho*x['bounce_incident'],axis=0)
        transmitted=np.sum(net.group_area[:,None]*self.group_tau*x['incident'],axis=0)
        residual=emitted+env_in-absorbed-escaped-second-transmitted
        # Go uses its own Planck table, independently normalized via carrier eps.
        face_emitted=self.eps*blackbody(self.temperature_array(temperatures)+273.15,self.bands)
        predicted=float(np.sum(self.areas[:,None]*(face_emitted-self.eps*x['incident'][self.groups])))
        A=net.group_area
        sky_escape=np.sum(A[:,None]*net.group_sky[:,None]*x['outgoing'],axis=0)
        space_escape=sky_escape*x['atmospheric_tau']
        return dict(lw_spectral_transport_residual_w=float(residual.sum()),
            lw_spectral_max_band_residual_w=float(max(abs(residual))),
            lw_spectral_engine_difference_w=actual_building_lw-predicted,
            lw_second_reflection_unresolved_w=float(second.sum()),
            lw_material_transmission_out_w=float(transmitted.sum()),
            lw_sky_escape_w=float(sky_escape.sum()),lw_space_escape_w=float(space_escape.sum()),
            lw_atmosphere_absorbed_upward_w=float((sky_escape-space_escape).sum()),
            lw_external_incoming_w=float(env_in.sum()),lw_outgoing_far_and_sky_w=float(escaped.sum()),
            lw_spectral_bands=[dict(name=b['name'],emitted_w=float(emitted[k]),environment_in_w=float(env_in[k]),
                absorbed_w=float(absorbed[k]),sky_escape_w=float(sky_escape[k]),space_escape_w=float(space_escape[k]),
                second_reflection_unresolved_w=float(second[k]),material_transmission_out_w=float(transmitted[k]),
                transport_residual_w=float(residual[k])) for k,b in enumerate(self.bands)],
            lw_atmospheric_optical_depth_scale=float(x['scale']))

class SpectralResponse:
    def __init__(self,spectral,temperatures,ambient_c,sky_ir):
        self.s=spectral;self.temperatures=temperatures
        s=spectral
        face_emission=s.eps*blackbody(s.temperature_array(temperatures)+273.15,s.bands)
        self.fixed=s.aggregate(face_emission)
        sky,self.atmospheric_tau,self.scale=atmosphere(s.config,ambient_c,sky_ir)
        self.sky=s.net.group_sky[:,None]*sky
    def boundary(self,ground,air):
        s=self.s;net=s.net
        E=self.fixed.copy();tg=np.array([t for w,t in ground])
        gb=blackbody(tg+273.15,s.bands)
        E[-1]=np.sum(np.array([w for w,t in ground])[:,None]*s.geps*gb,axis=0)
        env=self.sky+net.far[:,None]*blackbody(air+273.15,s.bands)
        direct=net.group_exchange@E+env
        reflected=s.group_rho*direct
        bounce=net.group_exchange@reflected
        H=direct+bounce
        if s.solvers:
            J=np.stack([solve(E[:,k]+s.group_rho[:,k]*env[:,k]) for k,solve in enumerate(s.solvers)],axis=-1)
            H=net.group_exchange@J+env
            reflected=J-E
            bounce=np.zeros_like(H)
        flat=np.sum(s.eps*H[s.groups],axis=1)/s.carriers
        flat[s.areas==0]=0
        ground_net=np.sum(s.geps*(H[-1]-gb),axis=1)
        s.last=dict(emission=E,environment=env,incident=H,outgoing=E+reflected,bounce_incident=bounce,
            atmospheric_tau=self.atmospheric_tau,scale=self.scale)
        return [flat[a:b].tolist() for a,b in s.slices],ground_net,(E+reflected).sum(axis=1),(net.group_exchange@(E+reflected)).sum(axis=1)
