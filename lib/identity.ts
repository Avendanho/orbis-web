// O PDF baixado é mesmo o artigo pedido?
//
// Até aqui o ORBIS conferia só o cabeçalho `%PDF-` antes de incorporar. Isso
// garante que o arquivo é um PDF, não que seja *este* artigo. A diferença não é
// teórica: numa auditoria de 449 PDFs recuperados por um pipeline equivalente,
// 4 tinham os metadados corretos e não eram o artigo — material suplementar,
// um formulário de submissão da Nature, um caderno de resumos de congresso e um
// arquivo ilegível. Todos entrariam no corpus e seriam triados como se fossem
// o artigo.
//
// A regra que organiza o módulo: **o DOI conferido na página é evidência
// forte; o resto é indício; e "não deu para verificar" nunca vira
// "confirmado"**. A direção segura do erro é recusar e deixar pendente,
// porque um artigo de fora entra na contagem PRISMA e ninguém percebe.

// --- semelhança de título -------------------------------------------------

function normaliza(v:string){
 return String(v||'').normalize('NFKD').replace(/[̀-ͯ]/g,'')
  .toLowerCase().replace(/[^a-z0-9]+/g,' ').trim();
}

// Dice sobre bigramas de caracteres: tolera hífen, caixa e pontuação, e
// penaliza troca de palavra — que é o que distingue dois artigos parecidos.
function bigramas(s:string){
 const out=new Map<string,number>();
 for(let i=0;i<s.length-1;i++){const g=s.slice(i,i+2);out.set(g,(out.get(g)||0)+1);}
 return out;
}

export function titleSimilarity(a:string,b:string):number{
 const x=normaliza(a),y=normaliza(b);
 if(!x||!y)return 0;
 if(x===y)return 1;
 const ga=bigramas(x),gb=bigramas(y);
 let comuns=0;
 for(const [g,n] of ga){const m=gb.get(g);if(m)comuns+=Math.min(n,m);}
 const totalA=[...ga.values()].reduce((s,n)=>s+n,0);
 const totalB=[...gb.values()].reduce((s,n)=>s+n,0);
 const dice=(2*comuns)/(totalA+totalB);
 // Cobertura das palavras do título pedido: um subtítulo omitido mantém
 // cobertura alta, enquanto uma palavra trocada a derruba.
 const pa=new Set(x.split(' ')),pb=new Set(y.split(' '));
 let dentro=0;for(const p of pa)if(pb.has(p))dentro++;
 const cobertura=dentro/pa.size;
 return Math.min(1,0.55*dice+0.45*cobertura);
}

// Limiar alto, e de propósito. Medição que levou a esse número:
//
//   "Upside-down protein crystallization" vs "Upside Down Protein Cryst."  1.000  mesmo artigo
//   "Autism spectrum disorder: neuropathology" vs "Autism spectrum dis."   0.752  mesmo artigo
//   "Membrane protein crystallization" vs "Biomolecular membrane prot."    0.905  OUTRO artigo
//
// O artigo diferente pontua MAIS ALTO que o mesmo artigo com subtítulo
// omitido. Nenhum limiar separa os dois, porque o sinal não está no título —
// os dois casos são a mesma relação de subconjunto. Por isso o limiar fica
// perto da igualdade: aprovar por título só quando praticamente não há dúvida,
// e mandar o resto para conferência humana. Um artigo pendente custa um
// clique; um artigo errado entra na revisão e ninguém percebe.
export const TITLE_MIN=0.95;
export const YEAR_TOLERANCE=1; // publicação online costuma anteceder o fascículo

// --- veredito -------------------------------------------------------------

export type Evidence={pageDoi?:string;pageTitle?:string;pageYear?:string};
export type Verdict={ok:boolean;method:string;score:number;detail:string};

const doiIgual=(a?:string,b?:string)=>
 !!a&&!!b&&String(a).trim().toLowerCase()===String(b).trim().toLowerCase();

export function checkIdentity(article:{doi?:string;title?:string;year?:string},ev:Evidence):Verdict{
 // 1. DOI declarado pela página. É a única evidência que decide sozinha.
 if(ev.pageDoi){
  if(doiIgual(ev.pageDoi,article.doi))
   return {ok:true,method:'doi_na_pagina',score:1,detail:'A página declara o mesmo DOI.'};
  return {ok:false,method:'doi_divergente',score:0,
   detail:'A página declara outro DOI ('+ev.pageDoi+'). O arquivo é de outro artigo.'};
 }

 // 2. Título, com o ano como desempate quando houver.
 if(ev.pageTitle&&article.title){
  const s=titleSimilarity(article.title,ev.pageTitle);
  if(s<TITLE_MIN)
   return {ok:false,method:s<0.6?'titulo_divergente':'titulo_insuficiente',score:s,
    detail:s<0.6
     ?'O título da página não corresponde ao artigo pedido.'
     :'O título é parecido, mas não idêntico, e a página não declarou o DOI. Confira o arquivo antes de incorporar.'};
  if(ev.pageYear&&article.year){
   const d=Math.abs(Number(ev.pageYear)-Number(article.year));
   if(Number.isFinite(d)&&d>YEAR_TOLERANCE)
    return {ok:false,method:'ano_divergente',score:s,
     detail:'Título compatível, mas o ano ('+ev.pageYear+') destoa do esperado ('+article.year+').'};
   return {ok:true,method:'titulo_e_ano',score:Math.min(1,s+0.05),
    detail:'Título e ano conferem com o artigo pedido.'};
  }
  return {ok:true,method:'titulo',score:s*0.9,
   detail:'Título confere; a página não declarou DOI nem ano.'};
 }

 // 3. Nada para conferir. Não é aprovação.
 return {ok:false,method:'sem_evidencia',score:0,
  detail:'A fonte não forneceu DOI nem título para conferir a identidade do arquivo.'};
}

// --- é um artigo, ou outra coisa? -----------------------------------------
//
// Procurado só no topo do documento: no meio do texto, "supplementary table" é
// uma referência legítima que o artigo faz ao próprio material extra.
const NAO_E_ARTIGO:[RegExp,string][]=[
 [/^\s*(supplementary|supplemental)\s+(table|figure|material|information|data|note)/im,
  'O arquivo é material suplementar, não o artigo.'],
 [/reporting summary|life sciences reporting/i,
  'O arquivo é um formulário editorial, não o artigo.'],
 [/^\s*(author index|abstracts?\s+by\s+(number|author)|index of abstracts)/im,
  'O arquivo é um índice de resumos, não o artigo.'],
];

const LINHAS_CABECALHO=25;
const MIN_TEXTO=200;

export function looksLikeArticle(text:string):{ok:boolean;detail:string}{
 const t=String(text||'').trim();
 if(t.length<MIN_TEXTO)
  return {ok:false,detail:'O PDF não tem texto legível (imagem, truncado ou corrompido).'};
 const cabecalho=t.split('\n').slice(0,LINHAS_CABECALHO).join('\n');
 for(const [rx,motivo] of NAO_E_ARTIGO)if(rx.test(cabecalho))return {ok:false,detail:motivo};
 return {ok:true,detail:'O arquivo tem a forma de um artigo.'};
}
