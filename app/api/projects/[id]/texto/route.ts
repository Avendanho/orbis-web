import {identity,owned,database,bucket,ok,fail,ApiError,requireRole} from '@/lib/server';
import {textKey} from '@/lib/motor-download';
const QUOTA=2*1024*1024*1024,MAX_TEXT=2*1024*1024;
function article(p:any,r:Request){const s=JSON.parse(p.state),a=s.articles.find((x:any)=>x.id===new URL(r.url).searchParams.get('article'));if(!a?.texto?.key)throw new ApiError(404,'Este artigo não tem texto extraído.');return a;}
export async function GET(r:Request,{params}:any){try{const actor=await identity(r),{id}=await params,p=await owned(id,actor),a=article(p,r);const o=await bucket().get(a.texto.key);if(!o)throw new ApiError(404,'Texto indisponível.');return new Response(o.body,{headers:{'content-type':'text/plain; charset=utf-8','cache-control':'private, no-store','x-content-type-options':'nosniff'}})}catch(e){return fail(e)}}
// Restauração de backup: grava o texto no endereço que a ação `restore` já
// recalculou para este projeto. Não mexe no estado, como o upload de PDF.
export async function POST(r:Request,{params}:any){let reserved=0,project='';try{
 const actor=await identity(r),{id}=await params;project=id;const p=await owned(id,actor);requireRole(p,['owner','editor']);const a=article(p,r);
 if(a.texto.key!==await textKey(id,a.doi||a.id))throw new ApiError(409,'O texto não pertence a este projeto.');
 const bytes=new TextEncoder().encode(await r.text());if(!bytes.length)throw new ApiError(400,'Texto vazio.');if(bytes.length>MAX_TEXT)throw new ApiError(413,'Texto acima de 2 MB.');
 if(await bucket().head(a.texto.key))return ok({existing:true});
 const db=database(),quota=await db.prepare('UPDATE projects SET bytes=bytes+? WHERE id=? AND archived=0 AND bytes+?<=?').bind(bytes.length,id,bytes.length,QUOTA).run();if(!quota.meta.changes)throw new ApiError(413,'O projeto atingiu o limite de 2 GB.');reserved=bytes.length;
 await bucket().put(a.texto.key,bytes,{httpMetadata:{contentType:'text/plain; charset=utf-8'}});reserved=0;
 return ok({size:bytes.length});
}catch(e){if(reserved)await database().prepare('UPDATE projects SET bytes=MAX(0,bytes-?) WHERE id=?').bind(reserved,project).run().catch(()=>{});return fail(e)}}
