// Ponte opcional para o motor Python (`servico-python/`).
//
// A regra que governa este módulo: **o ORBIS nunca depende do serviço.** Sem
// ele, tudo que está em TypeScript continua funcionando — Unpaywall, bucket do
// PMC, PubMed, identidade por metadados e triagem sobre título e resumo. Com
// ele, ficam disponíveis as capacidades que exigem biblioteca nativa: ler o
// texto de dentro do PDF, conferir identidade pelo conteúdo e triar o texto
// completo.
//
// Por isso toda função aqui devolve `null` em vez de lançar erro quando o
// serviço não responde: indisponibilidade é estado previsto, não falha.
const TIMEOUT=120000;

export type EngineStatus={online:boolean;resources:string[];missing:string[];url:string;pdfDir:string};
export type EngineIdentity={ok:boolean;metodo:string;score:number;detalhe:string};
export type EngineDownload={ok:boolean;erro?:string;fonte?:string;fontes_tentadas?:string[];arquivo?:string;identidade?:EngineIdentity;texto?:string;paginas?:number;chars?:number;texto_truncado?:boolean;aviso?:string;formato?:string;imagens?:number;pasta_imagens?:string;arquivo_md?:string;aviso_extracao?:string};
export type DownloadRequest={doi:string;projeto:string;modo:'baixar'|'analisar';titulo?:string;autor?:string;ano?:string;periodico?:string;prazo?:number};

// Recusa explícita do motor (entrada inválida, disco cheio). Diferente de
// "fora do ar": o motor respondeu, e o ORBIS precisa mostrar o motivo.
export class EngineRefused extends Error{status:number;constructor(status:number,message:string){super(message);this.status=status}}

export function engineUrl(env:any):string{
 return String(env?.ORBIS_ENGINE_URL||'').trim().replace(/\/+$/,'');
}

async function call(base:string,path:string,body?:unknown,signal?:AbortSignal){
 const r=await fetch(base+path,{
  method:body===undefined?'GET':'POST',
  headers:body===undefined?{Accept:'application/json'}:{'content-type':'application/json',Accept:'application/json'},
  body:body===undefined?undefined:JSON.stringify(body),
  signal:signal??AbortSignal.timeout(TIMEOUT),
 });
 if(!r.ok){
  const detalhe=await r.text().catch(()=>'');
  throw new Error('motor HTTP '+r.status+(detalhe?': '+detalhe.slice(0,300):''));
 }
 return r.json();
}

// Sondagem curta: é usada para decidir o que oferecer na interface, então não
// pode fazer a página esperar.
export async function engineStatus(env:any):Promise<EngineStatus>{
 const url=engineUrl(env);
 if(!url)return {online:false,resources:[],missing:[],url:'',pdfDir:''};
 try{
  const d:any=await call(url,'/saude',undefined,AbortSignal.timeout(4000));
  return {online:!!d?.ok,resources:d?.recursos||[],missing:d?.faltando||[],url,pdfDir:String(d?.pasta_pdfs||'')};
 }catch{return {online:false,resources:[],missing:[],url,pdfDir:''};}
}

export async function extractText(env:any,pdfUrl:string):Promise<{pages:number;text:string}|null>{
 const url=engineUrl(env);
 if(!url)return null;
 try{
  const d:any=await call(url,'/texto',{url:pdfUrl});
  return {pages:Number(d?.paginas)||0,text:String(d?.text||d?.texto||'')};
 }catch{return null;}
}

// Recebe a URL do PDF, e não o texto: o motor lê os bytes porque o DOI muitas
// vezes está nos metadados do documento, fora do texto extraído.
export async function verifyIdentity(env:any,pdfUrl:string,article:{doi:string;title?:string;authors?:string;journal?:string;year?:string}){
 const url=engineUrl(env);
 if(!url)return null;
 try{
  return await call(url,'/identidade',{
   url:pdfUrl,doi:article.doi,titulo:article.title||'',autor:article.authors||'',
   periodico:article.journal||'',ano:String(article.year||'')});
 }catch{return null;}
}

// Baixa um artigo pela cadeia completa do motor. O prazo é imposto pelo
// próprio motor; o limite daqui só cobre um motor travado.
export async function downloadViaEngine(env:any,req:DownloadRequest):Promise<EngineDownload|null>{
 const url=engineUrl(env);
 if(!url)return null;
 const prazo=req.prazo??90;
 let r:Response;
 try{
  r=await fetch(url+'/baixar',{method:'POST',headers:{'content-type':'application/json',Accept:'application/json'},body:JSON.stringify({...req,prazo}),signal:AbortSignal.timeout((prazo+30)*1000)});
 }catch(e:any){
  if(e?.name==='TimeoutError'||e?.name==='AbortError')return {ok:false,erro:'Tempo esgotado ao buscar o PDF. Retome o lote para tentar de novo.'};
  return null;
 }
 if(!r.ok){
  const d:any=await r.json().catch(()=>({}));
  throw new EngineRefused(r.status,String(d?.detail||'O motor recusou o pedido (HTTP '+r.status+').'));
 }
 return await r.json() as EngineDownload;
}

// --- configurações do motor -------------------------------------------------
// Única rota do motor que exige token: ela grava no motor/.env. O token vem do
// start.py (ORBIS_ENGINE_TOKEN) e só trafega entre servidores.
export type EngineConfig={itens:Record<string,{preenchido:boolean;valor:string}>};

async function callConfig(env:any,method:'GET'|'PUT',body?:unknown){
 const url=engineUrl(env);
 if(!url)throw new Error('O motor não está configurado neste ORBIS.');
 let r:Response;
 try{
  r=await fetch(url+'/config',{method,
   headers:{Accept:'application/json','x-orbis-token':String(env?.ORBIS_ENGINE_TOKEN||''),...(body?{'content-type':'application/json'}:{})},
   body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(8000)});
 }catch{throw new Error('O motor não está no ar.');}
 const d:any=await r.json().catch(()=>({}));
 if(!r.ok)throw new Error(String(d?.detail||'O motor recusou (HTTP '+r.status+').'));
 return d;
}

// Leitura: fora do ar ou recusando, a tela mostra o bloco do motor desabilitado.
export async function engineConfig(env:any):Promise<EngineConfig|null>{
 try{const d=await callConfig(env,'GET');return {itens:d?.itens||{}};}catch{return null;}
}

// Escrita: o erro sobe, porque a tela precisa dizer o que ficou pendente.
export async function saveEngineConfig(env:any,mudancas:Record<string,string|null>):Promise<{salvas:string[];reiniciar:string[]}>{
 const d=await callConfig(env,'PUT',{mudancas});
 return {salvas:d?.salvas||[],reiniciar:d?.reiniciar||[]};
}
