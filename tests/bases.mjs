import {readFile} from 'node:fs/promises';import {stripTypeScriptTypes} from 'node:module';import assert from 'node:assert/strict';
const url=s=>'data:text/javascript;base64,'+Buffer.from(s).toString('base64');
const load=async p=>import(url(stripTypeScriptTypes(await readFile(p,'utf8'))));
const lil=await load('lib/sources/lilacs-search.ts');
const coch=await load('lib/sources/cochrane-search.ts');
const emb=await load('lib/sources/embase-search.ts');
const bases=await load('lib/sources/bases.ts');

// ---------------------------------------------------------------------------
// Registro das bases
// ---------------------------------------------------------------------------
assert.deepEqual(Object.keys(bases.BASES),['pubmed','lilacs','cochrane','embase']);
assert.ok(bases.isBase('lilacs'));
assert.ok(!bases.isBase('toString'),'nome herdado do protótipo não é base');
assert.ok(!bases.isBase(undefined));

// ---------------------------------------------------------------------------
// LILACS
// ---------------------------------------------------------------------------
const u=new URL(lil.searchUrl('dengue AND tw:(tratamento)',{retmax:20}));
assert.equal(u.searchParams.get('q'),'dengue AND tw:(tratamento)','a expressão vai inteira');
assert.equal(u.searchParams.get('fq'),'indexed_database:"LILACS"','o filtro vai entre aspas: sem elas a API troca a caixa e não acha nada');
assert.equal(u.searchParams.get('count'),'20');
assert.equal(new URL(lil.searchUrl('x',{retmax:9999})).searchParams.get('count'),'500');
assert.throws(()=>lil.searchUrl('  '),/expressão/i);
assert.equal(new URL(lil.recordsUrl(['1','2'])).searchParams.get('id__in'),'1,2');

// O firewall da BVS responde 403 a qualquer "(" ou ")" encostado em AND/OR —
// a forma normal de uma estratégia de busca. Grupo com campo (`tw:(`) passa, e
// `tw` é o campo da busca livre: mesmo resultado, verificado contra a API.
assert.equal(lil.blindarGrupos('(depression OR depressão) AND (adolescent*)'),'tw:(depression OR depressão) AND tw:(adolescent*)');
assert.equal(lil.blindarGrupos('((a OR b) AND c) OR d'),'tw:(tw:(a OR b) AND c) OR d','grupos aninhados');
assert.equal(lil.blindarGrupos('mh:(Dengue) AND ti:("x")'),'mh:(Dengue) AND ti:("x")','grupo que já tem campo fica como está');
assert.equal(lil.blindarGrupos('a AND -(b) AND +(c)'),'a AND -tw:(b) AND +tw:(c)','prefixos + e - continuam valendo');
assert.equal(lil.blindarGrupos('"saúde (mental)" AND (x)'),'"saúde (mental)" AND tw:(x)','parêntese dentro de aspas é texto');
assert.equal(lil.blindarGrupos('a \\(b\\) AND (c)'),'a \\(b\\) AND tw:(c)','parêntese escapado é texto');
assert.equal(lil.blindarGrupos('dengue AND tw:(vacina)'),'dengue AND tw:(vacina)','sem grupo solto, nada muda');
assert.equal(new URL(lil.searchUrl('(a) AND (b)')).searchParams.get('q'),'tw:(a) AND tw:(b)','a URL já sai blindada');

assert.deepEqual(lil.parseSearch({diaServerResponse:[{response:{numFound:7,docs:[{django_id:'10'},{django_id:'11'},{}]}}]}),{ids:['10','11'],total:7});
assert.deepEqual(lil.parseSearch({}),{ids:[],total:0},'resposta malformada não quebra');

const regs=lil.parseRecords({objects:[
 {id:10,doi_number:'10.1590/ABC.1',title:[{_i:'pt',text:'Título  original'},{_i:'en',text:'Title'}],individual_author:[{text:'Silva, A'},{text:'Lima, B'}],publication_date_normalized:'20240500',title_serial:'Rev Saúde'},
 {id:11,doi_number:'',title:[{_i:'es',text:'Sin DOI'}],publication_date:'2019'},
 {id:12,doi_number:'https://doi.org/10.1/X',title:[]},
 {id:13,doi_number:'não informado'},
]});
assert.equal(regs[0].doi,'10.1590/abc.1','DOI em minúsculas');
assert.equal(regs[0].title,'Título original','primeiro título, espaços normalizados');
assert.equal(regs[0].authors,'Silva, A; Lima, B');
assert.equal(regs[0].year,'2024');
assert.equal(regs[1].doi,'','sem DOI o registro continua, com o campo vazio');
assert.equal(regs[2].doi,'10.1/x','prefixo doi.org é removido');
assert.equal(regs[3].doi,'','texto que não é DOI não vira DOI');

// ---------------------------------------------------------------------------
// Cochrane: só estreita a expressão ao CDSR, sem reescrevê-la
// ---------------------------------------------------------------------------
assert.equal(coch.cochraneTerm(' a OR b '),'(a OR b) AND "Cochrane Database Syst Rev"[Journal]');
assert.throws(()=>coch.cochraneTerm(''),/expressão/i);

// ---------------------------------------------------------------------------
// Embase
// ---------------------------------------------------------------------------
assert.throws(()=>emb.headers({}),/chave/i,'sem chave falha antes da rede');
assert.equal(emb.headers({apiKey:'K',instToken:'T'})['X-ELS-Insttoken'],'T');
assert.equal(emb.headers({apiKey:'K'})['X-ELS-Insttoken'],undefined);
assert.equal(new URL(emb.searchUrl("'dengue'/exp",100,50)).searchParams.get('start'),'100');

// Formato da família Scopus.
const s1=emb.parseSearch({'search-results':{'opensearch:totalResults':'3',entry:[
 {'dc:title':'A','prism:doi':'10.1016/J.X.1','prism:coverDate':'2021-02-01','prism:publicationName':'Lancet'},
 {'dc:title':'B sem DOI'},
]}});
assert.equal(s1.total,3);
assert.deepEqual(s1.records[0],{doi:'10.1016/j.x.1',title:'A',year:'2021',journal:'Lancet'});
assert.equal(s1.records[1].doi,'');
// Vazio na família Scopus vem como uma entrada de erro, não como lista vazia.
assert.deepEqual(emb.parseSearch({'search-results':{'opensearch:totalResults':'0',entry:[{error:'Result set was empty'}]}}),{records:[],total:0});
// Formato da Embase API: DOI aninhado.
const s2=emb.parseSearch({header:{hits:1},results:[{itemInfo:{itemIdList:{doi:'https://doi.org/10.1016/EMB.2'}},head:{citationTitle:'T',publicationYear:'2020',source:{sourceTitle:'J'}}}]});
assert.deepEqual(s2.records[0],{doi:'10.1016/emb.2',title:'T',year:'2020',journal:'J'});

console.log('bases: ok');
