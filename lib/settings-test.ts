// Botão "Testar" da tela: uma chamada real mínima por credencial. A resposta
// nunca carrega o valor da chave, nem quando o serviço o devolve no erro.
import {searchEmbase} from '@/lib/sources/embase-search';
import {searchLilacs} from '@/lib/sources/lilacs-search';
import {searchUrl as pubmedUrl} from '@/lib/sources/pubmed-search';
import {pickProvider,configOllama,listLocalModels,ollamaProvider} from '@/lib/ai-provider';
import {traduzirErro,semSegredos,type Alvo} from '@/lib/settings';

type Resultado={ok:boolean;detalhe:string};
// A OpenAI em modo JSON exige a palavra "JSON" no pedido.
const PEDIDO_MINIMO:[string,string]=['Responda somente com o JSON {"ok":true}.','teste'];

export async function testar(alvo:Alvo,v:Record<string,string>):Promise<Resultado>{
 try{return await executar(alvo,v);}
 catch(e:any){return {ok:false,detalhe:semSegredos(traduzirErro(String(e?.message||e)),v)};}
}

async function executar(alvo:Alvo,v:Record<string,string>):Promise<Resultado>{
 if(alvo==='ncbi'){
  const r=await fetch(pubmedUrl('cancer',{retmax:1,apiKey:v.NCBI_API_KEY||undefined,email:v.NCBI_EMAIL||undefined}),{signal:AbortSignal.timeout(15000)});
  await r.body?.cancel();
  if(!r.ok)throw new Error('HTTP '+r.status);
  return {ok:true,detalhe:v.NCBI_API_KEY?'PubMed respondeu com a sua chave.':'PubMed respondeu (sem chave: limite menor de consultas).'};
 }
 if(alvo==='lilacs'){
  const r=await searchLilacs('dengue',{retmax:1});
  return {ok:true,detalhe:'LILACS respondeu ('+r.total+' registros para "dengue").'};
 }
 if(alvo==='embase'){
  if(!v.ELSEVIER_API_KEY)return {ok:false,detalhe:'Sem chave da Elsevier cadastrada.'};
  const r=await searchEmbase('cancer',{retmax:1,apiKey:v.ELSEVIER_API_KEY,instToken:v.ELSEVIER_INST_TOKEN||undefined});
  return {ok:true,detalhe:'Embase respondeu ('+r.total+' registros).'};
 }
 if(alvo==='unpaywall'){
  if(!v.UNPAYWALL_EMAIL)return {ok:false,detalhe:'Sem e-mail do Unpaywall cadastrado.'};
  const r=await fetch('https://api.unpaywall.org/v2/10.1038/nature12373?email='+encodeURIComponent(v.UNPAYWALL_EMAIL),{signal:AbortSignal.timeout(15000)});
  await r.body?.cancel();
  if(!r.ok)throw new Error('HTTP '+r.status);
  return {ok:true,detalhe:'Unpaywall respondeu.'};
 }
 if(alvo==='ollama'){
  const c=configOllama(v),modelos=await listLocalModels(c.url);
  if(modelos===null)return {ok:false,detalhe:'O Ollama não respondeu em '+c.url+'. Ele está instalado e aberto?'};
  if(!modelos.some(m=>m===c.modelo||m===c.modelo+':latest'))return {ok:false,detalhe:'O modelo '+c.modelo+' não está instalado no Ollama. Instale com: ollama pull '+c.modelo};
  // Primeira chamada carrega o modelo na GPU: pode levar alguns segundos.
  await ollamaProvider(c).complete(...PEDIDO_MINIMO);
  return {ok:true,detalhe:'Ollama respondeu com '+c.modelo+'.'};
 }
 const chave={anthropic:v.ANTHROPIC_API_KEY,openai:v.OPENAI_API_KEY,gemini:v.GEMINI_API_KEY}[alvo];
 if(!chave)return {ok:false,detalhe:'Sem chave cadastrada.'};
 const p=(await pickProvider({...v,ORBIS_IA_PROVEDOR:alvo}))!;
 await p.complete(...PEDIDO_MINIMO);
 return {ok:true,detalhe:p.name+' ('+p.model+') respondeu.'};
}
