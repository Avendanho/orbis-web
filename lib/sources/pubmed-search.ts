// Busca no PubMed pelas E-utilities do NCBI.
//
// Por que existe: até aqui o ORBIS só aceitava uma lista de DOIs já pronta,
// colada pelo pesquisador. Não havia como partir de uma pergunta de pesquisa.
// Esta fonte fecha essa lacuna sem mudar o resto do fluxo — o que ela produz é
// uma lista de DOIs, exatamente o que a etapa de busca já consome.
//
// A expressão de busca vai inteira, sem reescrita: quem monta uma estratégia de
// revisão sistemática usa a sintaxe do PubMed ([MeSH], [tiab], booleanos) de
// propósito, e "melhorar" isso por conta própria mudaria silenciosamente o
// escopo da revisão.
//
// Funciona sem chave; com `NCBI_API_KEY` o teto de requisições por segundo sobe.
const EUTILS='https://eutils.ncbi.nlm.nih.gov/entrez/eutils/';

// Mesmo teto que o ORBIS já aplica ao lote de DOIs (MAX_DOIS em doi-batch.ts).
export const MAX_RESULTS=500;

export type PubmedRecord={pmid:string;doi:string;title:string;authors:string;year:string;journal:string};
type Options={retmax?:number;apiKey?:string;email?:string};

function comum(q:URLSearchParams,o:Options){
 if(o.apiKey)q.set('api_key',o.apiKey);
 if(o.email)q.set('email',o.email);
 q.set('tool','orbis-web');
 return q;
}

export function searchUrl(term:string,o:Options={}):string{
 const expressao=String(term||'').trim();
 if(!expressao)throw new Error('Informe uma expressão de busca.');
 // `??` e não `||`: retmax=0 é um pedido explícito (e absurdo) que deve ser
 // grampeado em 1, enquanto retmax ausente cai no teto padrão.
 const pedido=o.retmax==null?MAX_RESULTS:Number(o.retmax);
 const retmax=Math.min(MAX_RESULTS,Math.max(1,Number.isFinite(pedido)?pedido:MAX_RESULTS));
 const q=comum(new URLSearchParams({db:'pubmed',term:expressao,retmax:String(retmax),retmode:'json',sort:'relevance'}),o);
 return EUTILS+'esearch.fcgi?'+q.toString();
}

export function summaryUrl(pmids:string[],o:Options={}):string{
 const q=comum(new URLSearchParams({db:'pubmed',id:pmids.join(','),retmode:'json'}),o);
 return EUTILS+'esummary.fcgi?'+q.toString();
}

export function parseSearch(data:any):{pmids:string[];total:number}{
 const r=data?.esearchresult;
 if(!r)return {pmids:[],total:0};
 const pmids=(Array.isArray(r.idlist)?r.idlist:[]).map((x:any)=>String(x)).filter(Boolean);
 return {pmids,total:Number(r.count)||pmids.length};
}

export function parseSummary(data:any):PubmedRecord[]{
 const result=data?.result;
 if(!result)return [];
 const uids=Array.isArray(result.uids)?result.uids:[];
 const out:PubmedRecord[]=[];
 for(const uid of uids){
  const r=result[String(uid)];
  if(!r)continue;
  const ids=Array.isArray(r.articleids)?r.articleids:[];
  const doi=ids.find((i:any)=>String(i?.idtype).toLowerCase()==='doi')?.value;
  out.push({
   pmid:String(r.uid||uid),
   // Minúsculas porque é assim que o resto do ORBIS compara DOI.
   doi:doi?String(doi).trim().toLowerCase():'',
   title:String(r.title||'').replace(/\s+/g,' ').trim(),
   authors:(Array.isArray(r.authors)?r.authors:[]).map((a:any)=>String(a?.name||'').trim()).filter(Boolean).join('; '),
   year:(String(r.pubdate||'').match(/\d{4}/)||[''])[0],
   journal:String(r.source||'').trim(),
  });
 }
 return out;
}

// Só o que tem DOI entra no lote: é a chave pela qual o ORBIS identifica um
// artigo. Os registros sem DOI continuam existindo no resultado da busca, para
// que o pesquisador veja o que ficou de fora e por quê.
export function doisFrom(records:{doi?:string}[]):string[]{
 const vistos=new Set<string>();const out:string[]=[];
 for(const r of records||[]){
  const doi=String(r?.doi||'').trim().toLowerCase();
  if(!doi||vistos.has(doi))continue;
  vistos.add(doi);out.push(doi);
 }
 return out;
}

async function json(url:string,signal?:AbortSignal){
 const r=await fetch(url,{headers:{Accept:'application/json'},signal:signal??AbortSignal.timeout(25000)});
 if(!r.ok){await r.body?.cancel();throw new Error(r.status===429?'O PubMed limitou as consultas; tente novamente em instantes.':'PubMed indisponível (HTTP '+r.status+').');}
 return r.json();
}

export async function searchPubmed(term:string,o:Options={},signal?:AbortSignal){
 const {pmids,total}=parseSearch(await json(searchUrl(term,o),signal));
 if(!pmids.length)return {records:[] as PubmedRecord[],total:0,withoutDoi:0};
 const records=parseSummary(await json(summaryUrl(pmids,o),signal));
 return {records,total,withoutDoi:records.filter(r=>!r.doi).length};
}
