// Unpaywall — o índice que sabe onde existe cópia legal de um artigo.
//
// Estava ausente do ORBIS e é, por medição num corpus de 643 artigos, a fonte
// de maior rendimento isolado: respondeu por 34% das recuperações.
//
// A API pede um e-mail de contato e recusa a consulta sem ele. Sem e-mail
// configurado a fonte se omite — nunca tenta assim mesmo, porque a resposta
// seria um erro previsível a cada artigo.
//
// As páginas sem PDF declarado também entram como candidatas: quando o índice
// conhece o repositório mas não o arquivo, a página costuma trazer o endereço
// em `citation_pdf_url`, que `article-discovery.ts` já sabe ler.
const API='https://api.unpaywall.org/v2/';

export function requestUrl(doi:string,email:string):string|null{
 const contato=String(email||'').trim();
 if(!contato)return null;
 return API+encodeURIComponent(String(doi||'').trim())+'?'+new URLSearchParams({email:contato}).toString();
}

export function parse(data:any):{isOa:boolean;pdfUrls:string[];pageUrls:string[]}{
 if(!data||typeof data!=='object')return {isOa:false,pdfUrls:[],pageUrls:[]};
 const pdfUrls:string[]=[];const pageUrls:string[]=[];
 const adicionar=(lista:string[],valor:unknown)=>{
  if(typeof valor==='string'&&valor&&!lista.includes(valor))lista.push(valor);
 };
 // A melhor localização primeiro: é a escolha que o próprio Unpaywall faz.
 const locais=[data.best_oa_location,...(Array.isArray(data.oa_locations)?data.oa_locations:[])].filter(Boolean);
 for(const l of locais){adicionar(pdfUrls,l?.url_for_pdf);}
 for(const l of locais){
  // `url` é o endereço que o próprio Unpaywall considera melhor e às vezes é
  // o único preenchido, quando `url_for_landing_page` vem nulo.
  for(const pagina of [l?.url_for_landing_page,l?.url]){
   if(typeof pagina==='string'&&!pdfUrls.includes(pagina))adicionar(pageUrls,pagina);
  }
 }
 return {isOa:!!data.is_oa,pdfUrls,pageUrls};
}

export async function unpaywall(doi:string,email:string,signal?:AbortSignal){
 const url=requestUrl(doi,email);
 if(!url)return {isOa:false,pdfUrls:[],pageUrls:[]};
 try{
  const r=await fetch(url,{headers:{Accept:'application/json'},signal:signal??AbortSignal.timeout(15000)});
  if(!r.ok){await r.body?.cancel();return {isOa:false,pdfUrls:[],pageUrls:[]};}
  return parse(await r.json());
 }catch{return {isOa:false,pdfUrls:[],pageUrls:[]};}
}
