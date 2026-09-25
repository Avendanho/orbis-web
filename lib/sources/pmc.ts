// PubMed Central pelo acervo aberto que o NIH publica na AWS.
//
// Por que não o oa.fcgi: aquele serviço foi desligado na migração do PMC de
// agosto/2026 e responde 404 — o resolvedor do ORBIS o chamava e recebia lista
// vazia em silêncio. As páginas de artigo do PMC também passaram a responder
// um desafio reCAPTCHA a cliente HTTP. O bucket é a via que continua servindo
// o mesmo acervo, como objetos estáticos, sem chave e sem limite de consultas.
//
// Duas armadilhas na leitura da listagem, ambas cobertas por teste:
//   1. um artigo pode ter várias versões (PMC123.1/, PMC123.2/) e só a mais
//      nova vale;
//   2. material suplementar também termina em .pdf (..._MOESM1_ESM.pdf,
//      ...-s001.pdf), então só o arquivo chamado PMC<id>.<versão>.<ext> é o
//      artigo.
export const BUCKET='https://pmc-oa-opendata.s3.amazonaws.com/';
const IDCONV='https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/';

export function normalizePmcid(value:string):string{
 const v=String(value||'').trim().toUpperCase().replace(/^PMC/,'').trim();
 return v?'PMC'+v:'';
}

// O ponto no fim do prefixo ancora a consulta: sem ele, `PMC1000` também
// listaria `PMC10000338`.
export function bucketListingUrl(pmcid:string):string{
 return BUCKET+'?'+new URLSearchParams({'list-type':'2',prefix:normalizePmcid(pmcid)+'.'}).toString();
}

export function parseBucketListing(xml:string,pmcid:string):{version:number|null;pdf:string|null;xml:string|null}{
 const id=normalizePmcid(pmcid);const vazio={version:null,pdf:null,xml:null};
 if(!id)return vazio;
 const esc=id.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
 const artigo=new RegExp('^'+esc+'\\.(\\d+)/'+esc+'\\.\\1\\.(pdf|xml|txt)$');
 const porVersao=new Map<number,Record<string,string>>();
 for(const m of String(xml||'').matchAll(/<Key>([^<]+)<\/Key>/g)){
  const achado=artigo.exec(m[1]);if(!achado)continue;
  const versao=Number(achado[1]);
  const arquivos=porVersao.get(versao)||{};arquivos[achado[2]]=BUCKET+encodeURI(m[1]);porVersao.set(versao,arquivos);
 }
 if(!porVersao.size)return vazio;
 const versao=Math.max(...porVersao.keys());const arquivos=porVersao.get(versao)!;
 return {version:versao,pdf:arquivos.pdf||null,xml:arquivos.xml||null};
}

export function idConvUrl(identifier:string,email?:string):string{
 const q=new URLSearchParams({ids:identifier,format:'json',tool:'orbis-web'});
 if(email)q.set('email',email);
 return IDCONV+'?'+q.toString();
}

export function parseIdConv(data:any):{pmid?:string;pmcid?:string}{
 for(const r of data?.records||[]){
  if(r?.status==='error')continue;
  const out:{pmid?:string;pmcid?:string}={};
  if(r?.pmid)out.pmid=String(r.pmid);
  if(r?.pmcid)out.pmcid=normalizePmcid(r.pmcid);
  return out;
 }
 return {};
}

// Localiza o artigo no bucket. Ausência é resultado normal e barato: os
// manuscritos depositados por mandato do NIH (NIHMS) estão no PMC mas fora do
// subconjunto aberto, então simplesmente não estão aqui.
export async function pmcOpenAccess(pmcid:string,signal?:AbortSignal){
 const id=normalizePmcid(pmcid);
 if(!id)return {version:null,pdf:null,xml:null};
 try{
  const r=await fetch(bucketListingUrl(id),{signal:signal??AbortSignal.timeout(12000)});
  if(!r.ok){await r.body?.cancel();return {version:null,pdf:null,xml:null};}
  return parseBucketListing(await r.text(),id);
 }catch{return {version:null,pdf:null,xml:null};}
}

export async function identifiersFor(doiOrPmid:string,email?:string,signal?:AbortSignal){
 try{
  const r=await fetch(idConvUrl(doiOrPmid,email),{headers:{Accept:'application/json'},signal:signal??AbortSignal.timeout(12000)});
  if(!r.ok){await r.body?.cancel();return {};}
  return parseIdConv(await r.json());
 }catch{return {};}
}
