/* Wake word - HUD side.  Loaded by the HUD only while the feature is switched on (see wake_word.py for the full picture).
   Opens the microphone, turns it into 16 kHz mono chunks, and streams ONLY the chunks that contain speech to the local
   bridge (WebSocket /api/m/wake_word/ws).  Everything stays on this computer.  When the bridge says "wake", it plays a
   short tone and starts the normal listen -> chat -> speak flow. */
(function(){
const API='/api/m/wake_word/';
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let st={state:'loading',progress:0,muted:false,phrase:'Hey ATLAS',aliases:[],error:'',text:''};
let enabled=true,ws=null,stream=null,actx=null,node=null,src=null,wantTimer=null,retry=0,lastFlash=0,permErr='';
/* ---- speech gate (keeps the bridge idle while nobody talks) ---- */
let ring=[],hot=0,speaking=false,sinceVoice=0,uttChunks=0,floor=0.004;
const chip=()=>document.getElementById('wakechip');

function paused(){return ATLAS.orb()!=='idle'}
function resetGate(){ring=[];hot=0;speaking=false;sinceVoice=0;uttChunks=0}
function send(buf){if(ws&&ws.readyState===1)ws.send(buf)}
function onChunk(pcm,rms){
  if(paused()){if(speaking&&ws&&ws.readyState===1)ws.send(JSON.stringify({reset:true}));resetGate();return}
  if(rms<floor*1.6)floor=floor*0.97+rms*0.03;else if(!speaking)floor=Math.min(0.03,floor*1.001);
  floor=Math.max(0.002,floor);
  const thr=Math.max(0.012,floor*3.2),loud=rms>thr;
  if(!speaking){
    ring.push(pcm);if(ring.length>4)ring.shift();
    hot=loud?hot+1:0;
    if(hot>=2){speaking=true;sinceVoice=0;uttChunks=0;ring.forEach(c=>send(c.buffer));ring=[]}
  }else{
    send(pcm.buffer);uttChunks++;
    if(loud)sinceVoice=0;else sinceVoice++;
    if(sinceVoice>=7||uttChunks>=80){if(ws&&ws.readyState===1)ws.send(JSON.stringify({end:true}));resetGate()}
  }
}
const WORKLET='class P extends AudioWorkletProcessor{constructor(){super();this.r=sampleRate/16000;this.ph=0;this.sum=0;this.cnt=0;this.buf=new Int16Array(1600);this.n=0;this.e=0}'
 +'process(i){const ch=i[0]&&i[0][0];if(!ch)return true;for(let k=0;k<ch.length;k++){this.sum+=ch[k];this.cnt++;this.ph+=1;'
 +'if(this.ph>=this.r){const v=this.sum/this.cnt;this.ph-=this.r;this.sum=0;this.cnt=0;const c=Math.max(-1,Math.min(1,v));this.buf[this.n++]=c*32767;this.e+=c*c;'
 +'if(this.n===1600){this.port.postMessage({pcm:this.buf,rms:Math.sqrt(this.e/1600)},[this.buf.buffer]);this.buf=new Int16Array(1600);this.n=0;this.e=0}}}return true}}'
 +'registerProcessor("atlas-wake",P)';

async function openMic(){
  if(stream)return true;
  try{stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true,channelCount:1}})}
  catch(e){stream=null;permErr=(e&&e.name==='NotFoundError')?'No microphone found - plug one in, then click the Wake chip.':(e&&e.name==='NotAllowedError')?'Microphone blocked - allow it in the browser address bar, then click the Wake chip.':'Microphone unavailable ('+((e&&e.name)||'error')+') - click the Wake chip to retry.';paint();return false}
  permErr='';
  try{
    if(!actx){            /* audio graph is built once and only suspended while muted; re-arming just re-attaches the microphone */
      try{actx=new (window.AudioContext||window.webkitAudioContext)({sampleRate:16000})}catch(e){actx=new (window.AudioContext||window.webkitAudioContext)()}
      const url=URL.createObjectURL(new Blob([WORKLET],{type:'application/javascript'}));
      try{await actx.audioWorklet.addModule(url)}finally{URL.revokeObjectURL(url)}
      node=new AudioWorkletNode(actx,'atlas-wake',{numberOfOutputs:0});node.port.onmessage=e=>onChunk(e.data.pcm,e.data.rms);
    }
    src=actx.createMediaStreamSource(stream);src.connect(node);
    if(actx.state!=='running')await actx.resume().catch(()=>{});
  }catch(e){closeMic(true);permErr='This browser cannot process audio for the wake word ('+((e&&e.message)||e)+').';paint();return false}
  return true;
}
function closeMic(full){
  try{src&&src.disconnect()}catch(e){}
  if(stream){stream.getTracks().forEach(t=>t.stop())}          /* releases the microphone: the browser's recording indicator goes off */
  stream=src=null;resetGate();
  if(actx){if(full){try{node&&node.disconnect()}catch(e){}try{actx.close()}catch(e){}actx=node=null}else actx.suspend().catch(()=>{})}
}
function openWs(){
  if(ws&&(ws.readyState===0||ws.readyState===1))return;
  const w=new WebSocket('ws://'+location.host+API+'ws');w.binaryType='arraybuffer';ws=w;
  w.onmessage=e=>{let j;try{j=JSON.parse(e.data)}catch(x){return}
    if(j.type==='wake')onWake(j);else if(j.type==='ready'){retry=0;paint()}};
  w.onclose=()=>{if(ws===w)ws=null};
}
function closeWs(){if(ws){try{ws.close()}catch(e){}ws=null}}
/* ---- tone + hand-off ---- */
function tone(){
  try{const c=new (window.AudioContext||window.webkitAudioContext)();const o=c.createOscillator(),g=c.createGain();
    o.type='sine';o.frequency.setValueAtTime(660,c.currentTime);o.frequency.linearRampToValueAtTime(990,c.currentTime+.11);
    g.gain.setValueAtTime(0.0001,c.currentTime);g.gain.exponentialRampToValueAtTime(0.12,c.currentTime+.02);g.gain.exponentialRampToValueAtTime(0.0001,c.currentTime+.16);
    o.connect(g);g.connect(c.destination);o.start();o.stop(c.currentTime+.18);setTimeout(()=>c.close(),400)}catch(e){}
}
function onWake(j){
  if(st.muted||paused())return;
  ATLAS.log('Wake phrase heard · '+(j.phrase||st.phrase));
  document.body.classList.add('wake-flash');setTimeout(()=>document.body.classList.remove('wake-flash'),1400);
  tone();
  setTimeout(()=>{if(!st.muted&&ATLAS.orb()==='idle')ATLAS.listen({wake:true})},260);   /* after the tone, so it is not recorded */
}
/* ---- state / chip ---- */
function armed(){return enabled&&st.state==='ready'&&!st.muted&&!!stream&&!!ws&&ws.readyState===1}
function paint(){
  const c=chip();if(c){
    let t,cls='tag',title='';
    if(!enabled){t='Wake word · off';title='Turn it on in Settings → Features'}
    else if(st.state==='downloading'||st.state==='loading'){t='Wake · preparing '+Math.round((st.progress||0)*100)+'%';cls+=' warn';title=st.text||''}
    else if(st.state==='error'){t='Wake · error';cls+=' off';title=st.error}
    else if(permErr){t='Wake · no mic';cls+=' off';title=permErr}
    else if(st.muted){t='Wake · muted';cls+=' mutedw';title='Click to unmute'}
    else if(armed()){t=st.phrase;cls+=' live armed';title='Microphone is armed for the wake phrase. Click to mute.'}
    else{t='Wake · connecting…';cls+=' warn'}
    c.textContent=t;c.className=cls;c.title=title;
  }
  if(window.ATLAS&&ATLAS.settingsChanged)ATLAS.settingsChanged('wake_word');
}
async function refresh(){
  try{st=Object.assign(st,await ATLAS.api(API+'status'));enabled=true}catch(e){/* feature stopped or bridge offline */ enabled=false}
  sync();
}
function sync(){
  const want=enabled&&st.state==='ready'&&!st.muted;
  if(want){if(!stream)openMic().then(ok=>{if(ok)openWs();paint()});else openWs()}
  else{closeWs();closeMic()}
  paint();
}
async function toggleMute(){
  try{st=Object.assign(st,await ATLAS.post(API+'mute',{muted:!st.muted}))}catch(e){}
  sync();
}
/* chip click: mute/unmute, retry on error, or open Settings when off */
document.addEventListener('click',e=>{const c=chip();if(!c||!c.contains(e.target))return;
  if(!enabled){ATLAS.openSettings('features');return}
  if(st.state==='error'){ATLAS.post(API+'retry',{}).catch(()=>{});return}
  if(permErr){permErr='';sync();return}
  if(st.state==='ready')toggleMute()});
ATLAS.on('wake_state',m=>{if(m.feature!=='wake_word')return;const was=st.muted;st=Object.assign(st,m);enabled=true;
  if(m.muted!==was||m.state==='ready'||(m.state!=='ready'&&stream))sync();else paint()});
ATLAS.on('features',m=>{const f=(m.features||[]).find(x=>x.id==='wake_word');if(!f)return;
  if(!f.enabled||f.status!=='running'){enabled=false;sync()}else refresh()});
ATLAS.on('orb',()=>{/* paused state is read live in onChunk */});
window.addEventListener('beforeunload',()=>{closeWs();closeMic(true)});
setInterval(()=>{if(enabled&&st.state==='ready'&&!st.muted&&(!ws||ws.readyState>1)&&stream){if(retry<8){retry++;openWs()}}},3000);

/* ---- Settings → Features: extra controls under the wake-word row ---- */
ATLAS.register('wake_word',{title:'Wake word',dock:false,render(){},settings(el){
  const prog=st.state==='downloading'||st.state==='loading';
  let h='<div class="wk"><div class="wkrow"><span class="lab">Say</span><b class="wkphrase">'+esc(st.phrase)+'</b>'
    +'<span class="lab" style="margin-left:1vw">Change the name in Settings → Profile → Assistant</span></div>';
  if(prog)h+='<div class="wkrow"><div class="wkbar"><i style="width:'+Math.round((st.progress||0)*100)+'%"></i></div><span class="lab">'+esc(st.text||'Preparing')+' · '+Math.round((st.progress||0)*100)+'%</span></div>';
  else if(st.state==='error')h+='<div class="wkrow"><span class="msg bad">'+esc(st.error)+'</span><button class="btn" id="wkretry">Try again</button></div>';
  else h+='<div class="wkrow"><span class="lab">Status</span><b class="'+(armed()?'wkon':'')+'">'+(permErr?esc(permErr):st.muted?'Muted - microphone released':armed()?'Armed - listening for the phrase':'Connecting…')+'</b>'
    +'<button class="btn '+(st.muted?'':'sel')+'" id="wkmute">'+(st.muted?'Unmute':'Mute')+'</button></div>';
  h+='<div class="wkrow"><label class="lab" for="wkalias">Also accept</label><input id="wkalias" maxlength="80" placeholder="sounds-like spellings, comma separated (optional)" value="'+esc((st.aliases||[]).join(', '))+'"><button class="btn" id="wksave">Save</button></div>'
    +'<div class="msg" style="opacity:.8">Mic is opened by this window only while armed; audio is analysed on this computer, never saved or sent. Engine: '+esc(st.engine||'Vosk')+' (offline).</div></div>';
  el.innerHTML=h;
  const m=el.querySelector('#wkmute');if(m)m.onclick=toggleMute;
  const r=el.querySelector('#wkretry');if(r)r.onclick=()=>ATLAS.post(API+'retry',{}).catch(()=>{});
  el.querySelector('#wksave').onclick=async()=>{try{st=Object.assign(st,await ATLAS.post(API+'aliases',{aliases:el.querySelector('#wkalias').value}));paint()}catch(e){}};
}});
refresh();
})();
