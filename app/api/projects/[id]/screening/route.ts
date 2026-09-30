import {identity,owned,database,body,ok,fail,ApiError,requireRole} from '@/lib/server';
import {resolveArticle} from '@/lib/article-resolver';
import {pickProvider,callWithRetry,LlmCallFailed} from '@/lib/ai-provider';
import {SYSTEM_PROMPT,buildUserPrompt,chunk,tamanhoLote} from '@/lib/ai-runner';
import {iaValores} from '@/lib/ai-env';
import {parseAI,normalizeAIResponse} from '@/lib/ai-analysis';
import {registroDe,situacao,decidir,pacoteIA,sugestaoDe,sugestaoAtual,aceitar,type Registro} from '@/lib/screening';
import {linhasDoProjeto,linhaDe,gravarDecisao,gravarSugestao} from '@/lib/screening-db';

// Triagem de títulos e resumos antes do download. A leitura vem no GET do
// projeto (`screening`); aqui ficam só as ações que gravam.
const MAX_ACEITAR=500,GRUPO_IA=5;
const lerLinha=(r:any)=>r?{...r,result:r.result?JSON.parse(r.result):null}:null;

async function registro(db:any,project:string,doi:string):Promise<Registro>{
 const reg=registroDe(lerLinha(await db.prepare('SELECT doi,status,result FROM search_items WHERE project=? AND doi=?').bind(project,doi).first()));
 if(!reg)throw new ApiError(404,'Registro não encontrado no lote (ou ainda sem título e resumo consultados).');
 return reg;
}

export async function POST(r:Request,{params}:any){try{
 const actor=await identity(r),{id}=await params,p=await owned(id,actor),b=await body(r),db=database();
 if(p.archived)throw new ApiError(409,'Projeto em exclusão.');
 const protocolo=JSON.parse(p.state).protocol;
 const doi=String(b.doi||'').trim().toLowerCase();

 if(b.action==='decide'){
  requireRole(p,['owner','editor','reviewer']);
  await registro(db,id,doi);
  let d;try{d=decidir(protocolo,b.answers,b.reason,b.reasons,actor)}catch(e:any){throw new ApiError(400,e.message)}
  await gravarDecisao(db,id,doi,d).run();
  return ok({doi,decision:d.decision,version:d.version});
 }

 if(b.action==='acceptAI'){
  requireRole(p,['owner','editor','reviewer']);
  if(!protocolo.approved)throw new ApiError(400,'Aprove o protocolo antes da triagem.');
  const dois=Array.isArray(b.dois)?[...new Set(b.dois.map((x:any)=>String(x||'').trim().toLowerCase()))]:[];
  if(!dois.length||dois.length>MAX_ACEITAR)throw new ApiError(400,'Selecione de 1 a '+MAX_ACEITAR+' sugestões para aceitar.');
  // Tudo é conferido antes de gravar qualquer coisa: ou aceita todas, ou nenhuma.
  const decisoes=[];
  for(const d of dois as string[]){
   const reg=await registro(db,id,d),l=await linhaDe(db,id,d);
   if(situacao(l,protocolo.version)!=='pendente')throw new ApiError(409,'Um registro selecionado já tem decisão na versão atual do protocolo.');
   if(!sugestaoAtual(l?.ai,reg,protocolo))throw new ApiError(409,'Uma sugestão selecionada não existe ou está desatualizada (protocolo, critérios ou resumo mudaram).');
   try{decisoes.push({doi:d,decisao:aceitar(l!.ai!,protocolo,actor)})}catch(e:any){throw new ApiError(400,e.message)}
  }
  for(let n=0;n<decisoes.length;n+=50)await db.batch(decisoes.slice(n,n+50).map(x=>gravarDecisao(db,id,x.doi,x.decisao)));
  return ok({applied:decisoes.map(x=>({doi:x.doi,decision:x.decisao.decision}))});
 }

 if(b.action==='ai'){
  requireRole(p,['owner','editor']);
  if(!protocolo.approved)throw new ApiError(400,'Aprove o protocolo antes da triagem.');
  const v=iaValores(),provider=await pickProvider(v);
  if(!provider)throw new ApiError(400,'Nenhum provedor de IA disponível: o Ollama local não respondeu e não há chave de nuvem configurada. Configure um em Configurações ou use a triagem manual.');
  const limite=tamanhoLote(b.limit,v.ORBIS_IA_LOTE);
  const linhas=new Map((await linhasDoProjeto(db,id)).map(l=>[l.doi,l]));
  const rows=(await db.prepare("SELECT doi,status,result FROM search_items WHERE project=? AND status IN ('done','partial') ORDER BY updated").bind(id).all()).results||[];
  const pendentes=(rows.map(lerLinha).map(registroDe).filter(Boolean) as Registro[])
   .filter(reg=>situacao(linhas.get(reg.doi),protocolo.version)==='pendente'&&!sugestaoAtual(linhas.get(reg.doi)?.ai,reg,protocolo));
  const lote=pendentes.slice(0,limite),failures:{dois:string[];reason:string}[]=[];let analysed=0;
  for(const grupo of chunk(lote,GRUPO_IA)){
   const pkg=pacoteIA(id,protocolo,grupo);
   try{
    const texto=await callWithRetry(()=>provider.complete(SYSTEM_PROMPT,buildUserPrompt(pkg,pkg.items)));
    const resposta=normalizeAIResponse(parseAI(texto),'triagem'),itens:any[]=Array.isArray(resposta?.items)?resposta.items:[];
    const at=new Date().toISOString(),respondidos=new Set<string>();
    for(const item of itens){
     const reg=grupo.find(x=>x.doi===String(item?.article_id||item?.arquivo||'').trim().toLowerCase());
     if(!reg||respondidos.has(reg.doi))continue;
     respondidos.add(reg.doi);
     try{await gravarSugestao(db,id,reg.doi,sugestaoDe(item,protocolo,reg,provider.name,provider.model,at)).run();analysed++}
     catch(e:any){failures.push({dois:[reg.doi],reason:'Resposta inválida da IA: '+e.message})}
    }
    const faltaram=grupo.filter(x=>!respondidos.has(x.doi)).map(x=>x.doi);
    if(faltaram.length)failures.push({dois:faltaram,reason:'A IA não devolveu estes registros.'});
   }catch(e:any){
    // Falha técnica não vira sugestão: o registro continua pendente.
    failures.push({dois:grupo.map(x=>x.doi),reason:e instanceof LlmCallFailed?e.message:'Resposta inválida do provedor: '+(e?.message||e)});
   }
  }
  return ok({analysed,remaining:pendentes.length-lote.length,failures,provider:provider.name,model:provider.model});
 }

 if(b.action==='refreshAbstract'){
  requireRole(p,['owner','editor']);
  await registro(db,id,doi);
  const row=lerLinha(await db.prepare('SELECT doi,status,result FROM search_items WHERE project=? AND doi=?').bind(id,doi).first());
  const found=await resolveArticle(doi,true),result={...row.result};
  for(const k of ['title','authors','year','journal'] as const)if(!String(result[k]||'').trim()&&(found as any)[k])result[k]=(found as any)[k];
  if(String(found.abstract||'').trim().length>String(result.abstract||'').trim().length){result.abstract=found.abstract;result.abstractSource=found.abstractSource||'';}
  await db.prepare('UPDATE search_items SET result=?,updated=? WHERE project=? AND doi=?').bind(JSON.stringify(result),new Date().toISOString(),id,doi).run();
  return ok({registro:registroDe({...row,result}),abstractFound:!!String(result.abstract||'').trim()});
 }

 throw new ApiError(400,'Ação de triagem desconhecida.');
}catch(e){return fail(e)}}
