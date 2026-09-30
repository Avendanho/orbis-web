// Onde as configurações da tela moram (D1) e como o servidor as lê.
//
// A leitura nunca derruba quem chama: sem banco ou sem a tabela (instalação
// que ainda não rodou a migração), valem o ambiente e os padrões — exatamente
// o comportamento de antes desta tela existir.
import {env as cfEnv} from 'cloudflare:workers';
import {database} from '@/lib/server';
import {CATALOGO,resolver,valores,type Resolvido} from '@/lib/settings';

function ambiente():Record<string,unknown>{
 const out:Record<string,unknown>={};
 for(const i of CATALOGO)for(const k of [i.key,...(i.aliases||[])]){
  const v=(cfEnv as any)?.[k]??(globalThis as any)[k]??(typeof process!=='undefined'?process.env?.[k]:undefined);
  if(v!=null&&v!=='')out[k]=v;
 }
 return out;
}

async function salvos():Promise<Record<string,string>>{
 try{
  const r=await database().prepare('SELECT key,value FROM settings').all<{key:string;value:string}>();
  return Object.fromEntries((r.results||[]).map(x=>[x.key,x.value]));
 }catch{return {};}
}

export async function loadSettings():Promise<Resolvido>{return resolver(await salvos(),ambiente());}
export async function settingsValues():Promise<Record<string,string>>{return valores(await loadSettings());}

export async function gravarSettings(m:Record<string,string|null>):Promise<void>{
 const db=database(),at=new Date().toISOString();
 const ops=Object.entries(m).map(([k,v])=>v==null
  ?db.prepare('DELETE FROM settings WHERE key=?').bind(k)
  :db.prepare('INSERT INTO settings(key,value,updated) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated=excluded.updated').bind(k,v,at));
 if(ops.length)await db.batch(ops);
}

// O que a resolução por DOI precisa das configurações.
export async function resolveOpts(){const v=await settingsValues();return {unpaywallEmail:v.UNPAYWALL_EMAIL||undefined,s2ApiKey:v.SEMANTIC_SCHOLAR_API_KEY||undefined};}
