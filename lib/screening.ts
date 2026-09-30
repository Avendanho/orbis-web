// Triagem de títulos e resumos ANTES do download.
//
// Os DOIs do lote já chegam com título e resumo buscados na web (Crossref,
// Europe PMC, Semantic Scholar, OpenAlex, página da editora). Cada um vira um
// registro, triado aqui com as mesmas regras da triagem do corpus, e só os
// incluídos são baixados. As decisões ficam na tabela `screening`, fora do
// estado do projeto: centenas de resumos estourariam o limite de 1,8 MB.
import {triageDecision} from './domain';
import {makeAIPackage,articleHash,criteriaHash,aiRows,type AIRow} from './ai-analysis';

export type Registro={doi:string;title:string;authors:string;year:string;journal:string;abstract:string;abstractSource:string};
export type Sugestao={provider:string;model:string;version:number;criteriaHash:string;sourceHash:string;decision:'incluir'|'excluir';questions:AIRow[];at:string};
export type LinhaTriagem={doi:string;decision:string|null;answers:string[];reasons:string[];reason:string;actor:string;version:number|null;source:string;ai:Sugestao|null};
export type Decisao={decision:'incluir'|'excluir';answers:string[];reasons:string[];reason:string;actor:string;version:number;source:string;provider?:string;model?:string};
export type Filtro='todos'|'pendente'|'incluir'|'excluir'|'sem_resumo';
type Situacao='pendente'|'incluir'|'excluir';
export const POR_PAGINA=50;
const texto=(x:any)=>typeof x==='string'?x:x==null?'':String(x);
const semCaixa=(doi:any)=>texto(doi).trim().toLowerCase();

// Só é registro o DOI cuja consulta terminou e achou metadados.
export function registroDe(row:any):Registro|null{
 const r=row?.result;
 if(!r?.found||!['done','partial'].includes(row?.status))return null;
 return {doi:texto(row.doi),title:texto(r.title),authors:texto(r.authors),year:texto(r.year),journal:texto(r.journal),abstract:texto(r.abstract),abstractSource:texto(r.abstractSource)};
}
// O registro no formato de artigo que o pacote de IA e o hash já conhecem.
export function pseudoArtigo(r:Registro){return {id:r.doi,filename:r.doi,doi:r.doi,title:r.title,authors:r.authors,abstract:r.abstract,abstractSource:r.abstractSource}}
export const registroHash=(r:Registro)=>articleHash(pseudoArtigo(r));

// Decisão de outra versão do protocolo não vale: o registro volta a pendente.
export function situacao(l:LinhaTriagem|null|undefined,versao:number):Situacao{
 return l&&l.version===versao&&(l.decision==='incluir'||l.decision==='excluir')?l.decision:'pendente';
}

export function listar(searches:any[],linhas:LinhaTriagem[],protocolo:any,{filtro='pendente',busca='',pagina=1}:{filtro?:Filtro;busca?:string;pagina?:number}={}){
 const porDoi=new Map(linhas.map(l=>[l.doi,l]));
 const todos=(searches.map(registroDe).filter(Boolean) as Registro[]).map(registro=>{
  const linha=porDoi.get(registro.doi)||null;
  return {registro,linha,situacao:situacao(linha,protocolo.version),antiga:!!linha?.decision&&linha.version!==protocolo.version,
   sugestao:linha?.ai&&sugestaoAtual(linha.ai,registro,protocolo)?linha.ai:null};
 });
 const contagens={todos:todos.length,pendente:0,incluir:0,excluir:0,sem_resumo:0};
 for(const x of todos){contagens[x.situacao]++;if(!x.registro.abstract.trim())contagens.sem_resumo++;}
 const q=busca.trim().toLocaleLowerCase();
 const filtrados=todos.filter(x=>(filtro==='todos'||(filtro==='sem_resumo'?!x.registro.abstract.trim():x.situacao===filtro))
  &&(!q||[x.registro.title,x.registro.authors,x.registro.doi,x.registro.journal,x.registro.abstract].join(' ').toLocaleLowerCase().includes(q)));
 const paginas=Math.max(1,Math.ceil(filtrados.length/POR_PAGINA)),atual=Math.min(Math.max(1,Math.floor(pagina)||1),paginas);
 return {itens:filtrados.slice((atual-1)*POR_PAGINA,atual*POR_PAGINA),total:filtrados.length,paginas,pagina:atual,contagens};
}

// Mesmas regras da triagem do corpus: protocolo aprovado, todas as perguntas,
// exclusão justificada, ações configuradas por pergunta.
export function decidir(protocolo:any,answers:any,reason:any,reasons:any,actor:string):Decisao{
 if(!protocolo?.approved)throw Error('Aprove o protocolo antes da triagem.');
 if(!Array.isArray(answers)||answers.length!==protocolo.questions.length)throw Error('Responda todas as perguntas.');
 const motivo=texto(reason).slice(0,20000);
 const decision=triageDecision(answers,motivo,protocolo.questionRules||[]) as 'incluir'|'excluir';
 return {decision,answers:answers.map(String),reasons:Array.isArray(reasons)?reasons.map((x:any)=>texto(x).slice(0,4000)):[],reason:motivo,actor,version:protocolo.version,source:''};
}

export function pacoteIA(projectId:string,protocolo:any,registros:Registro[]){
 return makeAIPackage({id:projectId,state:{protocol:protocolo,articles:[]}},'triagem',undefined,registros.map(pseudoArtigo));
}

// Uma resposta da IA vira sugestão; a decisão sai das ações de cada pergunta,
// nunca de uma "decisão" que a IA declare.
export function sugestaoDe(item:any,protocolo:any,reg:Registro,provider:string,model:string,at:string):Sugestao{
 const questions=aiRows(item?.triagem_nivel1??item?.questions,protocolo.questions,true);
 const regras=protocolo.questionRules||[];
 const acao=(a:string,i:number)=>a==='sim'?(regras[i]?.yesDecision||'include'):a==='nao'?(regras[i]?.noDecision||'exclude'):(regras[i]?.unknownDecision||'include');
 return {provider,model,version:protocolo.version,criteriaHash:criteriaHash(protocolo,'triagem'),sourceHash:registroHash(reg),
  decision:questions.some((q,i)=>acao(q.answer,i)==='exclude')?'excluir':'incluir',questions,at};
}

// Sugestão feita com outro protocolo, outros critérios ou outro resumo não vale.
export function sugestaoAtual(s:Sugestao|null|undefined,reg:Registro,protocolo:any):boolean{
 return !!s&&s.version===protocolo.version&&s.criteriaHash===criteriaHash(protocolo,'triagem')&&s.sourceHash===registroHash(reg);
}

export function aceitar(s:Sugestao,protocolo:any,actor:string):Decisao{
 const answers=s.questions.map(q=>q.answer==='sim'?'Sim':q.answer==='nao'?'Não':'Indeterminado');
 const reason=('Decisão aceita da análise '+s.provider+(s.model?' / '+s.model:'')+'. '
  +s.questions.map((q,i)=>(i+1)+'. '+q.reason+(q.evidence?' Evidência: '+q.evidence:'')).join(' ')).slice(0,20000);
 const decision=triageDecision(answers,reason,protocolo.questionRules||[]) as 'incluir'|'excluir';
 return {decision,answers,reasons:s.questions.map(q=>q.reason),reason,actor,version:protocolo.version,source:'ia_accepted',provider:s.provider,model:s.model};
}

// A decisão acompanha o artigo quando ele entra no corpus.
export function triagemParaArtigo(l:LinhaTriagem){
 return {answers:l.answers||[],reasons:l.reasons||[],reason:l.reason||'',decision:l.decision,actor:l.actor,version:l.version,
  source:l.source==='ia_accepted'?'ia_accepted':'triagem_pre_download'};
}

export function incluidosParaBaixar(linhas:LinhaTriagem[],versao:number,articles:any[]):string[]{
 const noCorpus=new Set(articles.map(a=>semCaixa(a?.doi)).filter(Boolean));
 return linhas.filter(l=>situacao(l,versao)==='incluir'&&!noCorpus.has(semCaixa(l.doi))).map(l=>l.doi);
}

// Contagens para o fluxograma: da identificação até o que foi obtido.
//
// Projetos de antes desta etapa foram triados no corpus, sem linha em
// `screening` (e às vezes sem o DOI no lote): a triagem do artigo vale.
export function contagensPrisma(searches:any[],linhas:LinhaTriagem[],articles:any[],versao:number){
 const porDoi=new Map(linhas.map(l=>[l.doi,l])),noCorpus=new Set(articles.map(a=>semCaixa(a?.doi)).filter(Boolean));
 const doArtigo=new Map(articles.filter(a=>a?.doi&&a.triage?.version===versao).map(a=>[semCaixa(a.doi),a.triage.decision]));
 const dois=new Set((searches.map(registroDe).filter(Boolean) as Registro[]).map(r=>semCaixa(r.doi)));
 for(const a of articles)if(a?.doi)dois.add(semCaixa(a.doi));
 let triados=0,excluidosTriagem=0,incluidosTriagem=0,obtidos=0;
 for(const doi of dois){
  let s:string=situacao(porDoi.get(doi),versao);
  if(s==='pendente'&&doArtigo.has(doi))s=doArtigo.get(doi);
  if(s!=='incluir'&&s!=='excluir')continue;
  triados++;
  if(s==='excluir'){excluidosTriagem++;continue}
  incluidosTriagem++;
  if(noCorpus.has(doi))obtidos++;
 }
 return {identificados:dois.size,triados,excluidosTriagem,incluidosTriagem,pendentesTriagem:dois.size-triados,
  buscados:incluidosTriagem,obtidos,naoObtidos:incluidosTriagem-obtidos,locais:articles.filter(a=>a?.source?.kind==='local').length};
}
