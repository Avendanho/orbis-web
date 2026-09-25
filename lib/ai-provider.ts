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
// A chave vem do ambiente do Worker, nunca do repositório. Sem chave nenhuma
// configurada, a triagem automática simplesmente não é oferecida na interface
// e o fluxo de importação manual continua sendo o caminho.

export type Provider={name:string;model:string;complete:(system:string,user:string)=>Promise<string>};

type Env={ANTHROPIC_API_KEY?:string;OPENAI_API_KEY?:string;GEMINI_API_KEY?:string};

export function pickProvider(env:Env):Provider|null{
 if(env.ANTHROPIC_API_KEY)return {
  name:'Anthropic',model:'claude-sonnet-5',
  complete:async(system,user)=>{
   const r=await fetch('https://api.anthropic.com/v1/messages',{
    method:'POST',
    headers:{'content-type':'application/json','x-api-key':env.ANTHROPIC_API_KEY!,'anthropic-version':'2023-06-01'},
    body:JSON.stringify({model:'claude-sonnet-5',max_tokens:8192,system,messages:[{role:'user',content:user}]}),
    signal:AbortSignal.timeout(120000),
   });
   if(!r.ok)throw new Error('Anthropic HTTP '+r.status+': '+(await r.text()).slice(0,300));
   const d:any=await r.json();
   return String(d?.content?.[0]?.text||'');
  }};

 if(env.OPENAI_API_KEY)return {
  name:'OpenAI',model:'gpt-4o-mini',
  complete:async(system,user)=>{
   const r=await fetch('https://api.openai.com/v1/chat/completions',{
    method:'POST',
    headers:{'content-type':'application/json',authorization:'Bearer '+env.OPENAI_API_KEY},
    body:JSON.stringify({model:'gpt-4o-mini',temperature:0,response_format:{type:'json_object'},
     messages:[{role:'system',content:system},{role:'user',content:user}]}),
    signal:AbortSignal.timeout(120000),
   });
   if(!r.ok)throw new Error('OpenAI HTTP '+r.status+': '+(await r.text()).slice(0,300));
   const d:any=await r.json();
   return String(d?.choices?.[0]?.message?.content||'');
  }};

 if(env.GEMINI_API_KEY)return {
  name:'Google',model:'gemini-2.5-flash',
  complete:async(system,user)=>{
   const r=await fetch('https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key='+encodeURIComponent(env.GEMINI_API_KEY!),{
    method:'POST',headers:{'content-type':'application/json'},
    body:JSON.stringify({systemInstruction:{parts:[{text:system}]},contents:[{parts:[{text:user}]}],
     generationConfig:{temperature:0,responseMimeType:'application/json'}}),
    signal:AbortSignal.timeout(120000),
   });
   if(!r.ok)throw new Error('Gemini HTTP '+r.status+': '+(await r.text()).slice(0,300));
   const d:any=await r.json();
   return String(d?.candidates?.[0]?.content?.parts?.[0]?.text||'');
  }};

 return null;
}
