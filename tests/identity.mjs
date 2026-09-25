import {readFile} from 'node:fs/promises';import {stripTypeScriptTypes} from 'node:module';import assert from 'node:assert/strict';
const url=s=>'data:text/javascript;base64,'+Buffer.from(s).toString('base64');
const load=async p=>import(url(stripTypeScriptTypes(await readFile(p,'utf8'))));
const id=await load('lib/identity.ts');

// ---------------------------------------------------------------------------
// Semelhança de título
//
// Índices bibliográficos escrevem o mesmo título de formas diferentes: com e
// sem subtítulo, com hífen ou espaço, em caixa alta, com pontuação trocada.
// Tolerar isso é necessário; tolerar demais faria passar outro artigo.
// ---------------------------------------------------------------------------
assert.equal(id.titleSimilarity('Autism and Epilepsy','Autism and Epilepsy'),1,'idênticos');
assert.ok(id.titleSimilarity('Upside-down protein crystallization','Upside Down Protein Crystallization')>0.95,
 'hífen contra espaço e caixa diferente é o mesmo título');
// Medido: o subtítulo omitido (mesmo artigo) dá 0.752 e o artigo diferente dá
// 0.905 — o errado pontua mais alto que o certo. Os dois são a mesma relação
// de subconjunto, então nenhum limiar os separa. O teste registra esse fato em
// vez de fingir que o algoritmo resolve; quem resolve é o DOI.
assert.ok(id.titleSimilarity('Autism spectrum disorder: neuropathology','Autism spectrum disorder')>0.7);
assert.ok(id.titleSimilarity('Membrane protein crystallization','Biomolecular membrane protein crystallization')>0.85,
 'documenta a limitação: título sozinho não distingue este par');
assert.ok(id.titleSimilarity('Autism and Epilepsy','Gut microbiota in mice')<0.3,'assuntos diferentes');
assert.equal(id.titleSimilarity('',''),0,'vazio não casa com vazio');
assert.equal(id.titleSimilarity('Algo',''),0);

// ---------------------------------------------------------------------------
// O veredito
//
// A regra que organiza tudo: o DOI conferido na página é evidência forte; o
// resto é indício. E "não deu para verificar" nunca pode virar "confirmado".
// ---------------------------------------------------------------------------
const artigo={doi:'10.1000/abc',title:'Autism and Epilepsy',authors:'Souza A; Lima B',year:'2021'};

let v=id.checkIdentity(artigo,{pageDoi:'10.1000/abc'});
assert.equal(v.ok,true);assert.equal(v.method,'doi_na_pagina');assert.equal(v.score,1);

v=id.checkIdentity(artigo,{pageDoi:'10.9999/outro'});
assert.equal(v.ok,false,'DOI divergente reprova mesmo com o resto batendo');
assert.equal(v.method,'doi_divergente');

v=id.checkIdentity(artigo,{pageTitle:'Autism and Epilepsy',pageYear:'2021'});
assert.equal(v.ok,true);assert.equal(v.method,'titulo_e_ano');

v=id.checkIdentity(artigo,{pageTitle:'Autism and Epilepsy'});
assert.equal(v.ok,true);assert.equal(v.method,'titulo');
assert.ok(v.score<1,'só o título vale menos que o DOI conferido');

v=id.checkIdentity(artigo,{pageTitle:'Gut microbiota in mice'});
assert.equal(v.ok,false,'outro título reprova');
assert.equal(v.method,'titulo_divergente');

// Consequência do limiar alto: parecido-mas-não-idêntico fica pendente, não
// aprovado. É a direção segura do erro.
v=id.checkIdentity({doi:'10.1/x',title:'Membrane protein crystallization'},
                   {pageTitle:'Biomolecular membrane protein crystallization'});
assert.equal(v.ok,false,'título parecido de outro artigo não entra no corpus');
assert.equal(v.method,'titulo_insuficiente');

v=id.checkIdentity(artigo,{pageTitle:'Autism and Epilepsy',pageYear:'1998'});
assert.equal(v.ok,false,'título igual e ano muito distante é suspeito');

v=id.checkIdentity(artigo,{pageTitle:'Autism and Epilepsy',pageYear:'2022'});
assert.equal(v.ok,true,'um ano de diferença é normal (online antes do fascículo)');

// Sem nenhuma evidência, o veredito é "não verificado" — e não passa.
v=id.checkIdentity(artigo,{});
assert.equal(v.ok,false);assert.equal(v.method,'sem_evidencia');

// ---------------------------------------------------------------------------
// O que NÃO é o artigo
//
// Os quatro casos abaixo vieram de uma auditoria real: 449 PDFs baixados, 4
// tinham metadados corretos e não eram o artigo.
// ---------------------------------------------------------------------------
assert.equal(id.looksLikeArticle('SUPPLEMENTARY TABLE 1 Summary of genetically modified models\nGene\nBrain').ok,false,
 'material suplementar não é o artigo');
assert.equal(id.looksLikeArticle('nature research | life sciences reporting summary\nCorresponding author(s): X').ok,false,
 'formulário editorial da Nature não é o artigo');
assert.equal(id.looksLikeArticle('ABSTRACTS BY NUMBER\n1. Resumo um\n2. Resumo dois\n3. Resumo tres').ok,false,
 'caderno de resumos não é o artigo');
assert.equal(id.looksLikeArticle('').ok,false,'PDF sem texto não pode ser conferido');
assert.equal(id.looksLikeArticle('   ').ok,false);

const artigoReal='Autism and Epilepsy\nSouza A, Lima B\nDepartment of Neurology\nAbstract\n'+'texto do corpo '.repeat(40);
assert.equal(id.looksLikeArticle(artigoReal).ok,true,'artigo de verdade passa');

// Falso positivo real encontrado na auditoria: a Frontiers in Bioscience
// imprime "TABLE OF CONTENTS" dentro do próprio artigo.
assert.equal(id.looksLikeArticle(
 '[Frontiers in Bioscience 6, d936-943]\nTHE ASSOCIATION OF MHC GENES WITH AUTISM\nTorres A\nTABLE OF CONTENTS\n1. Abstract\n2. Introduction\n'+'corpo do artigo '.repeat(30)
).ok,true,'sumário dentro do artigo não reprova o artigo');

// E um artigo que cita o próprio suplemento continua sendo o artigo.
assert.equal(id.looksLikeArticle(
 'TREM2 Is Required for Synapse Elimination\nFilipello\nAbstract\n'+'corpo '.repeat(40)+'\nas shown in Supplementary Table 2.'
).ok,true,'citar suplemento no corpo não reprova');

console.log('identity: ok');
