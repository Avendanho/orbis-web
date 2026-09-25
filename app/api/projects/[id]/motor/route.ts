import {env} from 'cloudflare:workers';
import {identity,owned,database,bucket,body,ok,fail,ApiError,requireRole} from '@/lib/server';
import {engineStatus,downloadViaEngine,EngineRefused} from '@/lib/python-bridge';
import {isMode,textKey,motorSummary} from '@/lib/motor-download';
const QUOTA=2*1024*1024*1024;
// A interface pergunta isto para decidir se oferece os dois modos.
export async function GET(r:Request,{params}:any){try{const actor=await identity(r),{id}=await params;await owned(id,actor);return ok(await engineStatus(env))}catch(e){return fail(e)}}
// Fase 1 do lote: o motor busca o PDF de UM artigo. Não toca no estado do
// projeto — só na linha do DOI —, por isso o navegador pode chamar vários em
// paralelo sem conflito de revisão.
export async function POST(r:Request,{params}:any){try{
 const actor=await identity(r),{id}=await params,p=await owned(id,actor);requireRole(p,['owner','editor']);if(p.archived)throw new ApiError(409,'Projeto em exclusão.');
 const b=await body(r),doi=String(b.doi||''),modo=b.modo;if(!isMode(modo))throw new ApiError(400,'Modo de download inválido.');
 const db=database(),row=await db.prepare('SELECT result FROM search_items WHERE project=? AND doi=?').bind(id,doi).first<any>();if(!row)throw new ApiError(404,'DOI não está no lote.');
 const meta=JSON.parse(row.result||'null');if(!meta?.found)throw new ApiError(400,'Consulte o DOI antes de buscar o PDF.');
 let res;
 try{res=await downloadViaEngine(env,{doi,projeto:id,modo,titulo:meta.title||'',autor:meta.authors||'',ano:String(meta.year||''),periodico:meta.journal||''})}
 catch(e:any){if(e instanceof EngineRefused)throw new ApiError(e.status===507?507:502,e.message);throw e}
 if(!res)throw new ApiError(503,'O motor não está no ar. O lote foi pausado; suba o motor e retome.');
 const at=new Date().toISOString();
 if(!res.ok){
  const motor=motorSummary(res,modo,null);
  await db.prepare('UPDATE search_items SET result=?,error=?,updated=? WHERE project=? AND doi=?').bind(JSON.stringify({...meta,motor}),motor.erro,at,id,doi).run();
  return ok({ok:false,motor});
 }
 let texto=null;
 if(res.texto){
  // Mesmo endereço por DOI: repetir troca o texto e ajusta só a diferença da cota.
  const bytes=new TextEncoder().encode(res.texto),key=await textKey(id,doi),previous=meta.motor?.texto?.key===key?Number(meta.motor.texto.bytes||0):0,delta=bytes.length-previous;
  const quota=await db.prepare('UPDATE projects SET bytes=MAX(0,bytes+?) WHERE id=? AND archived=0 AND bytes+?<=?').bind(delta,id,delta,QUOTA).run();
  if(!quota.meta.changes)throw new ApiError(413,'O projeto atingiu o limite de 2 GB.');
  try{await bucket().put(key,bytes,{httpMetadata:{contentType:'text/plain; charset=utf-8'}})}
  catch(e){await db.prepare('UPDATE projects SET bytes=MAX(0,bytes-?) WHERE id=?').bind(delta,id).run().catch(()=>{});throw e}
  texto={key,bytes:bytes.length};
 }
 const motor=motorSummary(res,modo,texto);
 await db.prepare('UPDATE search_items SET result=?,error=NULL,updated=? WHERE project=? AND doi=?').bind(JSON.stringify({...meta,motor}),at,id,doi).run();
 return ok({ok:true,motor});
}catch(e){return fail(e)}}
