// Busca na Cochrane Library, restrita às revisões sistemáticas (CDSR).
//
// A Cochrane Library não tem API pública e o site responde a clientes HTTP com
// um desafio anti-robô. Toda revisão do Cochrane Database of Systematic Reviews
// é indexada no PubMed, com DOI; então a busca vai ao PubMed com o periódico
// fixado. O que fica de fora é o CENTRAL (registro de ensaios), que só a
// própria Cochrane serve — e a interface deixa isso explícito.
//
// A expressão do pesquisador vai entre parênteses, intacta: o filtro só estreita.
export const CDSR='"Cochrane Database Syst Rev"[Journal]';

export function cochraneTerm(term:string):string{
 const expressao=String(term||'').trim();
 if(!expressao)throw new Error('Informe uma expressão de busca.');
 return '('+expressao+') AND '+CDSR;
}
