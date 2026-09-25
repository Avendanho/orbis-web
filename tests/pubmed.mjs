import {readFile} from 'node:fs/promises';import {stripTypeScriptTypes} from 'node:module';import assert from 'node:assert/strict';
const url=s=>'data:text/javascript;base64,'+Buffer.from(s).toString('base64');
const load=async p=>import(url(stripTypeScriptTypes(await readFile(p,'utf8'))));
const pm=await load('lib/sources/pubmed-search.ts');

// ---------------------------------------------------------------------------
// Montagem da consulta
//
// O PubMed atende sem chave, mas com um teto baixo de requisições por segundo.
// Com chave o teto sobe; sem ela, a busca continua funcionando.
// ---------------------------------------------------------------------------
const u=new URL(pm.searchUrl('autism AND genetics',{retmax:50}));
assert.equal(u.searchParams.get('db'),'pubmed');
assert.equal(u.searchParams.get('term'),'autism AND genetics','a expressão vai inteira, sem reescrita');
assert.equal(u.searchParams.get('retmax'),'50');
assert.equal(u.searchParams.get('retmode'),'json');
assert.equal(u.searchParams.get('api_key'),null,'sem chave, o parâmetro não aparece');

assert.equal(new URL(pm.searchUrl('x',{apiKey:'K'})).searchParams.get('api_key'),'K','a chave entra quando existe');
assert.equal(new URL(pm.searchUrl('x',{email:'a@b.c'})).searchParams.get('email'),'a@b.c');

// O teto de 500 é o mesmo que o ORBIS já aplica ao lote de DOIs (MAX_DOIS).
assert.equal(new URL(pm.searchUrl('x',{retmax:9999})).searchParams.get('retmax'),'500','o teto é respeitado');
assert.equal(new URL(pm.searchUrl('x',{retmax:0})).searchParams.get('retmax'),'1','pede ao menos um');
assert.throws(()=>pm.searchUrl('   '),/expressão/i,'busca vazia é recusada antes da rede');

// ---------------------------------------------------------------------------
// Leitura das respostas
// ---------------------------------------------------------------------------
assert.deepEqual(pm.parseSearch({esearchresult:{idlist:['1','2','3'],count:'3'}}),{pmids:['1','2','3'],total:3});
assert.deepEqual(pm.parseSearch({esearchresult:{idlist:[],count:'0'}}),{pmids:[],total:0},'nenhum resultado não é erro');
assert.deepEqual(pm.parseSearch({}),{pmids:[],total:0},'resposta malformada não quebra');
assert.deepEqual(pm.parseSearch(null),{pmids:[],total:0});

const resumo={result:{
 uids:['111','222','333'],
 '111':{uid:'111',title:'Artigo com DOI',pubdate:'2021 May',source:'J Test',
        authors:[{name:'Souza A'},{name:'Lima B'}],
        articleids:[{idtype:'pubmed',value:'111'},{idtype:'doi',value:'10.1000/abc'}]},
 '222':{uid:'222',title:'Artigo sem DOI',pubdate:'1998',source:'J Old',authors:[],
        articleids:[{idtype:'pubmed',value:'222'}]},
 '333':{uid:'333',title:'Com DOI em caixa alta',pubdate:'2020 Jan 5',source:'J X',authors:[{name:'Rocha C'}],
        articleids:[{idtype:'doi',value:'10.1000/MAIUSCULA'}]},
}};
const registros=pm.parseSummary(resumo);
assert.equal(registros.length,3,'todo PMID vira registro, com ou sem DOI');
assert.equal(registros[0].doi,'10.1000/abc');
assert.equal(registros[0].year,'2021','o ano sai da data de publicação');
assert.equal(registros[0].authors,'Souza A; Lima B');
assert.equal(registros[1].doi,'','sem DOI o campo fica vazio, e o registro não se perde');
assert.equal(registros[2].doi,'10.1000/maiuscula','DOI é normalizado para minúsculas');

// O que alimenta o lote do ORBIS é só o que tem DOI: é a chave do corpus.
assert.deepEqual(pm.doisFrom(registros),['10.1000/abc','10.1000/maiuscula']);
assert.deepEqual(pm.doisFrom([]),[]);

// Duplicata dentro do próprio resultado não pode inflar o lote.
assert.deepEqual(
 pm.doisFrom([{doi:'10.1/a'},{doi:'10.1/A'},{doi:'10.1/b'}]),
 ['10.1/a','10.1/b'],'DOI repetido em caixas diferentes conta uma vez');

console.log('pubmed: ok');
