// Busca no Embase pela API da Elsevier.
//
// O Embase não tem acesso anônimo: exige chave (dev.elsevier.com) e, na prática,
// o token institucional (insttoken) que carrega a assinatura da universidade.
// Sem as duas, a API responde 401/403 — e isso vira uma mensagem clara, não um
// "nenhum resultado" enganoso.
//
// A resposta da API muda de formato entre versões (search-results/entry na
// família Scopus, results/header na Embase API). O parser não depende de um
// caminho fixo: localiza a lista de registros e procura o DOI em cada um.
const API='https://api.elsevier.com/content/embase/article';

export const MAX_RESULTS=500;
const PAGINA=100;

export type EmbaseRecord={doi:string;title:string;year:string;journal:string};
type Options={retmax?:number;apiKey?:string;instToken?:string};

export function searchUrl(term:string,start=0,count=PAGINA):string{
 const expressao=String(term||'').trim();
 if(!expressao)throw new Error('Informe uma expressão de busca.');
 return API+'?'+new URLSearchParams({query:expressao,start:String(start),count:String(count)}).toString();
}

export function headers(o:Options):Record<string,string>{
 if(!o.apiKey)throw new Error('Embase exige a chave da Elsevier (EMBASE_API_KEY ou ELSEVIER_API_KEY).');
 const h:Record<string,string>={Accept:'application/json','X-ELS-APIKey':o.apiKey};
 if(o.instToken)h['X-ELS-Insttoken']=o.instToken;
 return h;
}

const DOI=/^(?:https?:\/\/(?:dx\.)?doi\.org\/)?(10\.\d{4,9}\/\S+)$/i;

// Primeiro campo cujo nome contém "doi" e cujo valor tem cara de DOI.
function acharDoi(v:any,prof=0):string{
 if(!v||typeof v!=='object'||prof>6)return '';
 for(const [k,x] of Object.entries(v)){
  if(/doi/i.test(k)){
   const s=typeof x==='string'?x:typeof (x as any)?.['$']==='string'?(x as any)['$']:typeof (x as any)?.value==='string'?(x as any).value:'';
   const m=s.trim().match(DOI);if(m)return m[1].toLowerCase();
  }
 }
 for(const x of Object.values(v)){const d=acharDoi(x,prof+1);if(d)return d;}
 return '';
}

function primeiro(v:any,chaves:string[]):string{
 for(const k of chaves){const x=v?.[k];if(typeof x==='string'&&x.trim())return x.trim();if(x&&typeof x==='object'&&typeof x['$']==='string')return x['$'].trim();}
 return '';
}

export function parseSearch(data:any):{records:EmbaseRecord[];total:number}{
 const sr=data?.['search-results'];
 const lista=Array.isArray(sr?.entry)?sr.entry:Array.isArray(data?.results)?data.results:Array.isArray(data?.entries)?data.entries:[];
 // A família Scopus devolve [{error:'Result set was empty'}] quando não acha nada.
 const entradas=lista.filter((e:any)=>e&&typeof e==='object'&&!e.error);
 const total=Number(sr?.['opensearch:totalResults']??data?.header?.hits??data?.totalResults??data?.total)||entradas.length;
 const records=entradas.map((e:any)=>{
  const head=e.head||e.itemInfo||e;
  return {
   doi:acharDoi(e),
   title:primeiro(e,['dc:title','title','citationTitle'])||primeiro(head,['citationTitle','title']),
   year:(primeiro(e,['prism:coverDate','publicationYear','year'])||primeiro(head,['publicationYear'])).slice(0,4),
   journal:primeiro(e,['prism:publicationName','sourceTitle','journal'])||primeiro(head?.source,['sourceTitle']),
  };
 });
 return {records,total:entradas.length?total:0};
}

async function pagina(url:string,h:Record<string,string>,signal?:AbortSignal){
 const r=await fetch(url,{headers:h,signal:signal??AbortSignal.timeout(30000)});
 if(!r.ok){
  await r.body?.cancel();
  if(r.status===401||r.status===403)throw new Error('O Embase recusou as credenciais (HTTP '+r.status+'): confira a chave da Elsevier e o token institucional.');
  throw new Error(r.status===429?'O Embase limitou as consultas; tente novamente em instantes.':'Embase indisponível (HTTP '+r.status+').');
 }
 return r.json();
}

export async function searchEmbase(term:string,o:Options={},signal?:AbortSignal){
 const h=headers(o);
 const pedido=o.retmax==null?MAX_RESULTS:Number(o.retmax);
 const limite=Math.min(MAX_RESULTS,Math.max(1,Number.isFinite(pedido)?pedido:MAX_RESULTS));
 const records:EmbaseRecord[]=[];let total=0;
 for(let start=0;start<limite;start+=PAGINA){
  const r=parseSearch(await pagina(searchUrl(term,start,Math.min(PAGINA,limite-start)),h,signal));
  total=r.total;records.push(...r.records);
  if(r.records.length<Math.min(PAGINA,limite-start)||records.length>=total)break;
 }
 return {records,total,withoutDoi:records.filter(r=>!r.doi).length};
}
