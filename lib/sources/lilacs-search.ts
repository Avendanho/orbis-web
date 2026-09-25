// Busca na LILACS pela API pública do FI-Admin (BIREME/OPAS).
//
// Por que não o portal da BVS: pesquisa.bvsalud.org responde a qualquer cliente
// HTTP com um desafio anti-robô (403), inclusive na exportação RIS que o motor
// Python usava. O FI-Admin é o sistema onde a própria LILACS é catalogada; sua
// API atende sem chave e é a mesma base, só que sem a vitrine.
//
// Duas etapas, como no PubMed: a busca (Solr) devolve só os ids, e o DOI vem do
// registro completo, pedido em lote por `id__in`.
//
// A expressão vai inteira: aceita a sintaxe do iAHx (tw:, mh:, ti:, booleanos).
// O filtro da base vai em `fq` e entre aspas, porque sem aspas a API converte o
// valor para minúsculas e o campo `indexed_database` diferencia caixa.
const API='https://fi-admin-api.bvsalud.org/api/bibliographic/';

export const MAX_RESULTS=500;
// A API recusa com 403 pedidos `id__in` a partir de ~35 ids (a URL fica longa
// demais para o firewall dela), e cada lote leva ~10 s porque o registro vem
// completo. Lotes de 25, quatro de cada vez: 500 registros em ~1 minuto.
const LOTE=25,SIMULTANEOS=4;

export type LilacsRecord={id:string;doi:string;title:string;authors:string;year:string;journal:string};

// O firewall da API responde 403 a qualquer parêntese encostado em AND/OR/NOT
// (confunde a sintaxe booleana com injeção de SQL) — em GET e em POST. Grupo
// precedido de campo passa. `tw` é o campo da busca livre, então `(x)` e
// `tw:(x)` devolvem o mesmo conjunto; dentro do grupo, um termo com campo
// próprio (mh:, ti:) continua valendo. Verificado contra a API com OR, NOT,
// aninhamento e inclusão-exclusão. Aspas e parêntese escapado são texto.
export function blindarGrupos(expressao:string):string{
 let out='',aspas=false;
 for(let i=0;i<expressao.length;i++){
  const c=expressao[i];
  if(c==='\\'){out+=c+(expressao[i+1]??'');i++;continue;}
  if(c==='"')aspas=!aspas;
  else if(c==='('&&!aspas&&!/[\w:]$/.test(out))out+='tw:';
  out+=c;
 }
 return out;
}

export function searchUrl(term:string,o:{retmax?:number}={}):string{
 const expressao=blindarGrupos(String(term||'').trim());
 if(!expressao)throw new Error('Informe uma expressão de busca.');
 const pedido=o.retmax==null?MAX_RESULTS:Number(o.retmax);
 const count=Math.min(MAX_RESULTS,Math.max(1,Number.isFinite(pedido)?pedido:MAX_RESULTS));
 const q=new URLSearchParams({q:expressao,fq:'indexed_database:"LILACS"',count:String(count),format:'json'});
 return API+'search/?'+q.toString();
}

export function recordsUrl(ids:string[]):string{
 return API+'?'+new URLSearchParams({format:'json',id__in:ids.join(','),limit:String(ids.length)}).toString();
}

export function parseSearch(data:any):{ids:string[];total:number}{
 const r=data?.diaServerResponse?.[0]?.response;
 if(!r)return {ids:[],total:0};
 const docs=Array.isArray(r.docs)?r.docs:[];
 const ids=docs.map((d:any)=>String(d?.django_id||'').trim()).filter(Boolean);
 return {ids,total:Number(r.numFound)||ids.length};
}

// Título e autores vêm como listas com idioma; o primeiro título é o original.
function texto(v:any):string{
 if(Array.isArray(v))return texto(v[0]);
 if(v&&typeof v==='object')return String(v.text||'');
 return String(v||'');
}

export function parseRecords(data:any):LilacsRecord[]{
 const objs=Array.isArray(data?.objects)?data.objects:[];
 return objs.map((r:any)=>{
  const doi=String(r?.doi_number||'').trim().replace(/^https?:\/\/(dx\.)?doi\.org\//i,'');
  return {
   id:String(r?.id??''),
   // Minúsculas porque é assim que o resto do ORBIS compara DOI.
   doi:/^10\.\S+\/\S+/.test(doi)?doi.toLowerCase():'',
   title:texto(r?.title).replace(/\s+/g,' ').trim(),
   authors:(Array.isArray(r?.individual_author)?r.individual_author:[]).map(texto).map((s:string)=>s.trim()).filter(Boolean).join('; '),
   year:(String(r?.publication_date_normalized||r?.publication_date||'').match(/\d{4}/)||[''])[0],
   journal:String(r?.title_serial||r?.source||'').trim(),
  };
 });
}

async function json(url:string,signal?:AbortSignal){
 const r=await fetch(url,{headers:{Accept:'application/json'},redirect:'follow',signal:signal??AbortSignal.timeout(60000)});
 if(!r.ok){await r.body?.cancel();throw new Error('LILACS indisponível (HTTP '+r.status+').');}
 return r.json();
}

export async function searchLilacs(term:string,o:{retmax?:number}={},signal?:AbortSignal){
 const {ids,total}=parseSearch(await json(searchUrl(term,o),signal));
 if(!ids.length)return {records:[] as LilacsRecord[],total:0,withoutDoi:0};
 const lotes:string[][]=[];
 for(let n=0;n<ids.length;n+=LOTE)lotes.push(ids.slice(n,n+LOTE));
 const partes:LilacsRecord[][]=new Array(lotes.length);let proximo=0;
 async function trabalhar(){while(proximo<lotes.length){const i=proximo++;partes[i]=parseRecords(await json(recordsUrl(lotes[i]),signal));}}
 await Promise.all(Array.from({length:Math.min(SIMULTANEOS,lotes.length)},trabalhar));
 // Na ordem da busca, não na de chegada dos lotes.
 const records=partes.flat();
 return {records,total,withoutDoi:records.filter(r=>!r.doi).length};
}
