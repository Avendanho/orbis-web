// Executa a triagem por IA dentro do ORBIS, produzindo exatamente o mesmo
// formato que hoje é colado à mão.
//
// É a decisão central desta integração: `makeAIPackage` já monta o pacote que
// vai para a IA, e `importAI` já sabe receber a resposta, detectar duplicata,
// comparar com outras IAs e apontar divergência. Se a chamada automática
// devolver um `ORBIS_AI_RESULTS_V1`, ela entra pela mesma porta — e toda a
// interface de consenso, adjudicação e histórico funciona sem alteração.
//
// Nada aqui decide nada. O resultado é rascunho, como o importado: quem aplica
// é o pesquisador (regra 2 do projeto).
import {makeAIPackage,parseAI,normalizeAIResponse,type AIStage} from './ai-analysis';
import {callWithRetry,LlmCallFailed,type Provider} from './ai-provider';

export const SYSTEM_PROMPT=
 'Você é um revisor de triagem em uma revisão sistemática. Avalie cada artigo '+
 'estritamente pelos critérios fornecidos. Responda EXCLUSIVAMENTE com um único '+
 'objeto JSON válido, sem texto antes ou depois e sem cercas de código.\n\n'+
 'Regras de julgamento:\n'+
 '1. Aplique os critérios com rigor, um a um.\n'+
 '2. Só responda "nao" quando um critério falhar de fato, citando a evidência.\n'+
 '3. Na dúvida, ou sem evidência suficiente no texto fornecido, responda '+
 '"indeterminado". Nunca exclua por ausência de informação.\n'+
 '4. Cite trechos literais do artigo como evidência; não parafraseie.';

// Lotes pequenos: o custo de um lote que falha é ter de refazer tudo, e um
// pacote grande demais também estoura o limite de contexto do provedor.
export const BATCH_SIZE=5;
// Na PCC cada artigo leva o texto completo; 2 por chamada cabem no contexto
// dos provedores com folga.
export const PCC_BATCH_SIZE=2;
export const TEXT_LIMIT=60000;

// Anexa o texto extraído pelo motor aos itens da PCC. Texto que não pôde ser
// lido deixa o item como está: a IA responde null, como já faz sem PDF.
export async function withFullText(project:any,items:any[],readText:(key:string)=>Promise<string|null>){
 const out:any[]=[];
 for(const item of items){
  const a=project.state.articles.find((x:any)=>x.id===item.article_id);
  const t=a?.texto?.key?await readText(a.texto.key).catch(()=>null):null;
  out.push(t?{...item,texto_completo:t.slice(0,TEXT_LIMIT),texto_truncado:t.length>TEXT_LIMIT||!!a.texto.truncado}:item);
 }
 return out;
}

export function buildUserPrompt(pkg:any,items:any[]):string{
 return JSON.stringify({
  instrucoes:pkg.instrucoes,
  criterios:pkg.criterios,
  formato_resposta:pkg.formato_resposta,
  artigos:items,
 },null,1);
}

export function chunk<T>(list:T[],size=BATCH_SIZE):T[][]{
 const out:T[][]=[];
 for(let i=0;i<list.length;i+=Math.max(1,size))out.push(list.slice(i,i+Math.max(1,size)));
 return out;
}

export type RunOutcome={
 response:any|null;          // pronto para `importAI`
 analysed:number;
 failures:{articles:string[];reason:string}[];
};

// Roda a triagem sobre os artigos do pacote e devolve UMA resposta agregada,
// no formato que `importAI` consome.
export async function runAITriage(project:any,stage:AIStage,provider:Provider,opts:{batchSize?:number;onProgress?:(done:number,total:number)=>void;readText?:(key:string)=>Promise<string|null>}={}):Promise<RunOutcome>{
 const pkg=makeAIPackage(project,stage);
 const base:any[]=Array.isArray(pkg.items)?pkg.items:[];
 const itens=stage==='pcc'&&opts.readText?await withFullText(project,base,opts.readText):base;
 if(!itens.length)return {response:null,analysed:0,failures:[]};

 const lotes=chunk(itens,opts.batchSize??(stage==='pcc'?PCC_BATCH_SIZE:BATCH_SIZE));
 const recebidos:any[]=[];
 const failures:RunOutcome['failures']=[];
 let feitos=0;

 for(const lote of lotes){
  try{
   const texto=await callWithRetry(()=>provider.complete(SYSTEM_PROMPT,buildUserPrompt(pkg,lote)));
   const bruto=normalizeAIResponse(parseAI(texto),stage);
   const devolvidos=Array.isArray(bruto?.items)?bruto.items:[];
   if(!devolvidos.length)throw new Error('a resposta não trouxe nenhum artigo avaliado');
   recebidos.push(...devolvidos);
  }catch(e){
   // Falha técnica não vira parecer: o artigo continua pendente e o motivo
   // fica registrado, distinguível de uma decisão da IA.
   failures.push({
    articles:lote.map((i:any)=>String(i.arquivo||i.article_id||'?')),
    reason:e instanceof LlmCallFailed?e.message
      :e instanceof Error?'Resposta inválida do provedor: '+e.message
      :'Falha desconhecida ao analisar o lote.',
   });
  }
  feitos+=lote.length;
  opts.onProgress?.(feitos,itens.length);
 }

 if(!recebidos.length)return {response:null,analysed:0,failures};

 return {
  response:{...pkg.formato_resposta,provider:provider.name,model:provider.model,items:recebidos},
  analysed:recebidos.length,
  failures,
 };
}
