// Regras que decidem o que dizer sobre um DOI quando o PDF não aparece.
//
// Por que existe: o lote marcava como "Consulta pendente" tudo que tivesse
// qualquer problema em qualquer fonte. Na prática, o motivo quase sempre era
// um de três, e nenhum deles se resolve tentando de novo:
//  - o Semantic Scholar recusou por cota (fonte redundante; as outras
//    responderam);
//  - o "DOI" era um número de registro do CENTRAL, que não existe no doi.org;
//  - o registro não é artigo completo (dados, resumo de congresso, editorial).
// Sem imports: os testes carregam este arquivo direto.

type Motivo={reasonCode:string;reason:string;reasonDetail:string};

// CENTRAL (registro de ensaios da Cochrane) exporta "10.1002/central/CN-…" no
// campo de DOI do RIS. Crossref, DataCite e doi.org respondem 404: só existe
// dentro da Cochrane Library, que bloqueia acesso automático.
export function isCentralRecord(doi:string):boolean{return /^10\.1002\/central\/cn-/i.test(String(doi||'').trim());}

export const CENTRAL_REASON:Motivo={
 reasonCode:'central_record',
 reason:'Registro do CENTRAL, não é DOI de artigo',
 reasonDetail:'"10.1002/central/CN-…" é o número do registro na base de ensaios da Cochrane (CENTRAL), não o DOI do estudo. Traga o DOI ou o PMID do artigo original: eles aparecem no registro da Cochrane Library.',
};

export type RecordKind={kind:'dataset'|'abstract'|'editorial'}&Motivo;

const KINDS:Record<RecordKind['kind'],Motivo>={
 dataset:{reasonCode:'not_full_article',reason:'Conjunto de dados, não artigo',reasonDetail:'O DOI aponta para dados (por exemplo, arquivos no figshare), não para um artigo com PDF. Se houver um artigo publicado com esses dados, use o DOI dele.'},
 abstract:{reasonCode:'not_full_article',reason:'Resumo de congresso, não artigo completo',reasonDetail:'O registro é um resumo publicado em suplemento ou anais; normalmente não existe PDF próprio. Considere excluí-lo na triagem ou procurar o artigo completo dos mesmos autores.'},
 editorial:{reasonCode:'not_full_article',reason:'Editorial ou nota, não artigo de pesquisa',reasonDetail:'O registro é um editorial, uma nota ou uma correção. Considere excluí-lo na triagem.'},
};

// Título inteiro (ou seguido de ":" / travessão) — "Editorial board decisions…"
// é pesquisa, "Editorial: …" não é.
const TITULO_EDITORIAL=/^(from the editors?|editorial|editor'?s? note|erratum|corrigendum|correction)(\s*$|\s*[:—–-])/i;
const TITULO_RESUMOS=/^abstracts?\b|\babstracts for\b/i;
// Suplemento: "S267" (uma página) ou "S104-S176" (caderno inteiro de resumos).
const PAGINA_SUPLEMENTO=/^S(\d+)(?:\s*[-–]\s*S?(\d+))?$/i;

export function recordKind(m:{crType?:string;oaType?:string;dcType?:string;page?:string;title?:string}):RecordKind|null{
 const cr=String(m.crType||'').toLowerCase(),oa=String(m.oaType||'').toLowerCase(),dc=String(m.dcType||'').toLowerCase(),titulo=String(m.title||'').trim();
 const com=(kind:RecordKind['kind']):RecordKind=>({kind,...KINDS[kind]});
 if(dc==='dataset'||oa==='dataset'||cr==='dataset')return com('dataset');
 if(oa==='conference-abstract'||TITULO_RESUMOS.test(titulo))return com('abstract');
 const s=String(m.page||'').trim().match(PAGINA_SUPLEMENTO);
 if(s){const ini=Number(s[1]),fim=s[2]?Number(s[2]):ini;if(fim-ini===0||fim-ini>=29)return com('abstract');}
 if(['editorial','erratum','paratext','retraction'].includes(oa)||TITULO_EDITORIAL.test(titulo))return com('editorial');
 return null;
}

// Secundária: fonte redundante, que não segura o artigo como pendente.
// Bloqueio: o site recusou robôs (403) — tentar de novo não muda nada.
// Crítica: o resto; essa sim deixa a busca incompleta.
export function splitIssues(issues:string[]){
 const secundarias:string[]=[],bloqueios:string[]=[],criticas:string[]=[];
 for(const i of issues){
  if(/^Semantic Scholar:/.test(i))secundarias.push(i);
  else if(/bloqueio de acesso automático$/.test(i))bloqueios.push(i);
  else criticas.push(i);
 }
 return {secundarias,bloqueios,criticas};
}

export function isPartial(g:{criticas:string[]}):boolean{return g.criticas.length>0;}

export function escolherMotivo(a:{pdf:boolean;kind:RecordKind|null;bloqueios:string[];criticas:string[];secundarias?:string[];oa:boolean;base:Motivo}):Motivo{
 if(a.pdf)return a.base;
 const m=motivoSemPdf(a);
 // A fonte secundária não segura o artigo, mas pode ter o PDF que faltou.
 return a.secundarias?.length&&m.reasonCode!=='not_full_article'?{...m,reasonDetail:m.reasonDetail+' O Semantic Scholar não respondeu; use Retomar para consultá-lo de novo.'}:m;
}

function motivoSemPdf(a:{kind:RecordKind|null;bloqueios:string[];criticas:string[];oa:boolean;base:Motivo}):Motivo{
 if(a.kind)return {reasonCode:a.kind.reasonCode,reason:a.kind.reason,reasonDetail:a.kind.reasonDetail};
 if(a.bloqueios.length&&!a.criticas.length)return {
  reasonCode:'publisher_blocked',
  reason:'Site bloqueia acesso automático',
  reasonDetail:(a.oa?'Uma fonte indica que o artigo é gratuito, mas o site':'O site')+' recusa acesso automático (HTTP 403). Com o motor ligado, ele tenta pelo navegador e pelo acesso institucional; sem ele, abra a página do artigo e baixe o PDF manualmente.',
 };
 return a.base;
}
