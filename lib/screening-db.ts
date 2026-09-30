// Leitura e gravação da tabela `screening` (triagem de títulos e resumos antes
// do download). As regras ficam em `screening.ts`; aqui só o banco.
import {ApiError} from './server';
import {situacao,type LinhaTriagem,type Decisao,type Sugestao} from './screening';

const json=(x:any,padrao:any)=>{try{return x?JSON.parse(x):padrao}catch{return padrao}};

function linha(row:any):LinhaTriagem{
 return {doi:String(row.doi),decision:row.decision||null,answers:json(row.answers,[]),reasons:json(row.reasons,[]),reason:row.reason||'',
  actor:row.actor||'',version:row.version??null,source:row.source||'',ai:json(row.ai,null)};
}

export async function linhasDoProjeto(db:any,project:string):Promise<LinhaTriagem[]>{
 return ((await db.prepare('SELECT * FROM screening WHERE project=? ORDER BY doi').bind(project).all()).results||[]).map(linha);
}

export async function linhaDe(db:any,project:string,doi:string):Promise<LinhaTriagem|null>{
 const r=await db.prepare('SELECT * FROM screening WHERE project=? AND doi=?').bind(project,doi).first();
 return r?linha(r):null;
}

// Devolvem o comando preparado, para caber num `db.batch`. A decisão preserva a
// sugestão da IA, e a sugestão preserva a decisão.
export function gravarDecisao(db:any,project:string,doi:string,d:Decisao){
 return db.prepare('INSERT INTO screening(project,doi,decision,answers,reasons,reason,actor,version,source,updated) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(project,doi) DO UPDATE SET decision=excluded.decision,answers=excluded.answers,reasons=excluded.reasons,reason=excluded.reason,actor=excluded.actor,version=excluded.version,source=excluded.source,updated=excluded.updated')
  .bind(project,doi,d.decision,JSON.stringify(d.answers),JSON.stringify(d.reasons),d.reason,d.actor,d.version,d.source||'',new Date().toISOString());
}

export function gravarSugestao(db:any,project:string,doi:string,s:Sugestao){
 return db.prepare('INSERT INTO screening(project,doi,ai,updated) VALUES(?,?,?,?) ON CONFLICT(project,doi) DO UPDATE SET ai=excluded.ai,updated=excluded.updated')
  .bind(project,doi,JSON.stringify(s),new Date().toISOString());
}

export function apagarTriagem(db:any,project:string){
 return db.prepare('DELETE FROM screening WHERE project=?').bind(project).run();
}

// Nada é baixado sem ter passado pela triagem na versão atual do protocolo.
export async function exigirInclusao(db:any,project:string,doi:string,versao:number):Promise<LinhaTriagem>{
 const l=await linhaDe(db,project,doi);
 if(situacao(l,versao)!=='incluir')throw new ApiError(409,'Trie este registro por título e resumo e inclua-o antes de baixar o PDF.');
 return l!;
}

// Linhas de um backup: só as que têm DOI válido e decisão reconhecida.
export function linhasDoBackup(lista:any[]):any[]{
 return (Array.isArray(lista)?lista:[]).filter((x:any)=>x&&/^10\.\d{4,9}\/\S+$/i.test(String(x.doi||''))&&(x.decision==null||['incluir','excluir'].includes(x.decision)));
}

export function restaurarLinha(db:any,project:string,x:any){
 return db.prepare('INSERT INTO screening(project,doi,decision,answers,reasons,reason,actor,version,source,ai,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(project,doi) DO NOTHING')
  .bind(project,String(x.doi).toLowerCase(),x.decision??null,JSON.stringify(Array.isArray(x.answers)?x.answers:[]),JSON.stringify(Array.isArray(x.reasons)?x.reasons:[]),
   String(x.reason||'').slice(0,20000),String(x.actor||''),Number.isInteger(x.version)?x.version:null,String(x.source||''),x.ai?JSON.stringify(x.ai):null,new Date().toISOString());
}
