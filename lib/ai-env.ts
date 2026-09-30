// Chaves e preferências da IA como o servidor as vê: o ambiente do Worker
// (vars e segredos), com recurso a process.env e globalThis. A tela de
// Configurações (parte D) passa a vir na frente disto.
import {env as cfEnv} from 'cloudflare:workers';

export const CHAVES_IA=['ANTHROPIC_API_KEY','OPENAI_API_KEY','GEMINI_API_KEY','ORBIS_IA_PROVEDOR','ORBIS_IA_MODELO_ANTHROPIC','ORBIS_IA_MODELO_OPENAI','ORBIS_IA_MODELO_GEMINI','OLLAMA_URL','OLLAMA_MODELO','OLLAMA_CONTEXTO','OLLAMA_PRAZO','ORBIS_IA_LOTE'];

export function iaValores():Record<string,string>{
 const out:Record<string,string>={};
 for(const k of CHAVES_IA){
  const v=(cfEnv as any)?.[k]??(globalThis as any)[k]??(typeof process!=='undefined'?process.env?.[k]:undefined);
  if(v!=null&&String(v).trim())out[k]=String(v).trim();
 }
 return out;
}
