import {identity,owned,database,body,ok,fail,ApiError,requireRole} from '@/lib/server';
import {resolveArticle,s2ApiKey} from '@/lib/article-resolver';
import {prefetchS2} from '@/lib/sources/semantic-scholar';
import {isCentralRecord} from '@/lib/record-kind';
import {doisFrom} from '@/lib/sources/pubmed-search';
import {searchBase,isBase,BASES} from '@/lib/sources/bibliographic-search';
import {normalizeList} from '@/lib/domain';
export async function POST(r:Request,{params}:any){try{const actor=await identity(r),{id}=await params;const p=await owned(id,actor);requireRole(p,['owner','editor']);if(p.archived)throw new ApiError(409,'Projeto em exclusão.');const b=await body(r),db=database(),at=new Date().toISOString();
// `pubmed` continua aceito: é a ação que a interface usava antes das outras bases.
if(b.action==='pubmed'||b.action==='database'){
 const base=b.action==='pubmed'?'pubmed':b.base;
 if(!isBase(base))throw new ApiError(400,'Base de busca desconhecida.');
 const nome=BASES[base].label;
 const termo=String(b.term||'').trim();
 if(!termo)throw new ApiError(400,'Informe uma expressão de busca para '+nome+'.');
 const g=globalThis as any,env=(k:string)=>g[k]||process.env?.[k];
 let achado;
 try{achado=await searchBase(base,termo,Number(b.limit)||500,{NCBI_API_KEY:env('NCBI_API_KEY'),NCBI_EMAIL:env('NCBI_EMAIL'),EMBASE_API_KEY:env('EMBASE_API_KEY'),ELSEVIER_API_KEY:env('ELSEVIER_API_KEY'),EMBASE_INST_TOKEN:env('EMBASE_INST_TOKEN'),ELSEVIER_INST_TOKEN:env('ELSEVIER_INST_TOKEN')});}
 catch(e:any){throw new ApiError(502,e?.message||'Falha ao consultar '+nome+'.');}
 const dois=doisFrom(achado.records);
 if(!dois.length)throw new ApiError(404,achado.total?'Os '+achado.total+' resultados encontrados não têm DOI, então não podem entrar no lote.':'Nenhum resultado para esta expressão.');
 // Mesma fila da lista colada: a busca muda a origem, não o fluxo.
 for(let n=0;n<dois.length;n+=50)await db.batch(dois.slice(n,n+50).map(doi=>db.prepare('INSERT INTO search_items(project,doi,status,updated) VALUES(?,?,?,?) ON CONFLICT(project,doi) DO NOTHING').bind(id,doi,'waiting',at)));
 return ok({count:dois.length,total:achado.total,withoutDoi:achado.withoutDoi,term:termo,base});
}
// Uma chamada em lote ao Semantic Scholar antes das consultas individuais: sem
// isso, cada DOI disputa a cota anônima compartilhada e recebe 429.
if(b.action==='prefetch'){
 const rows=await db.prepare("SELECT doi FROM search_items WHERE project=? AND (status!='done' OR COALESCE(json_array_length(json_extract(result,'$.pdfUrls')),0)=0)").bind(id).all<{doi:string}>();
 const dois=(rows.results||[]).map(x=>x.doi).filter(d=>!isCentralRecord(d));
 return ok({count:dois.length,cached:dois.length?await prefetchS2(dois,{apiKey:s2ApiKey()}):0});
}
if(b.action==='queue'){const dois=normalizeList(String(b.text||''));if(!dois.length||dois.length>500)throw new ApiError(400,'Informe de 1 a 500 DOIs válidos.');for(let n=0;n<dois.length;n+=50)await db.batch(dois.slice(n,n+50).map(doi=>db.prepare('INSERT INTO search_items(project,doi,status,updated) VALUES(?,?,?,?) ON CONFLICT(project,doi) DO NOTHING').bind(id,doi,'waiting',at)));return ok({count:dois.length});}
const doi=String(b.doi||'');const item=await db.prepare('SELECT * FROM search_items WHERE project=? AND doi=?').bind(id,doi).first<any>();if(!item)throw new ApiError(404,'DOI não está no lote.');if(item.status==='done'&&!b.retry)return ok(JSON.parse(item.result));if(item.status==='running'&&Date.parse(item.updated)>Date.now()-180000)throw new ApiError(409,'Este DOI já está sendo consultado.');const lease=crypto.randomUUID();const claim=await db.prepare("UPDATE search_items SET status='running',lease=?,updated=? WHERE project=? AND doi=? AND (status!='running' OR updated<?)").bind(lease,at,id,doi,new Date(Date.now()-180000).toISOString()).run();if(!claim.meta.changes)throw new ApiError(409,'Consulta já iniciada.');
try{const found=await resolveArticle(doi);
 // O resultado do motor (PDF já validado, texto já cobrado na cota) sobrevive à reconsulta.
 let old:any=null;try{old=JSON.parse(item.result||'null')}catch{}const article=old?.motor?{...found,motor:old.motor}:found;await db.prepare("UPDATE search_items SET status=?,result=?,error=NULL,lease=NULL,updated=? WHERE project=? AND doi=? AND lease=?").bind(article.partial&&!article.pdf?'partial':'done',JSON.stringify(article),new Date().toISOString(),id,doi,lease).run();return ok(article)}catch(e:any){await db.prepare("UPDATE search_items SET status='error',error=?,lease=NULL,updated=? WHERE project=? AND doi=? AND lease=?").bind(e.message,new Date().toISOString(),id,doi,lease).run();throw e}
}catch(e){return fail(e)}}
