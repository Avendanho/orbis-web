// Semantic Scholar com lote, nova tentativa e chave opcional.
//
// Sem chave, todo cliente do mundo divide a mesma cota do Semantic Scholar; o
// 429 aparece conforme o movimento dos outros, não o nosso. Três defesas:
//  - lote: um POST /paper/batch para até 500 DOIs no início, em vez de uma
//    chamada por DOI (o resultado fica em cache para a consulta individual);
//  - nova tentativa: 429/5xx/queda repetem até 3 vezes, respeitando
//    Retry-After (com teto, para não travar o lote);
//  - chave (SEMANTIC_SCHOLAR_API_KEY): cota própria de 1 pedido/s.
// A fonte continua valendo a pena: é ela que às vezes aponta o preprint.
const API='https://api.semanticscholar.org/graph/v1/paper/';
const FIELDS='title,authors,year,venue,openAccessPdf,isOpenAccess';
const TETO_ESPERA=10000,LOTE=500,VALIDADE=60*60*1000;
// Consulta individual: 3 tentativas, esperas a partir de 0,5 s. Lote: vale por
// até 500 consultas, então insiste mais — 5 tentativas, esperas a partir de 2 s.
type Ritmo={tentativas:number;base:number};
const INDIVIDUAL:Ritmo={tentativas:3,base:1000},EM_LOTE:Ritmo={tentativas:5,base:4000};

type Opts={fetch?:typeof fetch;sleep?:(ms:number)=>Promise<void>;apiKey?:string};
type Resultado={data:any|null;issue?:string};

const cache=new Map<string,{data:any|null;expires:number}>();
export function cachedS2(doi:string):{data:any|null}|undefined{
 const c=cache.get(String(doi).toLowerCase());
 if(!c||c.expires<Date.now())return undefined;
 return {data:c.data};
}
export function limparCacheS2(){cache.clear();}
function guardar(doi:string,data:any|null){
 if(cache.size>=5000)cache.delete(cache.keys().next().value!);
 cache.set(doi.toLowerCase(),{data,expires:Date.now()+VALIDADE});
}

const dormir=(ms:number)=>new Promise<void>(r=>setTimeout(r,ms));
function espera(tentativa:number,retryAfter:string|null,base:number):number{
 const s=Number(retryAfter);
 if(retryAfter&&Number.isFinite(s)&&s>=0)return Math.min(TETO_ESPERA,s*1000);
 const janela=Math.min(TETO_ESPERA,base*Math.pow(2,tentativa));
 return Math.round(janela/2+Math.random()*(janela/2));
}

// Devolve a resposta boa, ou o motivo da última falha. 404 é resposta boa.
async function comTentativas(url:string,init:RequestInit,o:Opts,timeout:number,ritmo:Ritmo):Promise<{r?:Response;issue?:string}>{
 const f=o.fetch||fetch,sleep=o.sleep||dormir;
 const headers:Record<string,string>={Accept:'application/json',...(init.body?{'content-type':'application/json'}:{}),...(o.apiKey?{'x-api-key':o.apiKey}:{})};
 let issue='';
 for(let i=0;i<ritmo.tentativas;i++){
  let retryAfter:string|null=null;
  try{
   const r=await f(url,{...init,headers,signal:AbortSignal.timeout(timeout)});
   if(r.ok||r.status===404)return {r};
   retryAfter=r.headers.get('retry-after');await r.body?.cancel();
   issue=r.status===429?'limite temporário de consultas':r.status===401||r.status===403?'acesso à base bloqueado':'indisponível (HTTP '+r.status+')';
   // Chave recusada ou pedido inválido não melhoram repetindo.
   if(r.status!==429&&r.status<500)break;
  }catch(e){issue=e instanceof Error&&(e.name==='TimeoutError'||e.name==='AbortError')?'tempo limite excedido':'falha de conexão ou resposta inválida';}
  if(i<ritmo.tentativas-1)await sleep(espera(i,retryAfter,ritmo.base));
 }
 return {issue:'Semantic Scholar: '+issue};
}

export async function fetchS2Paper(doi:string,o:Opts={}):Promise<Resultado>{
 const c=cachedS2(doi);
 if(c)return c;
 const {r,issue}=await comTentativas(API+'DOI:'+encodeURIComponent(doi)+'?fields='+FIELDS,{method:'GET'},o,18000,INDIVIDUAL);
 if(!r)return {data:null,issue};
 if(r.status===404){await r.body?.cancel();guardar(doi,null);return {data:null};}
 try{const data=await r.json();guardar(doi,data);return {data};}
 catch{return {data:null,issue:'Semantic Scholar: falha de conexão ou resposta inválida'};}
}

// Pré-carrega o cache. Falha aqui não é erro: a consulta individual cobre.
export async function prefetchS2(dois:string[],o:Opts={}):Promise<number>{
 const unicos=[...new Set(dois.map(d=>String(d).trim()).filter(Boolean))];
 let n=0;
 for(let i=0;i<unicos.length;i+=LOTE){
  const parte=unicos.slice(i,i+LOTE);
  const {r}=await comTentativas(API+'batch?fields='+FIELDS,{method:'POST',body:JSON.stringify({ids:parte.map(d=>'DOI:'+d)})},o,30000,EM_LOTE);
  if(!r||!r.ok){await r?.body?.cancel();continue;}
  let lista:any;try{lista=await r.json();}catch{continue;}
  if(!Array.isArray(lista)||lista.length!==parte.length)continue;
  parte.forEach((d,j)=>{guardar(d,lista[j]??null);n++;});
 }
 return n;
}
