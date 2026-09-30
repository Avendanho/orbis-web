// Cliente de LLM com repetição — a parte que faltava para a triagem por IA
// deixar de ser copiar-e-colar.
//
// A distinção que organiza o módulo: **uma chamada que caiu não é um parecer.**
// Sem isso, rodando vários artigos em sequência, um 429 do provedor viraria
// silenciosamente uma decisão de triagem e entraria na contagem PRISMA como
// artigo avaliado. Aqui a falha sobe como `LlmCallFailed`, um tipo próprio,
// para o chamador conseguir registrá-la como falha técnica.
//
// Repete o que pode melhorar (429, 5xx, queda de conexão) e desiste na hora do
// que não melhora (chave inválida, prompt grande demais, recusa de conteúdo).

export const DEFAULT_ATTEMPTS=4;
export const BASE_DELAY=1500;   // ms, dobrados a cada tentativa
export const MAX_DELAY=30000;

const TRANSITORIO=[
 '429','rate limit','rate_limit','quota','resource_exhausted','too many requests',
 '500','502','503','504','529','internal server error','bad gateway',
 'service unavailable','overloaded','timeout','timed out','connection reset',
 'connection error','temporarily unavailable','network',
];
const DEFINITIVO=[
 '401','403','unauthenticated','unauthorized','permission denied',
 'api key not valid','invalid api key','invalid_api_key',
 'maximum token','context length','too long','content filter','safety','blocked',
];

export class LlmCallFailed extends Error{
 attempts:number;
 constructor(message:string,attempts:number){super(message);this.name='LlmCallFailed';this.attempts=attempts;}
}

export function isRetryable(err:unknown):boolean{
 const t=(err instanceof Error?err.name+': '+err.message:String(err)).toLowerCase();
 if(DEFINITIVO.some(m=>t.includes(m)))return false;
 return TRANSITORIO.some(m=>t.includes(m));
}

export function backoffDelay(attempt:number):number{
 const janela=Math.min(MAX_DELAY,BASE_DELAY*Math.pow(2,attempt));
 return Math.round(janela/2+Math.random()*(janela/2));
}

export async function callWithRetry<T>(fn:()=>Promise<T>,attempts=DEFAULT_ATTEMPTS,sleep=(ms:number)=>new Promise(r=>setTimeout(r,ms))):Promise<T>{
 let ultima:unknown;let feitas=0;
 for(let i=0;i<Math.max(1,attempts);i++){
  feitas=i+1;
  try{return await fn();}
  catch(e){
   ultima=e;
   if(!isRetryable(e)||i===attempts-1)break;
   await sleep(backoffDelay(i));
  }
 }
 const motivo=ultima instanceof Error?ultima.message:String(ultima);
 throw new LlmCallFailed('A chamada ao provedor de IA falhou após '+feitas+' tentativa(s): '+motivo,feitas);
}

// --- provedores -----------------------------------------------------------
//
// As chaves e preferências chegam num registro plano (os mesmos nomes das
// variáveis de ambiente e da tela de Configurações), nunca do repositório. Sem
// provedor nenhum, a triagem automática não é oferecida e o fluxo de
// importação manual continua sendo o caminho.

export type Provider={name:string;model:string;complete:(system:string,user:string)=>Promise<string>;
 // Modelo local: contexto pequeno, então o texto completo vai cortado e um
 // artigo por chamada na PCC.
 local?:boolean;limiteTexto?:number};

type Fetch=typeof fetch;
export const MODELOS_PADRAO={anthropic:'claude-sonnet-5',openai:'gpt-4o-mini',gemini:'gemini-2.5-flash'};
export const OLLAMA_PADRAO={url:'http://localhost:11434',modelo:'qwen3:14b',contexto:16384,prazo:600};
export type OllamaConfig={url:string;modelo:string;contexto:number;prazo:number};

const inteiro=(v:any,padrao:number,min:number,max:number)=>{const n=Number(String(v??'').trim()||NaN);return Number.isInteger(n)?Math.min(max,Math.max(min,n)):padrao};
const semBarra=(u:string)=>u.replace(/\/+$/,'');

export function configOllama(v:Record<string,any>):OllamaConfig{
 return {url:semBarra(String(v.OLLAMA_URL||'').trim()||OLLAMA_PADRAO.url),modelo:String(v.OLLAMA_MODELO||'').trim()||OLLAMA_PADRAO.modelo,
  contexto:inteiro(v.OLLAMA_CONTEXTO,OLLAMA_PADRAO.contexto,2048,131072),prazo:inteiro(v.OLLAMA_PRAZO,OLLAMA_PADRAO.prazo,30,3600)};
}

// Pelo Ollama já instalado na máquina (GPU local). `think:false` desliga o
// raciocínio do Qwen3, que só gastaria tempo aqui; `format:'json'` prende a
// saída ao JSON que o importador espera.
export function ollamaProvider(opts:Partial<OllamaConfig>={},fetchImpl:Fetch=fetch):Provider{
 const c={...OLLAMA_PADRAO,...Object.fromEntries(Object.entries(opts).filter(([,x])=>x!=null&&x!==''))} as OllamaConfig,url=semBarra(c.url);
 return {name:'Ollama',model:c.modelo,local:true,
  // ~3 caracteres por token; 6000 tokens ficam para instruções, critérios e resposta.
  limiteTexto:Math.min(60000,Math.max(8000,(c.contexto-6000)*3)),
  complete:async(system,user)=>{
   let r:Response;
   try{
    r=await fetchImpl(url+'/api/chat',{method:'POST',headers:{'content-type':'application/json'},
     body:JSON.stringify({model:c.modelo,stream:false,format:'json',think:false,options:{temperature:0,num_ctx:c.contexto},
      messages:[{role:'system',content:system},{role:'user',content:user}]}),
     signal:AbortSignal.timeout(c.prazo*1000)});
   }catch(e:any){
    // Sem as palavras que `isRetryable` repete: o mesmo prazo estouraria de novo.
    if(e?.name==='TimeoutError'||e?.name==='AbortError')throw new Error('O Ollama não respondeu em '+c.prazo+' s. Aumente o prazo em Configurações ou use um modelo menor.');
    throw new Error('Ollama fora do ar em '+url+' ('+(e?.message||e)+').');
   }
   if(!r.ok)throw new Error('Ollama HTTP '+r.status+': '+(await r.text()).slice(0,300));
   const d:any=await r.json();
   if(d?.error)throw new Error('Ollama: '+String(d.error).slice(0,300));
   return String(d?.message?.content||'');
  }};
}

// Modelos instalados (para a tela escolher); null = Ollama fora do ar.
export async function listLocalModels(url:string,fetchImpl:Fetch=fetch):Promise<string[]|null>{
 try{
  const r=await fetchImpl(semBarra(url)+'/api/tags',{signal:AbortSignal.timeout(2000)});
  if(!r.ok)return null;
  const d:any=await r.json();
  return Array.isArray(d?.models)?d.models.map((m:any)=>String(m?.name||'')).filter(Boolean):[];
 }catch{return null;}
}
export const ollamaOnline=async(url:string,fetchImpl:Fetch=fetch)=>(await listLocalModels(url,fetchImpl))!==null;

function anthropic(key:string,model:string):Provider{return {
 name:'Anthropic',model,
 complete:async(system,user)=>{
  const r=await fetch('https://api.anthropic.com/v1/messages',{
   method:'POST',
   headers:{'content-type':'application/json','x-api-key':key,'anthropic-version':'2023-06-01'},
   body:JSON.stringify({model,max_tokens:8192,system,messages:[{role:'user',content:user}]}),
   signal:AbortSignal.timeout(120000),
  });
  if(!r.ok)throw new Error('Anthropic HTTP '+r.status+': '+(await r.text()).slice(0,300));
  const d:any=await r.json();
  return String(d?.content?.[0]?.text||'');
 }};}

function openai(key:string,model:string):Provider{return {
 name:'OpenAI',model,
 complete:async(system,user)=>{
  const r=await fetch('https://api.openai.com/v1/chat/completions',{
   method:'POST',
   headers:{'content-type':'application/json',authorization:'Bearer '+key},
   body:JSON.stringify({model,temperature:0,response_format:{type:'json_object'},
    messages:[{role:'system',content:system},{role:'user',content:user}]}),
   signal:AbortSignal.timeout(120000),
  });
  if(!r.ok)throw new Error('OpenAI HTTP '+r.status+': '+(await r.text()).slice(0,300));
  const d:any=await r.json();
  return String(d?.choices?.[0]?.message?.content||'');
 }};}

function gemini(key:string,model:string):Provider{return {
 name:'Google',model,
 complete:async(system,user)=>{
  // O modelo vem da configuração e entra no caminho da URL: codificado, sempre.
  const r=await fetch('https://generativelanguage.googleapis.com/v1beta/models/'+encodeURIComponent(model)+':generateContent?key='+encodeURIComponent(key),{
   method:'POST',headers:{'content-type':'application/json'},
   body:JSON.stringify({systemInstruction:{parts:[{text:system}]},contents:[{parts:[{text:user}]}],
    generationConfig:{temperature:0,responseMimeType:'application/json'}}),
   signal:AbortSignal.timeout(120000),
  });
  if(!r.ok)throw new Error('Gemini HTTP '+r.status+': '+(await r.text()).slice(0,300));
  const d:any=await r.json();
  return String(d?.candidates?.[0]?.content?.parts?.[0]?.text||'');
 }};}

// Automático: o modelo local, se o Ollama responde; se não, a primeira chave de
// nuvem na ordem de sempre. Com preferência, só aquele provedor — escolher
// Anthropic e ser atendido pela OpenAI sem saber mudaria quem avaliou os artigos.
export async function pickProvider(v:Record<string,any>,opts:{online?:(url:string)=>Promise<boolean>}={}):Promise<Provider|null>{
 const k=(x:string)=>String(v[x]??'').trim();
 const nuvem:Record<string,()=>Provider|null>={
  anthropic:()=>k('ANTHROPIC_API_KEY')?anthropic(k('ANTHROPIC_API_KEY'),k('ORBIS_IA_MODELO_ANTHROPIC')||MODELOS_PADRAO.anthropic):null,
  openai:()=>k('OPENAI_API_KEY')?openai(k('OPENAI_API_KEY'),k('ORBIS_IA_MODELO_OPENAI')||MODELOS_PADRAO.openai):null,
  gemini:()=>k('GEMINI_API_KEY')?gemini(k('GEMINI_API_KEY'),k('ORBIS_IA_MODELO_GEMINI')||MODELOS_PADRAO.gemini):null,
 };
 const ollama=configOllama(v),preferido=k('ORBIS_IA_PROVEDOR').toLowerCase()||'automatico';
 if(preferido==='local')return ollamaProvider(ollama);
 if(nuvem[preferido])return nuvem[preferido]();
 if(await (opts.online||ollamaOnline)(ollama.url))return ollamaProvider(ollama);
 return nuvem.anthropic()||nuvem.openai()||nuvem.gemini();
}
