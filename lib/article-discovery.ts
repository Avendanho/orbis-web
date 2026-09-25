import {checkedFetch} from './remote';
import { publicPdfUrl } from './pdf-transfer';
import { normalizeDoi } from './doi-batch';
export function safeUrl(value:unknown,base?:string):string|null {if(typeof value!=='string')return null;try{return publicPdfUrl(new URL(value,base).href).href;}catch{return null;}}
function entities(s:string){return s.replace(/&amp;/gi,'&').replace(/&quot;/gi,'"').replace(/&#39;|&apos;/gi,"'").replace(/&#(\d+);/g,(_,n)=>String.fromCharCode(Number(n)));}
function attrs(tag:string){const a:Record<string,string>={};for(const m of tag.matchAll(/([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/g))a[m[1].toLowerCase()]=entities(m[2]??m[3]??m[4]);return a;}
export function parseArticlePage(html:string,url:string,doi:string){
 const metas=(html.match(/<meta\b[^>]*>/gi)||[]).map(attrs);
 const pageDois=metas.filter(m=>['citation_doi','dc.identifier','dc.identifier.doi','prism.doi'].includes((m.name||m.property||'').toLowerCase())).map(m=>normalizeDoi(m.content||'').toLowerCase()).filter(d=>d.startsWith('10.'));
 const matched=pageDois.includes(doi.toLowerCase());const mismatch=pageDois.length>0&&!matched;
 let free=false,paid=false;
 function hasDoi(value:unknown):boolean{
  if(typeof value==='string')return normalizeDoi(value).toLowerCase()===doi.toLowerCase();
  if(Array.isArray(value))return value.some(hasDoi);
  if(value&&typeof value==='object')return Object.values(value).some(hasDoi);
  return false;
 }
 function walk(value:unknown){
  if(Array.isArray(value)){value.forEach(walk);return;}
  if(!value||typeof value!=='object')return;
  const obj=value as Record<string,unknown>;
  const types=Array.isArray(obj['@type'])?obj['@type']:[obj['@type']];
  const identity=matched||hasDoi(obj.identifier)||hasDoi(obj.sameAs);
  if(identity&&types.some(t=>['ScholarlyArticle','Article','MedicalScholarlyArticle'].includes(String(t)))){if(obj.isAccessibleForFree===true||obj.isAccessibleForFree==='true')free=true;if(obj.isAccessibleForFree===false||obj.isAccessibleForFree==='false')paid=true;}
  if(obj['@graph'])walk(obj['@graph']);
 }
 for(const script of html.matchAll(/<script\b[^>]*type\s*=\s*["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi)){try{walk(JSON.parse(script[1]));}catch{}}
 const urls:string[]=[];
 if(!mismatch){
  for(const m of metas)if(['citation_pdf_url','wkhealth_pdf_url','eprints.document_url'].includes((m.name||m.property||'').toLowerCase())){const u=safeUrl(m.content,url);if(u)urls.push(u);}
  for(const tag of html.match(/<link\b[^>]*>/gi)||[]){const a=attrs(tag);if(a.type==='application/pdf'&&a.rel?.includes('alternate')){const u=safeUrl(a.href,url);if(u)urls.push(u);}}
 }
 // `pageDoi` é o que a própria página declara. Já era calculado para decidir
 // `matched`/`mismatch`, mas era descartado; a validação de identidade
 // precisa do valor, não só do veredito.
 return {pdfUrls:[...new Set(urls)].slice(0,4),free:!mismatch&&free,paid:!mismatch&&paid,matched,mismatch,pageDoi:pageDois[0]||''};
}
export async function inspectPage(url:string,doi:string){
 const signal=AbortSignal.timeout(18000);let target=publicPdfUrl(url);
 for(let i=0;i<6;i++){
  const r=await checkedFetch(target,{redirect:'manual',signal,headers:{Accept:'text/html,application/pdf;q=0.9'}});
  if([301,302,303,307,308].includes(r.status)){const loc=r.headers.get('location');await r.body?.cancel();if(!loc)throw new Error('redirecionamento sem destino');target=publicPdfUrl(new URL(loc,target).href);continue;}
  if(!r.ok){await r.body?.cancel();throw new Error(r.status===403?'bloqueio de acesso automático':r.status===429?'limite de consultas':'HTTP '+r.status);}
  if(r.headers.get('content-type')?.includes('application/pdf')){await r.body?.cancel();return {pdfUrls:[target.href],free:true,paid:false,matched:true,mismatch:false,pageDoi:'',url:target.href};}
  const reader=r.body?.getReader();if(!reader)throw new Error('página vazia');let size=0;const chunks:Uint8Array[]=[];
  try {while(size<1024*1024){const {done,value}=await reader.read();if(done)break;const chunk=value.subarray(0,1024*1024-size);chunks.push(chunk);size+=chunk.length;}}finally{await reader.cancel().catch(()=>{});}
  const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
  return {...parseArticlePage(new TextDecoder().decode(bytes),target.href,doi),url:target.href};
 }
 throw new Error('muitos redirecionamentos');
}
export async function probeFreePdf(url:string){
 const signal=AbortSignal.timeout(6000);let target=publicPdfUrl(url);
 for(let i=0;i<5;i++){
  const r=await checkedFetch(target,{redirect:'manual',signal,headers:{Accept:'application/pdf'}});
  if([301,302,303,307,308].includes(r.status)){const loc=r.headers.get('location');await r.body?.cancel();if(!loc)return false;target=publicPdfUrl(new URL(loc,target).href);continue;}
  if(!r.ok){await r.body?.cancel();return false;}
  const reader=r.body?.getReader();if(!reader)return false;
  try {let prefix='';while(prefix.length<1024){const chunk=await reader.read();if(chunk.done)break;prefix+=new TextDecoder().decode(chunk.value.subarray(0,1024-prefix.length));if(prefix.includes('%PDF-'))return true;}return false;}finally{await reader.cancel().catch(()=>{});}
 }
 return false;
}
export function accessReason(args:{pdf:boolean;oa:boolean;paid:boolean;closed:boolean;found:boolean;issues:string[]}){
 if(args.pdf)return {reasonCode:'pdf_found',reason:'PDF gratuito localizado',reasonDetail:'Há uma ou mais fontes gratuitas para tentar o download.'};
 if(args.oa)return {reasonCode:'oa_without_pdf',reason:'Gratuito, mas PDF não localizado',reasonDetail:'Uma fonte indica acesso gratuito, mas não foi possível obter um link direto de PDF. Tente a versão gratuita indicada.'};
 if(args.paid)return {reasonCode:'publisher_paywall',reason:'Artigo pago na editora',reasonDetail:'A página da editora declara acesso não gratuito. Não localizamos uma cópia gratuita nas demais fontes; isso não exclui outras versões em repositórios.'};
 if(args.issues.length)return {reasonCode:'search_incomplete',reason:'Busca incompleta',reasonDetail:'Não foi possível concluir todas as consultas: '+args.issues.join('; ')+'. Não há confirmação de que o artigo seja pago.'};
 if(args.closed)return {reasonCode:'possibly_paid',reason:'Possível acesso pago',reasonDetail:'O OpenAlex classifica o artigo como fechado. Essa indicação pode estar desatualizada e não confirma ausência de versões gratuitas.'};
 return {reasonCode:args.found?'pdf_not_found':'article_not_found',reason:args.found?'PDF gratuito não localizado':'Artigo não localizado',reasonDetail:args.found?'Nenhuma fonte consultada forneceu um PDF gratuito. O acesso pago não foi confirmado.':'Não encontramos metadados para este DOI. Confira o identificador; a ausência de resultado não significa que o artigo seja pago.'};
}
