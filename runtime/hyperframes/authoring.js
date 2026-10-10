/* Original Motionwright native HTML authoring profile. Data is not executable code. */
(() => {
  'use strict';
  const doc = JSON.parse(document.getElementById('mw-source').textContent);
  if (doc.version !== 1 || !Array.isArray(doc.nodes) || doc.nodes.length > 256) throw new Error('Native profile/version budget mismatch');
  const world = document.getElementById('mw-world');
  const seconds = frame => frame * doc.canvas.rate.den / doc.canvas.rate.num;
  const instant = key => key.frame + (key.subframe ? key.subframe.num / key.subframe.den : 0);
  const assets = new Map(doc.assets.map(asset => [asset.id, asset]));
  const nodes = new Map();
  const timeline = gsap.timeline({paused: true});
  const mediaUrl = id => {
    const asset = assets.get(id);
    if (!asset) throw new Error('Missing asset identity');
    const extensions = {png:'png',jpeg:'jpg',mp4:'mp4',woff2:'woff2'};
    return '/assets/' + asset.sha256 + '.' + extensions[asset.kind];
  };
  const css = (el, values) => Object.assign(el.style, values);
  const point = p => `${p.x},${p.y}`;
  function content(node, el) {
    const c=node.content;
    switch(c.kind) {
      case 'group': break;
      case 'text': {
        el.classList.add('mw-text');
        const font = c.font.kind === 'sans' ? 'Motionwright Sans' : c.font.kind === 'mono' ? 'Motionwright Mono' : c.font.family;
        css(el,{fontFamily:font,fontSize:c.size+'px',lineHeight:String(c.line_height),textAlign:c.align});
        for(const run of c.runs) {
          const span=document.createElement('span'); span.textContent=run.text;
          css(span,{color:run.color,fontWeight:String(run.weight),fontStyle:run.italic?'italic':'normal'});el.append(span);
        }
        break;
      }
      case 'rectangle': case 'ellipse':
        css(el,{background:c.fill,border:c.stroke?`${c.stroke_width}px solid ${c.stroke}`:'none',borderRadius:c.kind==='ellipse'?'50%':c.radius+'px'});break;
      case 'path': {
        const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
        svg.setAttribute('viewBox',`0 0 ${node.pose.width} ${node.pose.height}`);
        const path=document.createElementNS(svg.namespaceURI,'path');
        path.setAttribute('d','M'+c.points.map(point).join(' L')+(c.closed?' Z':''));
        path.setAttribute('fill',c.fill || 'none');path.setAttribute('stroke',c.stroke);path.setAttribute('stroke-width',String(c.stroke_width));
        path.setAttribute('stroke-linejoin','round');path.setAttribute('stroke-linecap','round');svg.append(path);el.append(svg);break;
      }
      case 'image': case 'video': {
        const media=document.createElement(c.kind==='image'?'img':'video');media.src=mediaUrl(c.asset_id);
        media.style.objectFit=c.fit==='stretch'?'fill':c.fit;
        if(c.kind==='image') media.alt=node.name;
        else {media.muted=true;media.preload='auto';media.playsInline=true;media.dataset.mwVideo=node.id;}
        el.append(media);break;
      }
      default: throw new Error('Unadmitted content kind');
    }
  }
  function decorations(node,el) {
    css(el,{mixBlendMode:node.blend,'--mw-blur':node.effects.blur+'px'});
    const shadow=node.effects.shadow;
    el.style.filter='blur(var(--mw-blur))'+(shadow?` drop-shadow(${shadow.x}px ${shadow.y}px ${shadow.blur}px ${shadow.color})`:'');
    const clip=node.clip;
    if(clip.kind==='inset') {
      for(const edge of ['top','right','bottom','left'])el.style.setProperty('--mw-clip-'+edge,clip[edge]+'%');
      el.style.clipPath=`inset(var(--mw-clip-top) var(--mw-clip-right) var(--mw-clip-bottom) var(--mw-clip-left) round ${clip.radius}px)`;
    } else if(clip.kind==='circle')el.style.clipPath=`circle(${clip.radius}% at ${clip.center_x}% ${clip.center_y}%)`;
    else if(clip.kind==='polygon')el.style.clipPath='polygon('+clip.points.map(p=>`${p.x}% ${p.y}%`).join(',')+')';
  }
  for(const node of doc.nodes) {
    const el=document.createElement('div');el.className='mw-node';el.id='mw-'+node.id;el.dataset.mwId=node.id;
    css(el,{width:node.pose.width+'px',height:node.pose.height+'px',zIndex:String(node.pose.z_index)});
    content(node,el);decorations(node,el);nodes.set(node.id,el);
  }
  for(const node of doc.nodes)(node.parent_id?nodes.get(node.parent_id):world).append(nodes.get(node.id));
  function property(property,value,camera=false) {
    if(camera && property==='scale_x')return {scaleX:value,scaleY:value};
    const aliases={scale_x:'scaleX',scale_y:'scaleY',rotation:'rotation',opacity:'opacity',x:'x',y:'y',width:'width',height:'height'};
    if(property in aliases)return {[aliases[property]]:value};
    if(property==='blur')return {'--mw-blur':value+'px'};
    if(property.startsWith('clip_'))return {['--mw-clip-'+property.slice(5)]:value+'%'};
    throw new Error('Unadmitted animation property');
  }
  function ease(curve) {
    switch(curve.kind) {
      case 'linear':return 'none';case 'ease_out_cubic':return 'power2.out';case 'ease_in_out':return t=>t*t*(3-2*t);
      case 'cubic_bezier':return t=>{
        const coordinate=(u,a,b)=>3*(1-u)*(1-u)*u*a+3*(1-u)*u*u*b+u*u*u;
        let lo=0,hi=1;
        for(let i=0;i<40;i++){const mid=(lo+hi)/2;if(coordinate(mid,curve.x1,curve.x2)<t)lo=mid;else hi=mid;}
        return coordinate((lo+hi)/2,curve.y1,curve.y2);
      };
      default:throw new Error('Unadmitted interpolation');
    }
  }
  function tracks(el,base,keys,camera=false) {
    let initial={};
    for(const [name,value] of Object.entries(base))initial={...initial,...property(name,value,camera)};
    // The initial frame is a real authored pose. A paused timeline has not crossed
    // any zero-duration event yet, so materialize zero-time keys before registration.
    for(const key of keys)if(instant(key)===0)initial={...initial,...property(key.property,key.value,camera)};
    gsap.set(el,initial);timeline.set(el,initial,0);
    const channels=[...new Set(keys.map(k=>k.property))];
    for(const channel of channels) {
      let previous=base[channel],start=0;
      for(const key of keys.filter(k=>k.property===channel).sort((a,b)=>instant(a)-instant(b))) {
        if(instant(key)===0 || key.curve.kind==='hold')timeline.set(el,property(channel,key.value,camera),seconds(instant(key)));
        else timeline.fromTo(el,property(channel,previous,camera),{...property(channel,key.value,camera),duration:seconds(instant(key)-start),ease:ease(key.curve),immediateRender:false,lazy:false},seconds(start));
        previous=key.value;start=instant(key);
      }
    }
  }
  for(const node of doc.nodes) {
    const p=node.pose,base={x:p.x,y:p.y,width:p.width,height:p.height,scale_x:p.scale_x,scale_y:p.scale_y,rotation:p.rotation,opacity:p.opacity,blur:node.effects.blur};
    if(node.clip.kind==='inset')for(const edge of ['top','right','bottom','left'])base['clip_'+edge]=node.clip[edge];
    tracks(nodes.get(node.id),base,node.keyframes);
  }
  tracks(world,{x:doc.camera.x,y:doc.camera.y,scale_x:doc.camera.zoom,rotation:doc.camera.rotation},doc.camera.keyframes,true);
  timeline.to({clock:0},{clock:1,duration:seconds(doc.canvas.frames),ease:'none'},0);
  window.__timelines = window.__timelines || {};
  window.__timelines.main=timeline;
  window.__mwInspect=()=>doc.nodes.map(node=>{
    const el=nodes.get(node.id),style=getComputedStyle(el),rect=el.getBoundingClientRect();
    return {id:node.id,source_role:node.name,kind:node.content.kind,
      box:{x:rect.x,y:rect.y,width:rect.width,height:rect.height},transform:style.transform,
      opacity:Number(style.opacity),blend:style.mixBlendMode,clip:style.clipPath,filter:style.filter,
      text:node.content.kind==='text'?el.textContent:null,
      truncated:node.content.kind==='text'?(el.scrollWidth>el.clientWidth+1 || el.scrollHeight>el.clientHeight+1):null,
      computed_font:node.content.kind==='text'?style.fontFamily:null,
      font_size_px:node.content.kind==='text'?Number.parseFloat(style.fontSize):null};
  });
  window.__mwNativeProfile={version:doc.version,frames:doc.canvas.frames,rate:doc.canvas.rate};
})();
