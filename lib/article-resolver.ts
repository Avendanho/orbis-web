import { normalizeDoi, type Article } from './doi-batch';
import { safeUrl, inspectPage, probeFreePdf, accessReason } from './article-discovery';
import { pmcOpenAccess, identifiersFor } from './sources/pmc';
import { unpaywall } from './sources/unpaywall';
import { fetchS2Paper } from './sources/semantic-scholar';
import { isCentralRecord, CENTRAL_REASON, recordKind, splitIssues, isPartial, escolherMotivo } from './record-kind';
type Row=Record<string,any>;
type SourceResult={name:string;data:Row|null;issue?:string};
const cache=new Map<string,{expires:number;article:Article}>();
// O antigo oa.fcgi foi desligado na migração do PMC de agosto/2026 e responde
// 404 — esta função devolvia lista vazia em silêncio desde então. O bucket que
// o NIH publica na AWS serve o mesmo acervo sem chave e sem desafio.
async function pmcOaPdfLinks(pmcid:string):Promise<string[]>{
 const achado=await pmcOpenAccess(pmcid);
 return achado.pdf?[achado.pdf]:[];
}
async function source(name:string,url:string):Promise<SourceResult>{
 try{
  const r=await fetch(url,{headers:{Accept:'application/json'},signal:AbortSignal.timeout(18000)});
  if(r.status===404)return {name,data:null};
  if(!r.ok){await r.body?.cancel();return {name,data:null,issue:name+': '+(r.status===429?'limite temporário de consultas':r.status===403||r.status===401?'acesso à base bloqueado':'indisponível (HTTP '+r.status+')')};}
  return {name,data:await r.json() as Row};
 }catch(e){return {name,data:null,issue:name+': '+(e instanceof Error&&(e.name==='TimeoutError'||e.name==='AbortError')?'tempo limite excedido':'falha de conexão ou resposta inválida')};}
}
// Chave do Semantic Scholar: cota própria em vez da cota anônima compartilhada.
export function s2ApiKey(){return String((globalThis as any).SEMANTIC_SCHOLAR_API_KEY||(typeof process!=='undefined'?process.env?.SEMANTIC_SCHOLAR_API_KEY:'')||'').trim()||undefined;}
export async function resolveArticle(input:string,refresh=false):Promise<Article>{
 const doi=normalizeDoi(input);const key=doi.toLowerCase();const cached=cache.get(key);if(!refresh&&cached&&cached.expires>Date.now())return cached.article;
 // Número de registro do CENTRAL não existe no doi.org: consultar só gera 404.
 if(isCentralRecord(doi))return {doi,title:doi,authors:'',year:'',journal:'',pdf:null,pdfUrls:[],source:'',found:false,partial:false,sourceIssues:[],recordKind:'central',...CENTRAL_REASON};
 const query=new URLSearchParams({query:'DOI:"'+doi.replace(/["\\]/g,'')+'"',format:'json',resultType:'core',pageSize:'5'});
 const results=await Promise.all([
  source('Crossref','https://api.crossref.org/works/'+encodeURIComponent(doi)),
  source('Europe PMC','https://www.ebi.ac.uk/europepmc/webservices/rest/search?'+query),
  fetchS2Paper(doi,{apiKey:s2ApiKey()}).then(r=>({name:'Semantic Scholar',...r}) as SourceResult),
  source('OpenAlex','https://api.openalex.org/works/https://doi.org/'+encodeURIComponent(doi))
 ]);
 // Unpaywall é a fonte de maior rendimento medido e não estava sendo consultada.
 // Exige e-mail de contato; sem ele, `unpaywall()` se omite sozinha.
 const up=await unpaywall(doi,(globalThis as any).UNPAYWALL_EMAIL||process.env?.UNPAYWALL_EMAIL||'');
 let cr=results[0].data?.message as Row|undefined;
 // DataCite covers DOIs such as preprints that are absent from Crossref.
 let dcType='';
 if(!cr){
  const dc=await source('DataCite','https://api.datacite.org/dois/'+encodeURIComponent(doi));results.push(dc);
  const d=dc.data?.data?.attributes;dcType=String(d?.types?.resourceTypeGeneral||'');
  if(d)cr={title:d.titles?.map((t:Row)=>t.title),author:d.creators?.map((a:Row)=>({given:a.givenName,family:a.familyName||a.name})),published:{'date-parts':[[d.publicationYear]]},URL:d.url,license:d.rightsList?.map((r:Row)=>({URL:r.rightsUri}))};
 }
 const ep=results[1].data?.resultList?.result?.find((r:Row)=>r.doi?.toLowerCase()===key) as Row|undefined;
 const ss=results[2].data;const oa=results[3].data;
 const issues=results.flatMap(r=>r.issue?[r.issue]:[]);
 let pageDoiVisto='';
 const candidates:{url:string;source:string}[]=[];const pages:{url:string;source:string;oa:boolean}[]=[];
 const addPdf=(url:unknown,name:string)=>{const u=safeUrl(url);if(u&&!candidates.some(c=>c.url===u))candidates.push({url:u,source:name});};
 const addPage=(url:unknown,name:string,open:boolean)=>{const u=safeUrl(url);if(u&&name!=='Editora'&&/^(dx\.)?doi\.org$/.test(new URL(u).hostname)&&pages.some(p=>p.source==='Editora'))return;if(u&&!pages.some(p=>p.url===u))pages.push({url:u,source:name,oa:open});};
 const cc=cr?.license?.some((l:Row)=>{const u=safeUrl(l.URL);return u&&/^https?:\/\/(?:www\.)?creativecommons.org\/(?:licenses|publicdomain)\//i.test(u)&&(!l.start?.['date-time']||Date.parse(l.start['date-time'])<=Date.now());});
 let knownOa=!!(cc||ep?.isOpenAccess==='Y'||ss?.isOpenAccess||oa?.open_access?.is_oa);let paid=false;
 // PLOS ONE's DOI resolver can add several slow redirects. Use its canonical article page.
 const publisherPage=/^10\.1371\/journal\.pone\./i.test(doi)?'https://journals.plos.org/plosone/article?id='+encodeURIComponent(doi):cr?.resource?.primary?.URL||cr?.URL||'https://doi.org/'+doi;
 addPage(publisherPage,'Editora',!!(cc||oa?.primary_location?.is_oa));
 const locations=[oa?.best_oa_location,...(oa?.locations||[])].filter(Boolean);
 for(const l of locations)if(l.is_oa){addPdf(l.pdf_url,'OpenAlex — '+(l.source?.display_name||'repositório'));addPage(l.landing_page_url,'OpenAlex — '+(l.source?.display_name||'repositório'),true);}
 for(const u of ep?.fullTextUrlList?.fullTextUrl||[])if(['OA','F'].includes(u.availabilityCode)||u.availability==='Free'){knownOa=true;if(u.documentStyle==='pdf')addPdf(u.url,'Europe PMC');else if(u.documentStyle==='html')addPage(u.url,'Europe PMC',true);}
 for(const u of up.pdfUrls)addPdf(u,'Unpaywall');
 for(const u of up.pageUrls)addPage(u,'Unpaywall',true);
 if(up.isOa)knownOa=true;
 // O Europe PMC nem sempre traz o PMCID; o conversor do NCBI resolve pelo DOI.
 const pmcid=ep?.pmcid||(await identifiersFor(doi)).pmcid;
 if(pmcid){const pmcLinks=await pmcOaPdfLinks(pmcid);for(const url of pmcLinks)addPdf(url,'PMC Open Access');if(pmcLinks.length)knownOa=true;}
 if(ss?.isOpenAccess)addPdf(ss.openAccessPdf?.url,'Semantic Scholar');
 if(cc)for(const l of cr?.link||[])if(l['content-type']==='application/pdf')addPdf(l.URL,'Editora — Crossref');
 // Inspect publisher and up to two repository pages, including when another PDF was found.
 const selected=pages.slice(0,3);
 const discoveries=await Promise.all(selected.map(async page=>{
  try{const result=await inspectPage(page.url,doi);if(result.pageDoi&&!pageDoiVisto)pageDoiVisto=result.pageDoi;return {page,result};}catch(e){return {page,error:page.source+': '+(e instanceof Error&&e.name==='TimeoutError'?'página demorou a responder':e instanceof Error?e.message:'falha ao abrir página')};}
 }));
 const unverified:{url:string;source:string}[]=[];
 for(const discovery of discoveries){
  if(discovery.error){issues.push(discovery.error);continue;}
  const r=discovery.result!;if(r.mismatch){issues.push(discovery.page.source+': página retornou outro DOI');continue;}
  if(r.free)knownOa=true;
  if(r.paid&&discovery.page.source==='Editora')paid=true;
  for(const url of r.pdfUrls){if((discovery.page.oa||r.free)&&!r.paid)addPdf(url,discovery.page.source);else unverified.push({url,source:discovery.page.source});}
 }
 // Publicly serveable PDFs count as free even when indexing is missing or outdated.
 await Promise.all(unverified.slice(0,2).map(async c=>{try{if(await probeFreePdf(c.url)){addPdf(c.url,c.source);knownOa=true;}}catch{issues.push(c.source+': não foi possível verificar o PDF indicado');}}));
 const priority=(s:string)=>s.startsWith('PMC Open Access')?5:s.startsWith('OpenAlex')?4:s.startsWith('Europe PMC')?3:s.startsWith('Semantic Scholar')?2:s.startsWith('Editora')?1:0;
 candidates.sort((a,b)=>priority(b.source)-priority(a.source));
 const pdf=candidates[0]?.url||null;
 const freePages=pages.filter(p=>p.oa).map(p=>p.url);
 // Só falha de fonte que importa deixa a busca incompleta (ver record-kind.ts).
 const gravidade=splitIssues(issues);
 const kind=recordKind({crType:cr?.type,oaType:oa?.type,dcType,page:cr?.page,title:cr?.title?.[0]||ep?.title||oa?.display_name||''});
 const base=accessReason({pdf:!!pdf,oa:knownOa,paid,closed:oa?.open_access?.oa_status==='closed',found:!!(cr||ep||ss||oa),issues:gravidade.criticas});
 const reason=escolherMotivo({pdf:!!pdf,kind,...gravidade,oa:knownOa,base});
 // Onde a pessoa baixa à mão quando o robô não consegue.
 const manualUrl=!pdf&&['publisher_blocked','oa_without_pdf'].includes(reason.reasonCode)?freePages[0]||publisherPage:undefined;
 const article:Article={doi,pageDoi:pageDoiVisto,abstract:(cr?.abstract||ep?.abstractText||'').replace(/<[^>]*>/g,''),title:cr?.title?.[0]?.replace(/<[^>]*>/g,'')||ep?.title||ss?.title||oa?.display_name||doi,authors:cr?.author?.slice(0,6).map((a:Row)=>[a.given,a.family].filter(Boolean).join(' ')).join(', ')||ep?.authorString||ss?.authors?.slice(0,6).map((a:Row)=>a.name).join(', ')||oa?.authorships?.slice(0,6).map((a:Row)=>a.author?.display_name).filter(Boolean).join(', ')||'',year:String(cr?.published?.['date-parts']?.[0]?.[0]||ep?.pubYear||ss?.year||oa?.publication_year||''),journal:cr?.['container-title']?.[0]||ep?.journalInfo?.journal?.title||ss?.venue||oa?.primary_location?.source?.display_name||'',pdf,pdfUrls:candidates.slice(0,12).map(c=>c.url),source:candidates[0]?.source||'',found:!!(cr||ep||ss||oa),partial:isPartial(gravidade),recordKind:kind?.kind,manualUrl,freeUrl:freePages[0]||(knownOa?pages[0]?.url:undefined),sourceIssues:issues,...reason};
 if(cache.size>=500)cache.delete(cache.keys().next().value!);
 cache.set(key,{article,expires:Date.now()+(!issues.length?10*60*1000:30*1000)});
 return article;
}
