import {env} from 'cloudflare:workers';
import {identity,body,ok,fail,ApiError} from '@/lib/server';
import {ALVOS,validar,visaoPublica,separarPorDestino,valores,type Alvo} from '@/lib/settings';
import {loadSettings,settingsValues,gravarSettings} from '@/lib/settings-store';
import {engineConfig,saveEngineConfig} from '@/lib/python-bridge';
import {configOllama,listLocalModels} from '@/lib/ai-provider';
import {testar} from '@/lib/settings-test';

// Configurações da instalação. Valem para todos os projetos (a instalação é
// pessoal), mas exigem login como as outras rotas.
//
// Só a instalação local (o ORBIS do start.py, em localhost) muda ou testa
// configurações. Num ORBIS hospedado, qualquer pessoa logada mudaria a
// instalação de todos — por exemplo, apontar o Ollama para um servidor seu e
// receber os textos dos projetos dos outros. Lá valem as variáveis de ambiente.
const local=(r:Request)=>['localhost','127.0.0.1','::1'].includes(new URL(r.url).hostname.replace(/^\[|\]$/g,'').toLowerCase());
function exigirLocal(r:Request){if(!local(r))throw new ApiError(403,'As configurações só podem ser alteradas na instalação local (start.py). Nesta hospedagem, valem as variáveis de ambiente.');}
export async function GET(r:Request){try{
 await identity(r);
 const orbis=await loadSettings();
 const [motor,modelos]=await Promise.all([engineConfig(env),listLocalModels(configOllama(valores(orbis)).url)]);
 return ok({editavel:local(r),orbis:visaoPublica(orbis),motor:motor?{online:true,itens:motor.itens}:{online:false},ollama:{online:modelos!==null,modelos:modelos||[]}});
}catch(e){return fail(e)}}

export async function PUT(r:Request){try{
 await identity(r);exigirLocal(r);
 const m=(await body(r))?.mudancas;
 if(!m||typeof m!=='object'||Array.isArray(m))throw new ApiError(400,'Envie as mudanças em "mudancas".');
 // Valida tudo antes de gravar qualquer coisa: ou o pedido inteiro vale, ou nada.
 const validas:Record<string,string|null>={};
 for(const [k,v] of Object.entries(m)){try{validas[k]=v===null?null:validar(k,v)}catch(e:any){throw new ApiError(400,e.message)}}
 const {orbis,motor}=separarPorDestino(validas);
 if(Object.keys(orbis).length)await gravarSettings(orbis);
 let motorRes:any={enviado:false};
 if(Object.keys(motor).length){
  try{motorRes={enviado:true,...await saveEngineConfig(env,motor)}}
  catch(e:any){motorRes={enviado:false,pendentes:Object.keys(motor),erro:e.message}}
 }
 return ok({orbis:visaoPublica(await loadSettings()),salvasNoOrbis:Object.keys(orbis),motor:motorRes});
}catch(e){return fail(e)}}

export async function POST(r:Request){try{
 await identity(r);exigirLocal(r);
 const b=await body(r);
 if(b?.acao!=='testar'||!(ALVOS as readonly string[]).includes(b?.alvo))throw new ApiError(400,'Teste desconhecido.');
 return ok(await testar(b.alvo as Alvo,await settingsValues()));
}catch(e){return fail(e)}}
