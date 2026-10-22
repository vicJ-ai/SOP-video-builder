import {useEffect,useState} from "react";
import {api,type Summary} from "./api";
// Warehouse SOP Video: the floor recording, the WMS screen recording and the SOP. Demo video: one recording + optional notes.
const LABEL=["Floor","WMS"];
type Mode="sop"|"demo";
function stateLabel(p:Summary){if(p.running)return p.current?`${p.current.replace(/_/g," ")}…`:"working…";return({done:"ready",failed:"needs attention",awaiting_review:"ready to render",stale:"changes pending",pending:"not started"} as Record<string,string>)[p.current_state]??p.current_state;}
const DEMO_ONE="A demo video uses one recording; the first one is kept.";
export function Home(){
 const [projects,setProjects]=useState<Summary[]>([]),[files,setFiles]=useState<File[]>([]),[sop,setSop]=useState<File|null>(null),[title,setTitle]=useState(""),[error,setError]=useState(""),[fileNotice,setFileNotice]=useState(""),[dragging,setDragging]=useState(false),[busy,setBusy]=useState(false);
 const [confirming,setConfirming]=useState(""),[deleting,setDeleting]=useState("");
 const [mode,setMode]=useState<Mode>("sop"),[kind,setKind]=useState<"screen"|"camera">("screen");
 const demo=mode==="demo";
 async function remove(id:string){setDeleting(id);setError("");try{await api.remove(id);setProjects(prev=>prev.filter(p=>p.id!==id));setConfirming("");}catch(err){setError((err as Error).message);}finally{setDeleting("");}}
 useEffect(()=>{api.list().then(setProjects).catch(e=>setError(e.message));},[]);
 useEffect(()=>{if(!projects.some(p=>p.running))return;const t=window.setTimeout(()=>api.list().then(setProjects).catch(()=>{}),3000);return()=>window.clearTimeout(t);},[projects]);
 const stem=(f:File|null|undefined,re:RegExp)=>f?.name.replace(re,"").replace(/[_-]+/g," ")||"";
 const ready=demo?files.length===1:files.length===2&&!!sop,inferredTitle=demo
  ?title.trim()||stem(sop,/\.(md|markdown)$/i)||stem(files[0],/\.[^.]+$/)||"Product demo"
  :title.trim()||sop?.name.replace(/\.(md|markdown)$/i,"").replace(/[_-]+/g," ")||"Observed workflow";
 function choose(next:Mode){
  if(next===mode)return;
  setMode(next);setError("");
  if(next==="demo"&&files.length>1){setFiles(files.slice(0,1));setFileNotice(DEMO_ONE);}else setFileNotice("");
 }
 function addFiles(list:FileList|File[]|null){
  if(!list)return;
  const max=demo?1:2;
  const incoming=Array.from(list),videos=incoming.filter(f=>f.type.startsWith("video/")||/\.(mp4|mov|mkv|webm|avi|m4v)$/i.test(f.name)),docs=incoming.filter(f=>/\.(md|markdown)$/i.test(f.name)),invalid=incoming.filter(f=>!videos.includes(f)&&!docs.includes(f));
  setFileNotice(invalid.length?`Unsupported file${invalid.length>1?"s":""}: ${invalid.map(f=>f.name).join(", ")}. Add MP4, MOV, MKV, or a Markdown (.md) ${demo?"notes file":"SOP"}.`:videos.length+files.length>max?(demo?DEMO_ONE:"Only two recordings are used: the floor recording first, then the WMS screen recording."):"");
  setFiles(prev=>{const known=new Set(prev.map(f=>`${f.name}:${f.size}:${f.lastModified}`));const fresh=videos.filter(f=>!known.has(`${f.name}:${f.size}:${f.lastModified}`));return [...prev,...fresh].slice(0,max);});
  if(docs[0])setSop(docs[0]);
 }
 function onDrop(e:React.DragEvent){e.preventDefault();setDragging(false);addFiles(Array.from(e.dataTransfer.files));}
 async function submit(e:React.FormEvent){e.preventDefault();setError("");if(!ready){setError(demo?"Add one recording to begin.":"Add the floor recording, the WMS recording and the SOP (.md) to begin.");return;}setBusy(true);const form=new FormData();form.set("title",inferredTitle);
  if(demo){form.set("mode","demo");form.set(kind==="screen"?"system":"physical",files[0]);if(sop)form.set("notes",sop);}
  else{form.set("physical",files[0]);form.set("system",files[1]);form.set("sop",sop!);}
  form.set("auto_start","true");form.set("auto_approve","true");try{const project=await api.create(form);window.location.hash=`/p/${project.id}`;}catch(err){setError((err as Error).message);}finally{setBusy(false);}}
 const missing=demo?[files.length<1&&"one recording"].filter(Boolean):[files.length<1&&"floor recording",files.length<2&&"WMS recording",!sop&&"SOP (.md)"].filter(Boolean);
 return <div className="home studio-home"><div className="page-heading"><div><div className="eyebrow">ITEM VIDEO STUDIO</div><h1>Upload once.<br/>Get the walkthrough.</h1><p className="muted">{demo?"Drop one screen recording or one body-camera video where you say each action out loud, then Generate video.":"Drop the floor recording, the WMS screen recording and the SOP, then Generate video."}</p>
 <div className="mode-toggle" role="radiogroup" aria-label="Video type">
  <button type="button" role="radio" aria-checked={!demo} className={!demo?"active":""} onClick={()=>choose("sop")}><b>Warehouse SOP Video</b><span>WMS + floor recording</span></button>
  <button type="button" role="radio" aria-checked={demo} className={demo?"active":""} onClick={()=>choose("demo")}><b>Demo video</b><span>Screen or body cam, for customers</span></button>
 </div></div></div>
 <section className="card intake" id="intake"><div className="eyebrow">NEW VIDEO</div><h2>{demo?"What should Item turn into a demo?":"What should Item turn into a video?"}</h2><form onSubmit={submit}><label className={`drop upload-drop ${files.length?"filled":""} ${dragging?"dragging":""}`} htmlFor="footage" role="button" tabIndex={0} aria-describedby="drop-help" onDragEnter={e=>{e.preventDefault();setDragging(true)}} onDragOver={e=>e.preventDefault()} onDragLeave={e=>{e.preventDefault();setDragging(false)}} onDrop={onDrop} onKeyDown={e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();document.getElementById("footage")?.click()}}}><strong>{ready?"Ready to generate":dragging?"Release to add files":demo?"Drop the recording and optional notes here":"Drop recordings and SOP here"}</strong><span id="drop-help" className="muted small">{demo?"One video where you say each action out loud · notes .md optional":"Floor video, WMS video and the SOP .md · you can drop them together"}</span><input id="footage" type="file" multiple accept="video/*,.mp4,.mov,.mkv,.webm,.md,.markdown" onChange={e=>{addFiles(e.target.files);e.target.value="";}}/></label>
 {fileNotice&&<div className="banner error" role="alert">{fileNotice}</div>}
 {(files.length>0||sop)&&<div className="file-chips">{files.map((f,i)=><button type="button" className="file-chip" key={`${f.name}-${i}`} title="Remove" onClick={()=>setFiles(files.filter((_,j)=>j!==i))}><span>{demo?(kind==="screen"?"Screen recording":"Body camera"):LABEL[i]}</span> {f.name} ×</button>)}{!demo&&files.length===2&&<button type="button" className="file-chip" title="Swap which video is the floor and which is WMS" onClick={()=>setFiles([files[1],files[0]])}>⇄ Swap</button>}{demo&&files.length===1&&<button type="button" className="file-chip" title="Tell Item what kind of recording this is" onClick={()=>setKind(kind==="screen"?"camera":"screen")}>{kind==="screen"?"⇄ It's body-cam footage":"⇄ It's a screen recording"}</button>}{sop&&<button type="button" className="file-chip" title="Remove" onClick={()=>setSop(null)}><span>{demo?"Notes":"SOP"}</span> {sop.name} ×</button>}</div>}
 <details><summary>Advanced options</summary><div className="advanced-options"><label className="field">{demo?"Video title":"Process name"}<input value={title} onChange={e=>setTitle(e.target.value)} placeholder={inferredTitle}/></label></div></details>
 {error&&<div className="banner error" role="alert">{error}</div>}<div className="row between generate-row"><span className="muted small">{missing.length?`Still needed: ${missing.join(", ")}`:demo?"Item opener → title → product demo → Item outro":"Item opener → title → real work → complete outro"}</span><button className="primary generate" disabled={busy||!ready}>{busy?"Uploading…":"Generate video →"}</button></div></form></section>
 {demo
  ?<section className="card how-it-works"><div><div className="eyebrow">AUTOMATIC CHECKS</div><h3>We handle the technical work</h3><p className="muted">The studio listens to what you said, takes a quick look at the screen, rewrites each line for customers in Jenny’s voice, captions it, speeds up long waits and checks the export. If something needs attention, you’ll get a clear message with a next action.</p></div><div className="step-list"><span>01 Upload</span><span>02 Listen & rewrite</span><span>03 Voice & render</span><span>04 Play & download</span></div></section>
  :<section className="card how-it-works"><div><div className="eyebrow">AUTOMATIC CHECKS</div><h3>We handle the technical work</h3><p className="muted">The studio syncs the two recordings, maps the work to your SOP, writes and voices the narration, captions it and checks the export. If something needs attention, you’ll get a clear message with a next action.</p></div><div className="step-list"><span>01 Upload</span><span>02 Sync & map</span><span>03 Voice & render</span><span>04 Play & download</span></div></section>}
 <section className="card"><div className="row between"><div><div className="eyebrow">YOUR VIDEOS</div><h2>Finished and in progress</h2></div><span className="muted small">{projects.length} local video{projects.length===1?"":"s"}</span></div>{projects.length?<div className="project-list">{projects.map(p=><div className="project-row" key={p.id}><a className="project-link" href={`#/p/${p.id}`}><span className="project-icon">▷</span><div className="grow"><b>{p.title}</b><div className="muted small">{p.mode==="demo"?"demo":"paired"} · {new Date(p.updated_at).toLocaleDateString()}</div></div><span className="pill">{stateLabel(p)}</span><span>→</span></a>{confirming===p.id?<span className="delete-confirm"><span className="small">{p.running?"Delete when it finishes?":"Delete this video?"}</span><button type="button" onClick={()=>setConfirming("")} disabled={deleting===p.id}>Cancel</button><button type="button" className="danger" onClick={()=>remove(p.id)} disabled={deleting===p.id}>{deleting===p.id?"Deleting…":"Delete"}</button></span>:<button type="button" className="delete-video" aria-label={`Delete ${p.title}`} title="Delete video" onClick={()=>setConfirming(p.id)}>Delete</button>}</div>)}</div>:<p className="muted">Your generated videos will appear here.</p>}</section></div>;
}
