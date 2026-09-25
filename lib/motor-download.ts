// Regras do download pelo motor, sem dependência do Worker: o que vai para o
// `search_items`, como vira artigo do corpus e onde fica o texto extraído.
//
// Nos dois modos o PDF NÃO vai para o R2: no modo "baixar" ele fica na pasta
// local do pesquisador; no "analisar" é lido e descartado. O que o ORBIS
// guarda é o texto, porque é a única base da análise que sobra.
import type {EngineDownload,EngineIdentity} from './python-bridge';

export type DownloadMode='baixar'|'analisar';
export const isMode=(v:any):v is DownloadMode=>v==='baixar'||v==='analisar';

export type MotorText={key:string;bytes:number;chars:number;paginas:number;truncado:boolean};
export type MotorResult={ok:boolean;modo:DownloadMode;fonte?:string;fontes:string[];arquivo?:string;identidade?:EngineIdentity;erro?:string;aviso?:string;texto?:MotorText|null};

// Mesmo endereço para o mesmo DOI no mesmo projeto: repetir o download
// sobrescreve em vez de duplicar, e a restauração recalcula para o projeto novo.
export async function textKey(project:string,ref:string){
 const d=new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(ref.toLowerCase())));
 return project+'/texto/'+Array.from(d.slice(0,16),x=>x.toString(16).padStart(2,'0')).join('')+'.txt';
}

export function motorSummary(res:EngineDownload,modo:DownloadMode,texto:{key:string;bytes:number}|null):MotorResult{
 if(!res.ok)return {ok:false,modo,erro:String(res.erro||'O motor não entregou um PDF válido.').slice(0,3000),fontes:res.fontes_tentadas||[],identidade:res.identidade};
 return {ok:true,modo,fonte:res.fonte||'',fontes:res.fontes_tentadas||[],arquivo:modo==='baixar'?res.arquivo:undefined,identidade:res.identidade,aviso:res.aviso,
  texto:texto?{key:texto.key,bytes:texto.bytes,chars:Number(res.chars)||0,paginas:Number(res.paginas)||0,truncado:!!res.texto_truncado}:null};
}

export function motorArticle(doi:string,metadata:any,articleId:string,filename:string,at:string){
 const m:MotorResult=metadata.motor;
 return {id:articleId,doi,title:metadata.title||'',authors:metadata.authors||'',year:metadata.year||'',abstract:metadata.abstract||'',filename,
  identity:{ok:true,method:'conteudo_pdf:'+(m.identidade?.metodo||''),score:m.identidade?.score??0,detail:m.identidade?.detalhe||'',checkedAt:at},
  source:{kind:'motor',modo:m.modo,fonte:m.fonte,arquivoLocal:m.modo==='baixar'?m.arquivo:undefined,reason:m.modo==='baixar'?'PDF validado e salvo na pasta local':'PDF validado, lido e descartado'},
  texto:m.texto?{...m.texto,origem:'motor'}:undefined};
}

export function motorNote(a:any):string{
 if(a?.source?.kind!=='motor')return '';
 const semTexto=' Sem texto extraído — a PCC automática responderá null.';
 if(a.source.modo==='baixar')return 'PDF na pasta local: '+(a.source.arquivoLocal||'—')+'.'+(a.texto?.key?'':semTexto);
 return a.texto?.key?'PDF lido e descartado; texto disponível para a análise.':'PDF lido e descartado.'+semTexto;
}

// Só chaves deste projeto são lidas ou apagadas: um backup restaurado não pode
// apontar para o texto de outro projeto.
export const ownKey=(project:string,key:any)=>typeof key==='string'&&key.startsWith(project+'/');

// Textos de DOIs que nunca entraram no corpus: ao limpar a busca, somem junto.
export function orphanTexts(rows:{result:string|null}[],articles:any[],project:string){
 const kept=new Set(articles.map(a=>a?.texto?.key).filter(Boolean));const keys:string[]=[];let bytes=0;
 for(const row of rows){let t:any;try{t=JSON.parse(row.result||'null')?.motor?.texto}catch{continue}if(ownKey(project,t?.key)&&!kept.has(t.key)){keys.push(t.key);bytes+=Number(t.bytes||0)}}
 return {keys,bytes};
}
