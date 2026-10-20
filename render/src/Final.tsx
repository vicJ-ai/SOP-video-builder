// Owned Item composition: integer frames, constant source trims, no fake device chrome.
import React,{useEffect,useState} from "react";
import {AbsoluteFill, Audio, Easing, Freeze, Img, interpolate, OffthreadVideo, Sequence, staticFile, useCurrentFrame,delayRender,continueRender,cancelRender} from "remotion";
import {Progress} from "@/components/remocn/progress";
import {Backdrop} from "@/components/remocn/backdrop";
import type {FinalProps, SegmentProp, SourceRef} from "./types";
import {BrandAnimation} from "./Brand";
import {FitText} from "./FitText";
export const ITEM_BOUNDS={titleLogo:{top:870,width:185,height:73,bottom:943},captionTop:981};
const C={white:"#FFFFFF",black:"#000000",purple:"#6B46C1",orange:"#F97316",muted:"#595959"};
const Fonts=()=>{
 const [handle]=useState(()=>delayRender("Load all Satoshi faces"));
 useEffect(()=>{let active=true;Promise.all([400,500,700].map(w=>document.fonts.load(`${w} 30px Satoshi`)))
  .then(faces=>{if(faces.some(face=>!face.length))throw new Error("Satoshi font unavailable");if(active)continueRender(handle)})
  .catch(error=>{if(active)cancelRender(error)});return()=>{active=false}},[handle]);
 return <style>{[400,500,700].map(w=>`@font-face{font-family:Satoshi;src:url('${staticFile(`fonts/Satoshi-${w}.woff2`)}');font-weight:${w};font-display:block}`).join("")}</style>;
};
const Panel=({source,segments,role,left,width}:{source:SourceRef;segments:SegmentProp[];role:"physical"|"system";left:number;width:number})=>
 <div style={{position:"absolute",left,top:162,width,height:819,background:"#F2F2F2",overflow:"hidden"}}>
 {segments.filter(s=>(s.kind==="source"||s.kind==="hold")&&(!s.availableRoles||s.availableRoles.includes(role))).map(s=>{
 const video=<OffthreadVideo src={staticFile(source.src)} trimBefore={role==="physical"?s.physicalStartFrame:s.systemStartFrame} muted playbackRate={s.playbackRate??1} style={{width:"100%",height:"100%",objectFit:"contain"}}/>;
 return <Sequence key={s.fromFrame} from={s.fromFrame} durationInFrames={s.durationInFrames} layout="none">
 {s.kind==="hold"?<Freeze frame={0}>{video}</Freeze>:video}
 </Sequence>;})}</div>;
export const ItemTitle=({props}:{props:FinalProps})=><AbsoluteFill style={{background:C.white,color:C.black}}>
 <div style={{position:"absolute",left:132,top:126,width:9,height:155,background:C.purple}}/>
 <div style={{position:"absolute",left:177,top:121}}><FitText text={props.title} label="Process title" width={1510} height={179} maxFont={76} minFont={44} maxLines={2}/></div>
 <div style={{position:"absolute",left:178,top:350,width:1450,fontSize:28,lineHeight:1.45,color:C.muted}}>{props.subtitle}</div>
 <div style={{position:"absolute",left:178,top:477,fontSize:16,fontWeight:700,color:C.purple,letterSpacing:1.4}}>DEMONSTRATED WORKFLOW</div>
 {(props.workflow??[]).slice(0,4).map((text,i)=><div key={i} style={{position:"absolute",left:178+i*405,top:533,width:355}}>
 <div style={{fontSize:25,fontWeight:700,color:i===3?C.orange:C.purple}}>{i+1}</div><div style={{marginTop:16,fontSize:24,fontWeight:500,lineHeight:1.3}}>{text}</div></div>)}
 {props.demo&&<div style={{position:"absolute",left:178,top:770,fontSize:21,color:C.muted}}>DEMO · synthetic test source · not operational evidence</div>}
 <Img src={staticFile("brand/item-logo.svg")} style={{position:"absolute",left:178,top:870,width:185,height:73,objectFit:"contain"}}/>
 </AbsoluteFill>;
export const Final:React.FC<FinalProps>=(props)=>{
 const frame=useCurrentFrame();
 const segment=props.segments.find(s=>frame>=s.fromFrame&&frame<s.fromFrame+s.durationInFrames);
 let index=-1;props.layout.forEach((k,i)=>{if(frame>=k.fromFrame&&frame<k.toFrame)index=i;});
 const k=props.layout[index],previous=props.layout[Math.max(0,index-1)];
 const shares={system:.66,physical:.34,split:.5};
 const share=k?interpolate(frame,[k.fromFrame,k.fromFrame+18],[shares[previous?.focus??"split"],shares[k.focus]],{extrapolateLeft:"clamp",extrapolateRight:"clamp",easing:Easing.bezier(.4,0,.2,1)}):.5;
 const roles=segment?.availableRoles??[...(props.physical?["physical"]:[]),...(props.system?["system"]:[])];
 const physical=props.physical&&roles.includes("physical"),system=props.system&&roles.includes("system"),paired=!!physical&&!!system;
 const systemWidth=paired?Math.round(1756*share):1780,physicalWidth=paired?1756-systemWidth:1780;
 const caption=props.captions.find(c=>frame>=(c.fromFrame??Math.round(c.startMs*props.fps/1000))&&frame<(c.endFrame??Math.round(c.endMs*props.fps/1000)));
 const source=segment?.kind==="source"||segment?.kind==="hold",bookend=segment?.kind==="intro"||segment?.kind==="outro";
 return <AbsoluteFill style={{fontFamily:"Satoshi,Arial,sans-serif",background:bookend?C.black:C.white}}><Fonts/>
 {source&&<Backdrop fill={{type:"color",value:C.white}} padding={0} radius={0} shadow="">
 {physical&&props.physical&&<Panel source={props.physical} role="physical" segments={props.segments} left={70} width={physicalWidth}/>}
 {system&&props.system&&<Panel source={props.system} role="system" segments={props.segments} left={paired?70+physicalWidth+24:70} width={systemWidth}/>}
 <div style={{position:"absolute",left:70,top:28,color:C.muted}}><FitText text={props.title.toUpperCase()} label="Process header" width={1550} height={22} maxFont={15} minFont={13} maxLines={1} letterSpacing={1.4}/></div>
 <div style={{position:"absolute",left:70,top:59}}><FitText text={k?.heading??"Observed workflow"} label="Action heading" width={1550} height={64} maxFont={40} minFont={28} maxLines={2}/></div>
 <div style={{position:"absolute",left:72,top:126,fontSize:14,color:C.muted}}>{props.demo?"DEMO · ":""}{paired?"PHYSICAL + SYSTEM":physical?"PHYSICAL OPERATION · SINGLE RECORDING":"SYSTEM OPERATION · SINGLE RECORDING"}</div>
 <div style={{position:"absolute",right:230,top:126}}><Progress width={270} value={100*frame/props.durationInFrames} theme={{primary:C.purple,muted:"#E6E6E6"}}/></div>
 <Img src={staticFile("brand/item-logo.svg")} style={{position:"absolute",right:70,top:56,width:140,height:56,objectFit:"contain"}}/>
 {(segment?.playbackRate??1)>1&&<div style={{position:"absolute",right:90,top:182,padding:"8px 18px",borderRadius:999,background:"rgba(0,0,0,.72)",color:C.white,fontSize:26,fontWeight:700,letterSpacing:.5}}>⏩ {Math.round(segment!.playbackRate!)}×</div>}
 </Backdrop>}
 {props.segments.filter(s=>s.kind==="title"||(s.kind==="intro"&&props.template!=="item-v1")).map(s=><Sequence key={s.fromFrame} from={s.fromFrame} durationInFrames={s.durationInFrames}><ItemTitle props={props}/></Sequence>)}
 {!bookend&&caption&&<div style={{position:"absolute",top:981,left:0,right:0,height:99,background:C.white,display:"flex",alignItems:"center",justifyContent:"center",padding:"6px 110px"}}><div style={{fontSize:29,fontWeight:500,lineHeight:1.3,color:C.black,textAlign:"center",maxWidth:1680}}>{caption.text}</div></div>}
 {props.cues.map(c=><Sequence key={c.id} from={c.fromFrame} durationInFrames={c.durationInFrames} layout="none"><Audio src={staticFile(c.src)}/></Sequence>)}
 {props.template==="item-v1"&&props.segments.filter(s=>s.kind==="intro"||s.kind==="outro").map(s=><Sequence key={s.fromFrame} from={s.fromFrame} durationInFrames={s.durationInFrames}><BrandAnimation clip={s.kind as "intro"|"outro"} frameCount={s.durationInFrames} frameDirectory={props.brandFrames?.[s.kind as "intro"|"outro"]}/></Sequence>)}
 </AbsoluteFill>;
};
