import {database,bucket,commit,ApiError} from './server';
import {resolveArticle} from './article-resolver';
import {retrievePdf} from './pdf-transfer';
import {checkIdentity} from './identity';
import {motorArticle} from './motor-download';
const QUOTA=2*1024*1024*1024;
export async function incorporateWithPdf(p:any,actor:string,state:any,doi:string){
 const db=database(),row=await db.prepare("SELECT result FROM search_items WHERE project=? AND doi=? AND status IN ('done','partial')").bind(p.id,doi).first<any>();
 if(!row)throw new ApiError(400,'Consulte o DOI antes de incorporar.');
 if(state.articles.some((a:any)=>a.doi===doi))throw new ApiError(409,'DOI já incorporado.');
 if(state.articles.length>=2000)throw new ApiError(400,'Limite de 2.000 registros por projeto.');
 const metadata=JSON.parse(row.result||'null');if(!metadata?.found)throw new ApiError(400,'Metadados não encontrados.');if(metadata.motor?.ok)return incorporateFromMotor(p,actor,state,doi,metadata);if(!Array.isArray(metadata.pdfUrls)||!metadata.pdfUrls.length)throw new ApiError(422,'PDF gratuito não localizado. O artigo permanece fora do corpus.');
 let reserved=0,docId='',key='',committed=false;
 try{
  const resolved=await resolveArticle(doi,true),urls=[...new Set([...(resolved.pdfUrls||[]),...(metadata.pdfUrls||[])])].slice(0,12);
  if(!urls.length)throw new ApiError(422,'PDF gratuito não localizado. O artigo permanece pendente, fora do corpus.');
  let bytes:Uint8Array|undefined;const reasons:string[]=[],start=Date.now();
  for(const url of urls){const remaining=55000-(Date.now()-start);if(remaining<=0){reasons.push('Tempo limite da tentativa excedido.');break}try{bytes=await retrievePdf(url,AbortSignal.timeout(Math.min(14000,remaining)));break}catch(e:any){reasons.push((()=>{try{return new URL(url).hostname}catch{return 'Fonte'}})()+': '+e.message)}}
  if(!bytes)throw new ApiError(422,reasons.join('; ').slice(0,3000)||'Nenhuma fonte entregou PDF válido.');
  const quota=await db.prepare('UPDATE projects SET bytes=bytes+? WHERE id=? AND owner=? AND archived=0 AND bytes+?<=?').bind(bytes.length,p.id,actor,bytes.length,QUOTA).run();
  if(!quota.meta.changes)throw new ApiError(413,'O projeto atingiu o limite de 2 GB.');reserved=bytes.length;
  const articleId=crypto.randomUUID(),filename='artigo_'+(state.articles.length+1)+'_'+doi.replace(/[^a-z0-9.-]/gi,'_')+'.pdf';
  const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes.buffer as ArrayBuffer))).map(x=>x.toString(16).padStart(2,'0')).join('');
  docId=crypto.randomUUID();key=p.id+'/'+docId;
  await db.prepare('INSERT INTO documents(id,project,article,key,name,size,hash,status,created) VALUES(?,?,?,?,?,?,?,?,?)').bind(docId,p.id,articleId,key,filename,bytes.length,digest,'uploading',new Date().toISOString()).run();
  await bucket().put(key,bytes,{httpMetadata:{contentType:'application/pdf'}});
  // Identidade bibliográfica: o arquivo é um PDF, mas é *este* artigo?
  // `inspectPage` já lê o DOI que a página declara (`matched`/`mismatch`);
  // aqui esse indício vira veredito registrado junto do artigo.
  const veredito=checkIdentity(
   {doi,title:metadata.title||resolved.title,year:String(metadata.year||resolved.year||'')},
   {pageDoi:metadata.pageDoi||resolved.pageDoi,pageTitle:metadata.pageTitle,pageYear:metadata.pageYear?String(metadata.pageYear):undefined});
  state.articles.push({id:articleId,doi,title:metadata.title||resolved.title,authors:metadata.authors||resolved.authors,year:metadata.year||resolved.year,abstract:metadata.abstract||resolved.abstract||'',filename,identity:{ok:veredito.ok,method:veredito.method,score:veredito.score,detail:veredito.detail,checkedAt:new Date().toISOString()},source:{pdf:resolved.pdf,pdfUrls:urls,source:metadata.source,reason:'PDF validado e armazenado',reasonDetail:veredito.detail}});
  await db.prepare("UPDATE documents SET status='ready' WHERE id=? AND project=?").bind(docId,p.id).run();
  const result=await commit(p,actor,state,'incorporate');committed=true;reserved=0;
  await db.prepare('INSERT INTO pdf_attempts(id,project,article,status,reason,created) VALUES(?,?,?,?,?,?)').bind(crypto.randomUUID(),p.id,articleId,'saved','PDF validado e armazenado antes da incorporação',new Date().toISOString()).run().catch(()=>{});
  await db.prepare('UPDATE search_items SET error=NULL WHERE project=? AND doi=?').bind(p.id,doi).run().catch(()=>{});
  return {...result,document:{id:docId,article:articleId,name:filename,size:bytes.length,hash:digest,status:'ready'},article:articleId};
 }catch(e:any){
  if(!committed){if(key)await bucket().delete(key).catch(()=>{});if(docId)await db.prepare('DELETE FROM documents WHERE id=? AND project=?').bind(docId,p.id).run().catch(()=>{});if(reserved)await db.prepare('UPDATE projects SET bytes=bytes-? WHERE id=? AND owner=?').bind(reserved,p.id,actor).run().catch(()=>{});}
  const reason=e instanceof ApiError?e.message:'A fonte não entregou um PDF válido. Tente novamente.';
  await db.prepare('UPDATE search_items SET error=? WHERE project=? AND doi=?').bind(reason.slice(0,3000),p.id,doi).run().catch(()=>{});
  if(e instanceof ApiError)throw e;
  throw new ApiError(422,reason);
 }
}

// O motor já baixou, validou a identidade pelo conteúdo e extraiu o texto na
// fase 1. Aqui só se grava o registro: nenhum PDF vai para o R2 (no modo
// "baixar" ele está na pasta local; no "analisar" já foi descartado).
async function incorporateFromMotor(p:any,actor:string,state:any,doi:string,metadata:any){
 const db=database(),articleId=crypto.randomUUID(),at=new Date().toISOString();
 const filename='artigo_'+(state.articles.length+1)+'_'+doi.replace(/[^a-z0-9.-]/gi,'_')+'.pdf';
 state.articles.push(motorArticle(doi,metadata,articleId,filename,at));
 const result=await commit(p,actor,state,'incorporate');
 await db.prepare('INSERT INTO pdf_attempts(id,project,article,status,reason,created) VALUES(?,?,?,?,?,?)').bind(crypto.randomUUID(),p.id,articleId,'saved','PDF validado pelo motor (modo '+metadata.motor.modo+')',at).run().catch(()=>{});
 await db.prepare('UPDATE search_items SET error=NULL WHERE project=? AND doi=?').bind(p.id,doi).run().catch(()=>{});
 return {...result,article:articleId};
}
