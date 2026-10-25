'use strict';
const $=id=>document.getElementById(id);
const names={default:'默认',residential:'住宅',office:'办公',commercial:'商业',hotel:'酒店',medical:'医疗',school:'教育'};
const clone=x=>structuredClone(x);
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const fmt=x=>Number.isFinite(+x)?Number((+x).toPrecision(6)).toString():String(x);
let data=null,selected=new Set(),draft={},draftStd={},dirty=new Set(),pendingKind=null;
let job=null,offset=0,timer=null,result=null,urbanDirty=false;
let map={scale:1,x:0,y:0,width:0,height:0,paths:[],box:false,drag:null};

async function api(path,payload){
  const options=payload===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)};
  const response=await fetch('/api/'+path,options);
  const body=await response.json();
  if(!response.ok)throw new Error(body.error||`请求失败 (${response.status})`);
  return body;
}
function message(text,title='提示'){$('message-title').textContent=title;$('message-text').textContent=text;$('message-dialog').showModal();}
$('message-close').onclick=()=>$('message-dialog').close();
async function action(fn,text='正在处理…'){
  $('busy-text').textContent=text;$('busy').hidden=false;
  try{return await fn();}catch(e){message(e.message,'操作未完成');return null;}finally{$('busy').hidden=true;}
}
function log(text){const el=$('logs');el.textContent+='\n'+text;el.scrollTop=el.scrollHeight;}
function discardResult(){result=null;$('results').hidden=true;$('run-state').textContent='方案已修改，待计算';}
async function refresh({fit=false}={}){
  data=await api('state');selected=new Set(data.state.selected);
  $('building-count').textContent=data.buildings.length;
  $('selected-count').textContent=selected.size;
  $('map-empty').hidden=data.geometry.features.length>0;
  $('map-title').textContent=data.geometry.features.length?'建筑轮廓与选择':'城市建筑';
  $('shp-path').value=data.paths.shp||'';$('weather-path').value=data.paths.weather||'';$('rad-path').value=data.paths.rad||'';
  $('project-label').textContent=data.paths.project||'未保存的方案';
  $('project-label').title=data.paths.project||'';
  $('climate-zone').value=data.location.climate_zone||'';
  if(data.state.weather_loaded){$('latitude').value=data.location.latitude;$('longitude').value=data.location.longitude;}
  for(const [id,key] of [['start-date','start_date'],['end-date','end_date'],['random-ratio','random_ratio'],['seed','seed'],['iteration','iteration'],['sample','sample'],['load-tolerance','load_tolerance']])$(id).value=data.state[key];
  $('map-legend').replaceChildren();
  for(const [kind,color] of Object.entries(data.colors)){
    const item=document.createElement('span'),dot=document.createElement('i');dot.className='swatch';dot.style.background=color;
    item.append(dot,document.createTextNode(names[kind]));$('map-legend').append(item);
  }
  renderUrban();renderBuilding();if(fit)fitMap();else drawMap();
  if(data.active_job&&!job){job=data.active_job;offset=0;running(true);schedulePoll();}
}
async function picker(kind){const p=await api('dialog',{kind});return p.path;}
document.querySelectorAll('.browse').forEach(button=>button.onclick=()=>action(async()=>{
  const path=await picker(button.dataset.kind);if(path)$(button.dataset.target).value=path;
},'请选择文件…'));
$('import-shp').onclick=()=>action(async()=>{
  const path=$('shp-path').value.trim();if(!path)throw Error('请填写或选择 SHP 路径');
  let response=await api('import/shp',{path});
  if(response.needs_height){
    $('busy').hidden=true;
    const field=window.prompt('未自动识别高度列。请输入高度列名称（单位：米）。\n可用字段：'+response.columns.join('、'));
    if(field===null){await refresh();return;}
    $('busy').hidden=false;
    response=await api('import/shp',{path,height_field:field});
  }
  discardResult();await refresh({fit:true});
  log(`建筑导入完成。识别为 ${response.detected_lcz}，已套用 ${response.applied_lcz}。全部建筑初始化为默认类型。`);
  if(response.detected_lcz!==response.applied_lcz)message(`识别结果为 ${response.detected_lcz}；当前仅有 LCZ1–5 模板，已使用 ${response.applied_lcz}。可在 Urban Settings 中修改。`);
},'正在读取建筑并计算场地形态…');
$('import-weather').onclick=()=>action(async()=>{
  const path=$('weather-path').value.trim();if(!path)throw Error('请选择气象文件');
  const params={path,start_date:$('start-date').value.trim(),end_date:$('end-date').value.trim(),climate_zone:$('climate-zone').value||null};
  if(path.toLowerCase().endsWith('.csv')){
    if($('latitude').value===''||$('longitude').value==='')throw Error('CSV 建网需要在气象补充设置中填写经纬度');
    params.latitude=Number($('latitude').value);params.longitude=Number($('longitude').value);
  }
  await api('import/weather',params);discardResult();await refresh();
  log('气象与计算时段已载入。更改日期后请重新导入气象。现有建筑参数保留，可重新套用气候区对应的类型模板。');
},'正在读取气象…');
$('import-rad').onclick=()=>action(async()=>{await api('import/rad',{path:$('rad-path').value.trim()});discardResult();await refresh();log('RAD 网络设置已更新。');});
$('save').onclick=()=>action(async()=>{
  if(dirty.size||urbanDirty)throw Error('参数尚未 Apply，请先应用或重新载入参数后保存');
  await saveOptions();
  const path=await picker('save');if(!path)return;
  const response=await api('save',{path});await refresh();log('方案与依赖文件已保存：'+response.path);
},'请选择 Neighborhood 保存位置…');
$('load').onclick=()=>action(async()=>{
  const path=await picker('load');if(!path)return;
  await api('load',{path});discardResult();await refresh({fit:true});log('Neighborhood 已载入。');
},'载入 Neighborhood…');

async function setSelection(ids){
  if(dirty.size)throw Error('建筑参数尚未 Apply，请先应用后再更改选择集');
  const response=await api('select',{ids});selected=new Set(response.selected);data.state.selected=response.selected;
  $('selected-count').textContent=selected.size;renderBuilding();drawMap();
}
$('random-select').onclick=()=>action(async()=>{
  if(dirty.size)throw Error('建筑参数尚未 Apply，请先应用后再更改选择集');
  const response=await api('select',{random:true,ratio:Number($('random-ratio').value),seed:Number($('seed').value)});
  selected=new Set(response.selected);data.state.selected=response.selected;
  $('selected-count').textContent=selected.size;renderBuilding();drawMap();
});
$('select-all').onclick=()=>action(()=>setSelection(data.buildings.map(b=>b.id)));
$('select-none').onclick=()=>action(()=>setSelection([]));
$('fit-map').onclick=()=>fitMap();
$('box-mode').onclick=()=>{map.box=!map.box;$('box-mode').setAttribute('aria-pressed',String(map.box));$('map').classList.toggle('box',map.box);};

function coords(geometry){return geometry.type==='Polygon'?[geometry.coordinates]:geometry.coordinates;}
function screen(point){return [point[0]*map.scale+map.x,-point[1]*map.scale+map.y];}
function pathFor(geometry,dx=0,dy=0){
  const path=new Path2D();for(const polygon of coords(geometry))for(const ring of polygon){
    ring.forEach((point,i)=>{const p=screen([point[0]+dx,point[1]+dy]);i?path.lineTo(...p):path.moveTo(...p);});path.closePath();
  }return path;
}
function fitMap(){
  if(!data)return;const points=data.geometry.features.flatMap(f=>coords(f.geometry).flat(2));
  resizeMap(false);if(!points.length){drawMap();return;}
  let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
  for(const [x,y]of points){minX=Math.min(minX,x);maxX=Math.max(maxX,x);minY=Math.min(minY,y);maxY=Math.max(maxY,y);}
  const maxHeight=Math.max(...data.geometry.features.map(f=>f.properties.height),1);
  minX-=maxHeight*.8;maxY+=maxHeight*.8;
  map.scale=Math.min((map.width-90)/Math.max(1,maxX-minX),(map.height-90)/Math.max(1,maxY-minY));
  map.x=map.width/2-(minX+maxX)/2*map.scale;map.y=map.height/2+(minY+maxY)/2*map.scale;
  drawMap();
}
function resizeMap(draw=true){
  const canvas=$('map'),rect=$('map-stage').getBoundingClientRect();map.width=rect.width;map.height=rect.height;
  const dpr=window.devicePixelRatio||1;canvas.width=Math.round(rect.width*dpr);canvas.height=Math.round(rect.height*dpr);
  canvas.getContext('2d').setTransform(dpr,0,0,dpr,0,0);if(draw)drawMap();
}
function drawMap(){
  if(!data)return;const ctx=$('map').getContext('2d');ctx.clearRect(0,0,map.width,map.height);map.paths=[];
  // Fixed SE sun at 45 degrees: shadows extend NW by exactly height metres.
  ctx.fillStyle='#45554720';
  for(const f of data.geometry.features){
    const d=f.properties.height/Math.SQRT2;ctx.fill(pathFor(f.geometry,-d,d),'evenodd');
    for(const polygon of coords(f.geometry)){
      const ring=polygon[0];for(let i=1;i<ring.length;i++){
        const a=screen(ring[i-1]),b=screen(ring[i]),c=screen([ring[i][0]-d,ring[i][1]+d]),e=screen([ring[i-1][0]-d,ring[i-1][1]+d]);
        ctx.beginPath();ctx.moveTo(...a);ctx.lineTo(...b);ctx.lineTo(...c);ctx.lineTo(...e);ctx.closePath();ctx.fill();
      }
    }
  }
  for(const f of data.geometry.features){
    const path=pathFor(f.geometry);ctx.fillStyle=data.colors[f.properties.kind]||data.colors.default;ctx.fill(path,'evenodd');
    ctx.strokeStyle=selected.has(f.properties.id)?'#203f32':'#ffffffae';ctx.lineWidth=selected.has(f.properties.id)?2.2:.65;ctx.stroke(path);
    if(selected.has(f.properties.id)){ctx.save();ctx.globalAlpha=.2;ctx.fillStyle='#fff';ctx.fill(path,'evenodd');ctx.restore();}
    map.paths.push({path,feature:f});
  }
  const drag=map.drag;if(drag&&drag.type==='box'){
    ctx.fillStyle='#315f4c18';ctx.strokeStyle='#315f4c';ctx.lineWidth=1;ctx.setLineDash([4,3]);
    ctx.fillRect(drag.start[0],drag.start[1],drag.last[0]-drag.start[0],drag.last[1]-drag.start[1]);
    ctx.strokeRect(drag.start[0],drag.start[1],drag.last[0]-drag.start[0],drag.last[1]-drag.start[1]);ctx.setLineDash([]);
  }
  if(data.geometry.features.length){
    const raw=80/map.scale,power=10**Math.floor(Math.log10(raw)),distance=[1,2,5,10].map(x=>x*power).find(x=>x>=raw);
    const length=distance*map.scale;ctx.strokeStyle='#6c7e69';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(map.width-30-length,map.height-25);ctx.lineTo(map.width-30,map.height-25);ctx.stroke();ctx.fillStyle='#6c7e69';ctx.font='10px Segoe UI';ctx.textAlign='right';ctx.fillText(`${fmt(distance)} m`,map.width-30,map.height-31);
  }
}
function mapPoint(e){const r=$('map').getBoundingClientRect();return [e.clientX-r.left,e.clientY-r.top];}
function hit(point){const ctx=$('map').getContext('2d');return [...map.paths].reverse().find(p=>ctx.isPointInPath(p.path,point[0]*(window.devicePixelRatio||1),point[1]*(window.devicePixelRatio||1),'evenodd'));}
$('map').addEventListener('pointerdown',e=>{
  if(e.button!==0||!data||job)return;const p=mapPoint(e);
  map.drag={start:p,last:p,origin:[map.x,map.y],type:e.shiftKey||map.box?'box':'pan',add:e.shiftKey||e.ctrlKey,moved:false};
  $('map').setPointerCapture(e.pointerId);$('map-tooltip').hidden=true;
});
$('map').addEventListener('pointermove',e=>{
  const p=mapPoint(e);if(map.drag){
    const d=map.drag;d.last=p;d.moved=Math.hypot(p[0]-d.start[0],p[1]-d.start[1])>4;
    if(d.type==='pan'){map.x=d.origin[0]+p[0]-d.start[0];map.y=d.origin[1]+p[1]-d.start[1];}drawMap();
  }else{
    const item=hit(p);const tip=$('map-tooltip');tip.hidden=!item;
    if(item){const f=item.feature.properties;tip.textContent=`${f.id} · ${names[f.kind]}\n高度 ${fmt(f.height)} m · GFA ${fmt(f.gfa)} m²`;tip.style.left=Math.min(p[0]+15,map.width-200)+'px';tip.style.top=Math.max(0,p[1]-65)+'px';}
  }
});
function polygonHitsBox(geometry,bounds){
  const [x0,y0,x1,y1]=bounds;
  const inside=([x,y])=>x>=x0&&x<=x1&&y>=y0&&y<=y1;
  const crossing=(a,b)=>{
    let lo=0,hi=1;const dx=b[0]-a[0],dy=b[1]-a[1];
    for(const [p,q]of [[-dx,a[0]-x0],[dx,x1-a[0]],[-dy,a[1]-y0],[dy,y1-a[1]]]){
      if(p===0){if(q<0)return false;continue;}const t=q/p;if(p<0)lo=Math.max(lo,t);else hi=Math.min(hi,t);if(lo>hi)return false;
    }return true;
  };
  for(const poly of coords(geometry)){const ring=poly[0].map(screen);if(ring.some(inside))return true;for(let i=1;i<ring.length;i++)if(crossing(ring[i-1],ring[i]))return true;}
  const path=pathFor(geometry),ctx=$('map').getContext('2d'),dpr=window.devicePixelRatio||1;
  return [[x0,y0],[x1,y0],[x1,y1],[x0,y1]].some(p=>ctx.isPointInPath(path,p[0]*dpr,p[1]*dpr,'evenodd'));
}
$('map').addEventListener('pointerup',e=>{
  if(!map.drag)return;const d=map.drag,p=mapPoint(e);map.drag=null;
  const next=d.add?new Set(selected):new Set();
  if(d.type==='box'&&d.moved){
    const b=[Math.min(d.start[0],p[0]),Math.min(d.start[1],p[1]),Math.max(d.start[0],p[0]),Math.max(d.start[1],p[1])];
    for(const f of data.geometry.features)if(polygonHitsBox(f.geometry,b))next.add(f.properties.id);
  }else if(!d.moved){const f=hit(p);if(f){const id=f.feature.properties.id;if(d.add&&next.has(id))next.delete(id);else next.add(id);}}
  else{drawMap();return;}
  action(()=>setSelection([...next]));
});
$('map').addEventListener('pointercancel',()=>{map.drag=null;drawMap();});
$('map').addEventListener('pointerleave',()=>{$('map-tooltip').hidden=true;});
$('map').addEventListener('wheel',e=>{
  e.preventDefault();const p=mapPoint(e),factor=Math.exp(-Math.max(-100,Math.min(100,e.deltaY))*.003);
  if(map.scale*factor<.00001||map.scale*factor>500)return;
  map.x=p[0]-(p[0]-map.x)*factor;map.y=p[1]-(p[1]-map.y)*factor;map.scale*=factor;drawMap();
},{passive:false});
new ResizeObserver(()=>resizeMap()).observe($('map-stage'));

function renderBuilding(){
  dirty=new Set();pendingKind=null;draft={};draftStd={};
  const chosen=data.buildings.filter(b=>selected.has(b.id));
  $('edit-count').textContent=chosen.length?`${chosen.length} 栋已选`:'未选择建筑';
  $('edit-hint').textContent=chosen.length?'正在编辑选中建筑。类型模板在 Apply 后生效。':'在地图上选择建筑，编辑参数或套用类型模板。';
  $('building-apply').disabled=!chosen.length;
  $('type-buttons').replaceChildren();
  for(const kind of Object.keys(names).filter(k=>k!=='default')){
    const button=document.createElement('button');button.textContent=names[kind];button.style.setProperty('--type-color',data.colors[kind]);button.disabled=!chosen.length;
    button.onclick=()=>{
      const t=data.templates[kind];draft=clone(t.values);draftStd=clone(t.uncertainty);pendingKind=kind;dirty=new Set(data.fields.map(f=>f.key));
      $('type-buttons').querySelectorAll('button').forEach(b=>b.classList.toggle('active',b===button));
      $('edit-hint').textContent=`待应用 ${names[kind]} 模板至 ${selected.size} 栋建筑`;buildingRows();
    };$('type-buttons').append(button);
  }
  for(const field of data.fields){
    const k=field.key,values=chosen.map(b=>b.parameters.values?.[k]??b.values[k]);
    const stds=chosen.map(b=>b.parameters.uncertainty?.[k]??0);
    draft[k]=values.length&&values.every(x=>same(x,values[0]))?clone(values[0]):null;
    draftStd[k]=stds.length&&stds.every(x=>same(x,stds[0]))?stds[0]:null;
  }buildingRows();
}
function buildingRows(){
  $('building-fields').replaceChildren();
  for(const field of data.fields){
    const k=field.key,row=document.createElement('tr');row.classList.toggle('dirty',dirty.has(k));
    const label=document.createElement('td'),small=document.createElement('small');label.textContent=field.label;small.textContent=k;label.append(small);
    const value=document.createElement('td'),unit=document.createElement('td'),std=document.createElement('td');unit.textContent=field.unit;
    const wrap=document.createElement('div');wrap.className='value-wrap';
    if(Array.isArray(draft[k])){const button=document.createElement('button');button.className='curve-button schedule-label';button.textContent='∿ 24 小时曲线';button.onclick=()=>openSchedule(k);wrap.append(button);}
    else{const input=document.createElement('input');input.type='number';input.step='any';input.value=draft[k]??'';input.placeholder=selected.size?'多个值':'—';input.disabled=!selected.size;
      input.setAttribute('aria-label',field.label+' value');input.oninput=()=>{draft[k]=input.value===''?null:Number(input.value);dirty.add(k);row.classList.add('dirty');};wrap.append(input);}
    if(field.schedule&&!Array.isArray(draft[k])){const curve=document.createElement('button');curve.textContent='∿';curve.title='编辑每天重复的 24 小时曲线';curve.className='curve-button';curve.disabled=!selected.size;curve.onclick=()=>openSchedule(k);wrap.append(curve);}
    value.append(wrap);
    const uncertainty=document.createElement('input');uncertainty.type='number';uncertainty.min='0';uncertainty.step='any';uncertainty.value=draftStd[k]??'';uncertainty.placeholder='多个值';uncertainty.disabled=!selected.size;
    uncertainty.setAttribute('aria-label',field.label+' uncertainty');uncertainty.oninput=()=>{draftStd[k]=uncertainty.value===''?null:Number(uncertainty.value);dirty.add(k);row.classList.add('dirty');};std.append(uncertainty);row.append(label,value,unit,std);$('building-fields').append(row);
  }
}
$('building-apply').onclick=()=>action(async()=>{
  if(!dirty.size)return;
  const values={},uncertainty={};
  for(const key of dirty){
    if(draft[key]===null&&draftStd[key]===null)throw Error(`${key} 没有有效的参数值或标准差`);
    if(draft[key]!==null)values[key]=draft[key];if(draftStd[key]!==null)uncertainty[key]=draftStd[key];
  }
  await api('buildings',{values,uncertainty,kind:pendingKind});discardResult();await refresh();log('建筑参数已 Apply。影响光学或几何的编辑需要重新建网。');
});
const urbanUnits={a:'-',k:'-',z0:'m',zRef:'m',zRefd:'m',z0d:'m',HAir:'m',GroundTemperature:'°C',UDeepLand:'W/(m²·K)',UDeepGround:'W/(m²·K)',UDeepWater:'W/(m²·K)',CLand:'J/(m²·K)',CGround:'J/(m²·K)',CWater:'J/(m²·K)',dt:'s',ReferenceHeight:'m'};
function renderUrban(){
  urbanDirty=false;$('lcz-badge').textContent=data.state.lcz;$('lcz-buttons').replaceChildren();
  for(const label of ['LCZ1','LCZ2','LCZ3','LCZ4','LCZ5','LCZx']){
    const button=document.createElement('button');button.textContent=label;button.classList.toggle('active',data.state.lcz===label);
    button.onclick=()=>action(async()=>{
      if(label==='LCZx')await api('urban',{restore:true});else await api('urban',{lcz:label});
      discardResult();await refresh();
    });$('lcz-buttons').append(button);
  }
  $('lcz-note').textContent=data.state.detected_lcz?`形态识别：${data.state.detected_lcz} · 手动修改后切换到 LCZx。`:'切换 LCZ 立即套用城市模板。';
  $('urban-fields').replaceChildren();
  const groups=[['覆盖与场地','cover',{FVC:data.urban.FVC,FWC:data.urban.FWC,...data.urban.site}],['模型参数','setting',data.urban.setting]];
  for(const [title,group,fields] of groups){
    const heading=document.createElement('p');heading.className='urban-group-title';heading.textContent=title;$('urban-fields').append(heading);
    for(const [key,value] of Object.entries(fields)){
      const row=document.createElement('div');row.className='urban-row';const label=document.createElement('label');label.textContent=key;
      const unit=document.createElement('small');unit.textContent=urbanUnits[key]||(group==='cover'?'0–1':'');label.append(unit);
      let input;
      if(typeof value==='boolean'){input=document.createElement('select');for(const x of ['true','false']){const option=document.createElement('option');option.value=x;option.textContent=x;input.append(option);}input.value=String(value);}
      else{input=document.createElement('input');input.type=typeof value==='number'?'number':'text';if(input.type==='number')input.step='any';input.value=Array.isArray(value)?JSON.stringify(value):value===null?'null':value;}
      input.setAttribute('aria-label',key);input.dataset.key=key;input.dataset.group=group;input.dataset.type=Array.isArray(value)?'array':value===null?'null':typeof value;
      input.oninput=()=>{urbanDirty=true;$('lcz-badge').textContent='LCZx · 待应用';$('lcz-buttons').querySelectorAll('button').forEach(b=>b.classList.toggle('active',b.textContent==='LCZx'));};
      row.append(label,input);$('urban-fields').append(row);
    }
  }
}
$('urban-apply').onclick=()=>action(async()=>{
  if(!urbanDirty)return;const payload={setting:{},site:{}};
  for(const input of $('urban-fields').querySelectorAll('[data-key]')){
    const k=input.dataset.key,type=input.dataset.type;
    let value=input.value;if(type==='number')value=Number(value);else if(type==='boolean')value=value==='true';else if(type==='array')value=JSON.parse(value);else if(type==='null')value=value==='null'?null:Number(value);
    if(input.dataset.group==='setting')payload.setting[k]=value;else if(k==='FVC'||k==='FWC')payload[k]=value;else payload.site[k]=value;
  }
  await api('urban',{data:payload});discardResult();await refresh();log('城市参数已应用为 LCZx，将在发起计算时记录。');
});
$('restore-urban').onclick=()=>action(async()=>{await api('urban',{restore:true});discardResult();await refresh();});

let scheduleKey=null,scheduleValues=[],curveMax=1,dragHour=null;
const svgNS='http://www.w3.org/2000/svg';
function svgEl(tag,attrs={},text=null){const e=document.createElementNS(svgNS,tag);for(const[k,v]of Object.entries(attrs))e.setAttribute(k,v);if(text!==null)e.textContent=text;return e;}
function openSchedule(key){
  scheduleKey=key;const value=draft[key];scheduleValues=Array.isArray(value)?clone(value):Array(24).fill(value??0);
  curveMax=Math.max(...scheduleValues,1)*1.25;$('schedule-title').textContent=(data.fields.find(f=>f.key===key)?.label||key)+' · 每日曲线';$('schedule-max').value=fmt(curveMax);
  $('schedule-values').replaceChildren();for(let h=0;h<24;h++){
    const label=document.createElement('label');label.textContent=String(h).padStart(2,'0')+':00';const input=document.createElement('input');input.type='number';input.step='any';input.value=fmt(scheduleValues[h]);
    input.oninput=()=>{scheduleValues[h]=Number(input.value);drawSchedule();};label.append(input);$('schedule-values').append(label);
  }drawSchedule();$('schedule-dialog').showModal();
}
function drawSchedule(){
  const svg=$('schedule-curve');svg.replaceChildren();
  const x=h=>55+h*755/23,y=v=>235-v/curveMax*210;
  for(let i=0;i<=4;i++){
    const value=curveMax*i/4;svg.append(svgEl('line',{x1:55,y1:y(value),x2:810,y2:y(value),stroke:'#dce1d5','stroke-width':1}));
    svg.append(svgEl('text',{x:45,y:y(value)+4,'text-anchor':'end',fill:'#7a837b','font-size':11},fmt(value)));
  }
  svg.append(svgEl('polyline',{points:scheduleValues.map((v,h)=>`${x(h)},${y(v)}`).join(' '),fill:'none',stroke:'#315f4c','stroke-width':2}));
  for(let h=0;h<24;h++){
    svg.append(svgEl('text',{x:x(h),y:260,'text-anchor':'middle',fill:'#7a837b','font-size':10},h));
    const dot=svgEl('circle',{cx:x(h),cy:y(scheduleValues[h]),r:5,fill:'#fffefa',stroke:'#315f4c','stroke-width':2,'data-hour':h,style:'cursor:ns-resize'});svg.append(dot);
  }
}
$('schedule-curve').onpointerdown=e=>{const h=e.target.dataset.hour;if(h===undefined)return;dragHour=Number(h);$('schedule-curve').setPointerCapture(e.pointerId);e.preventDefault();};
$('schedule-curve').onpointermove=e=>{
  if(dragHour===null)return;const r=$('schedule-curve').getBoundingClientRect(),y=(e.clientY-r.top)/r.height*280;
  let value=Math.max(0,Math.min(curveMax,(235-y)/210*curveMax));if(scheduleKey.endsWith('Temp'))value=Math.max(-30,value);
  if(scheduleKey==='CoolingSetpointHumidity')value=Math.min(1,value);
  scheduleValues[dragHour]=Number(value.toPrecision(5));$('schedule-values').querySelectorAll('input')[dragHour].value=fmt(value);drawSchedule();
};
$('schedule-curve').onpointerup=()=>{dragHour=null;};$('schedule-curve').onpointercancel=()=>{dragHour=null;};
$('schedule-max').onchange=()=>{const v=Number($('schedule-max').value);if(v>0&&Number.isFinite(v)){curveMax=v;drawSchedule();}};
$('schedule-confirm').onclick=e=>{e.preventDefault();if(!scheduleValues.every(Number.isFinite)){message('日程包含无效数值');return;}draft[scheduleKey]=clone(scheduleValues);dirty.add(scheduleKey);buildingRows();$('schedule-dialog').close();};

async function saveOptions(){await api('options',{iteration:$('iteration').value.trim(),sample:Number($('sample').value),load_tolerance:Number($('load-tolerance').value),seed:Number($('seed').value),random_ratio:Number($('random-ratio').value)});}
function running(value){
  $('calculate').disabled=value;$('stop').hidden=!value;
  for(const id of ['import-shp','import-weather','import-rad','save','load','urban-apply','building-apply','random-select','select-all','select-none'])$(id).disabled=value||(id==='building-apply'&&!selected.size);
  $('type-buttons').querySelectorAll('button').forEach(b=>b.disabled=value||!selected.size);
  $('lcz-buttons').querySelectorAll('button').forEach(b=>b.disabled=value);
  $('status').textContent=value?'后台模拟运行中':'本地工作台';
}
$('calculate').onclick=()=>action(async()=>{
  if(dirty.size||urbanDirty)throw Error('还有未 Apply 的参数，请先应用修改再计算');
  if($('start-date').value!==data.state.start_date||$('end-date').value!==data.state.end_date||$('weather-path').value!==data.paths.weather)throw Error('气象路径或日期已修改，请先重新导入气象');
  await saveOptions();const response=await api('run',{});job=response.id;offset=0;result=null;$('results').hidden=true;
  $('run-state').textContent='运行中';log('\n──────── 开始 N / F 对比 ────────');running(true);schedulePoll();
});
function schedulePoll(){clearTimeout(timer);timer=setTimeout(poll,1000);}
async function poll(){
  if(!job)return;
  try{
    const response=await api(`jobs/${job}?offset=${offset}`);offset=response.offset;
    if(response.logs){const el=$('logs');el.textContent+=response.logs;el.scrollTop=el.scrollHeight;}
    if(response.status==='running'){if(response.progress?.history?.length)$('run-state').textContent=`F 第 ${response.progress.history.length} 轮`;schedulePoll();return;}
    running(false);const completed=job;job=null;
    if(response.status==='complete'){
      result=response.result;$('run-state').textContent=result.converged?'计算完成 · 已收敛':'计算完成 · 未达到负荷阈值';
      await refresh();showResults(completed);
    }else{$('run-state').textContent=response.status==='cancelled'?'已停止':'计算失败';if(response.error)message(response.error,'计算失败');}
  }catch(e){log('读取进度失败：'+e.message+'；正在重试。');schedulePoll();}
}
$('stop').onclick=()=>action(async()=>{if(job){await api(`jobs/${job}/stop`,{});schedulePoll();}},'正在停止计算进程…');
$('clear-log').onclick=()=>{$('logs').textContent='';};

function chart(target,series,{unit='',labels=[],right=false,threshold=null}={}){
  const host=$(target);host.replaceChildren();const width=650,height=290,L=54,R=right?62:22,T=20,B=65;
  const svg=svgEl('svg',{viewBox:`0 0 ${width} ${height}`,role:'img','aria-label':target});host.append(svg);
  const leftValues=series.filter(s=>!s.right).flatMap(s=>s.values),rightValues=series.filter(s=>s.right).flatMap(s=>s.values);
  function range(values){let lo=Math.min(...values),hi=Math.max(...values);if(!Number.isFinite(lo))return[0,1];if(lo===hi){lo-=Math.max(1,Math.abs(lo)*.1);hi+=Math.max(1,Math.abs(hi)*.1);}const pad=(hi-lo)*.1;return[lo-pad,hi+pad];}
  const a=range(threshold===null?leftValues:[...leftValues,threshold]),b=range(rightValues),length=Math.max(1,...series.map(s=>s.values.length));
  const x=i=>L+i/Math.max(1,length-1)*(width-L-R),y=(v,r=false)=>{const range=r?b:a;return height-B-(v-range[0])/(range[1]-range[0])*(height-T-B);};
  for(let i=0;i<=4;i++){
    const value=a[0]+(a[1]-a[0])*i/4,yy=y(value);svg.append(svgEl('line',{x1:L,y1:yy,x2:width-R,y2:yy,stroke:'#e5e8de'}));
    svg.append(svgEl('text',{x:L-8,y:yy+4,'text-anchor':'end',fill:'#7a837b','font-size':10},Number(value.toPrecision(3))));
    if(right)svg.append(svgEl('text',{x:width-R+7,y:yy+4,fill:'#b77c53','font-size':9},Number((b[0]+(b[1]-b[0])*i/4).toPrecision(2))));
  }
  for(let i=0;i<=5;i++){const index=Math.round(i*(length-1)/5);svg.append(svgEl('text',{x:x(index),y:height-B+19,'text-anchor':'middle',fill:'#7a837b','font-size':9},labels[index]??index+1));}
  if(threshold!==null)svg.append(svgEl('line',{x1:L,y1:y(threshold),x2:width-R,y2:y(threshold),stroke:'#a3aa97','stroke-dasharray':'4 4'}));
  let legendX=L,legendY=height-18;
  for(const s of series){
    svg.append(svgEl('polyline',{points:s.values.map((v,i)=>`${x(i)},${y(v,s.right)}`).join(' '),fill:'none',stroke:s.color,'stroke-width':1.7,'stroke-dasharray':s.dash||'','stroke-linejoin':'round'}));
    if(length===1)svg.append(svgEl('circle',{cx:x(0),cy:y(s.values[0],s.right),r:3,fill:s.color}));
    if(legendX+100>width-R){legendY-=17;legendX=L;}
    svg.append(svgEl('line',{x1:legendX,y1:legendY-3,x2:legendX+14,y2:legendY-3,stroke:s.color,'stroke-width':2}));
    svg.append(svgEl('text',{x:legendX+20,y:legendY,fill:'#677367','font-size':10},s.name));legendX+=Math.max(70,s.name.length*8+35);
  }
  const cursor=svgEl('line',{y1:T,y2:height-B,stroke:'#8b9a87','stroke-dasharray':'3 3',visibility:'hidden'});svg.append(cursor);
  const tooltip=document.createElement('div');tooltip.className='plot-tooltip';tooltip.hidden=true;host.append(tooltip);
  svg.onpointermove=e=>{
    const r=svg.getBoundingClientRect(),px=(e.clientX-r.left)/r.width*width,index=Math.max(0,Math.min(length-1,Math.round((px-L)/(width-L-R)*(length-1))));
    cursor.setAttribute('x1',x(index));cursor.setAttribute('x2',x(index));cursor.setAttribute('visibility','visible');
    tooltip.hidden=false;tooltip.textContent=(labels[index]??`第 ${index+1} 轮`)+'\n'+series.map(s=>`${s.name}: ${fmt(s.values[index])}`).join('\n');
    tooltip.style.left=Math.max(0,Math.min(e.clientX-r.left+12,r.width-190))+'px';tooltip.style.top='25px';
  };svg.onpointerleave=()=>{tooltip.hidden=true;cursor.setAttribute('visibility','hidden');};
}
function hourLabel(hoy){const date=new Date(Date.UTC(2025,0,1,Number(hoy)));return `${String(date.getUTCMonth()+1).padStart(2,'0')}-${String(date.getUTCDate()).padStart(2,'0')} ${String(date.getUTCHours()).padStart(2,'0')}h`;}
function showResults(jobId){
  $('results').hidden=false;const summary=$('summary-cards');summary.replaceChildren();
  for(const [label,value,unit]of [['总建筑面积',result.total_gfa,'m² GFA'],['N 累计 HVAC + 照明电耗',result.energy_kwh_m2.N,'kWh/m² GFA'],['F 累计 HVAC + 照明电耗',result.energy_kwh_m2.F,'kWh/m² GFA'],['F 交替计算',result.iterations,result.converged?'轮 · 已收敛':'轮 · 未达到负荷阈值']]){
    const card=document.createElement('div');card.className='summary-card';const p=document.createElement('p'),b=document.createElement('b'),span=document.createElement('span');p.textContent=label;b.textContent=fmt(value);span.textContent=unit;card.append(p,b,span);summary.append(card);
  }
  $('downloads').replaceChildren();for(const name of ['N_hourly.csv','F_hourly.csv','results.json']){const a=document.createElement('a');a.href=`/api/jobs/${jobId}/download/${name}`;a.textContent=name;a.download=name;$('downloads').append(a);}
  const labels=result.F.map(r=>hourLabel(r.hoy));
  chart('temperature-chart',[{name:'N',color:'#527b9b',values:result.N.map(r=>r.Ta)},{name:'F',color:'#c08054',values:result.F.map(r=>r.Ta)},{name:'Ta.bg',color:'#6b756a',dash:'4 4',values:result.F.map(r=>r.Tb)}],{labels});
  chart('load-chart',[{name:'N',color:'#527b9b',values:result.N.map(r=>r.Load)},{name:'F',color:'#c08054',values:result.F.map(r=>r.Load)}],{labels});
  chart('convergence-chart',[{name:'负荷变化',color:'#315f4c',values:result.history.map(r=>r.load_change_w_m2_gfa)},{name:'城市残差',color:'#b77c53',right:true,values:result.history.map(r=>r.city_residual_w_m2)}],{right:true,labels:result.history.map(r=>String(r.iteration)),threshold:result.load_tolerance});
  fluxChart();$('result-paths').replaceChildren();
  for(const mode of ['N','F']){const p=document.createElement('p');p.append(document.createTextNode(`${mode} 建筑完整结果：`));const a=document.createElement('a');a.textContent=result.paths[mode].json;a.href=`/api/jobs/${jobId}/download/${mode}_building_results.json`;p.append(a);$('result-paths').append(p);}
}
function fluxChart(){if(!result)return;const rows=result[$('flux-mode').value];const keys=['QS','QL','QA','QV','QHFlux','QGFlux','Qsa','Qsg'],colors=['#527b9b','#b65f63','#c8a55c','#76a59a','#9b83b2','#89985e','#405b46','#ca885b'];chart('flux-chart',keys.map((k,i)=>({name:k,color:colors[i],values:rows.map(r=>r[k])})),{labels:rows.map(r=>hourLabel(r.hoy))});}
$('flux-mode').onchange=fluxChart;
action(()=>refresh({fit:true}),'正在打开 CETUS 工作台…');
