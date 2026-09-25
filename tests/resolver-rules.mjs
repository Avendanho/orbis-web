import {readFile} from 'node:fs/promises';import {stripTypeScriptTypes} from 'node:module';import assert from 'node:assert/strict';
const url=s=>'data:text/javascript;base64,'+Buffer.from(s).toString('base64');
const load=async p=>import(url(stripTypeScriptTypes(await readFile(p,'utf8'))));
const rk=await load('lib/record-kind.ts');
const s2=await load('lib/sources/semantic-scholar.ts');

// ---------------------------------------------------------------------------
// 2. Registros do CENTRAL não são DOIs: nenhuma consulta vai achá-los.
// ---------------------------------------------------------------------------
assert.equal(rk.isCentralRecord('10.1002/central/cn-01612144'),true);
assert.equal(rk.isCentralRecord('10.1002/central/CN-00555518'),true,'caixa alta, como a Cochrane exporta');
assert.equal(rk.isCentralRecord('10.1002/14651858.cd003488.pub3'),false,'revisão Cochrane (CDSR) é DOI de verdade');
assert.equal(rk.CENTRAL_REASON.reasonCode,'central_record');
assert.match(rk.CENTRAL_REASON.reasonDetail,/DOI ou o PMID/);

// ---------------------------------------------------------------------------
// 3. O que não é artigo completo — casos reais do lote que ficou pendente.
// ---------------------------------------------------------------------------
assert.equal(rk.recordKind({dcType:'Dataset',title:'Decoding the peripheral transcriptomic…'})?.kind,'dataset','figshare');
assert.equal(rk.recordKind({oaType:'dataset'})?.kind,'dataset');
assert.equal(rk.recordKind({crType:'journal-article',page:'S104-S176',title:'ABSTRACTS FOR SYMPOSIA'})?.kind,'abstract','livro de resumos');
assert.equal(rk.recordKind({crType:'journal-article',page:'S267',title:'6.4 Intestinal Inflammation, the Microbiome, and Human Neuropsychiatric Disorders'})?.kind,'abstract','resumo de congresso em suplemento');
assert.equal(rk.recordKind({oaType:'conference-abstract'})?.kind,'abstract');
assert.equal(rk.recordKind({crType:'journal-article',page:'495-495',title:'From the editors'})?.kind,'editorial');
assert.equal(rk.recordKind({oaType:'erratum',title:'Correction'})?.kind,'editorial');
// Não pode acusar artigo de verdade.
assert.equal(rk.recordKind({crType:'journal-article',page:'1-12',title:'Genomic landscape of rare variants in a Chinese autism cohort'}),null);
assert.equal(rk.recordKind({crType:'journal-article',page:'S12-S20',title:'A randomized trial of probiotics'}),null,'artigo curto em suplemento (9 págs.) continua artigo');
assert.equal(rk.recordKind({crType:'journal-article',title:'Editorial board decisions in peer review: a cohort study'}),null,'"editorial" no meio do título não basta');
assert.equal(rk.recordKind({}),null);

// ---------------------------------------------------------------------------
// 1 e 4. Gravidade dos problemas das fontes.
// ---------------------------------------------------------------------------
const g=rk.splitIssues(['Semantic Scholar: limite temporário de consultas','Editora: bloqueio de acesso automático','Europe PMC: tempo limite excedido']);
assert.deepEqual(g.secundarias,['Semantic Scholar: limite temporário de consultas']);
assert.deepEqual(g.bloqueios,['Editora: bloqueio de acesso automático']);
assert.deepEqual(g.criticas,['Europe PMC: tempo limite excedido']);

// O motivo mostrado: PDF achado > não é artigo > site bloqueado > regra de sempre.
const base={reasonCode:'search_incomplete',reason:'Busca incompleta',reasonDetail:'…'};
const kind=rk.recordKind({dcType:'Dataset'});
assert.equal(rk.escolherMotivo({pdf:true,kind,bloqueios:[],criticas:[],oa:false,base:{reasonCode:'pdf_found',reason:'',reasonDetail:''}}).reasonCode,'pdf_found','achou PDF: nada muda');
assert.equal(rk.escolherMotivo({pdf:false,kind,bloqueios:[],criticas:[],oa:true,base}).reasonCode,'not_full_article');
const b=rk.escolherMotivo({pdf:false,kind:null,bloqueios:['Editora: bloqueio de acesso automático'],criticas:[],oa:true,base});
assert.equal(b.reasonCode,'publisher_blocked');
assert.match(b.reasonDetail,/gratuito/,'se uma fonte diz que é gratuito, a mensagem conta');
assert.equal(rk.escolherMotivo({pdf:false,kind:null,bloqueios:['Editora: bloqueio de acesso automático'],criticas:['Europe PMC: tempo limite excedido'],oa:false,base}).reasonCode,'search_incomplete','com falha crítica, a busca continua incompleta');

// Semantic Scholar fora e nenhum PDF: não fica pendente, mas a mensagem diz
// que ele pode ter o que faltou e como tentar de novo.
const semPdf={reasonCode:'pdf_not_found',reason:'PDF gratuito não localizado',reasonDetail:'Nenhuma fonte consultada forneceu um PDF gratuito.'};
const s=rk.escolherMotivo({pdf:false,kind:null,bloqueios:[],criticas:[],secundarias:['Semantic Scholar: limite temporário de consultas'],oa:false,base:semPdf});
assert.equal(s.reasonCode,'pdf_not_found');
assert.match(s.reasonDetail,/^Nenhuma fonte.*Semantic Scholar não respondeu.*Retomar/);
assert.equal(rk.escolherMotivo({pdf:true,kind:null,bloqueios:[],criticas:[],secundarias:['Semantic Scholar: x'],oa:false,base:{reasonCode:'pdf_found',reason:'',reasonDetail:'Há fontes.'}}).reasonDetail,'Há fontes.','com PDF, a nota não aparece');

// Pendente só quando uma fonte que importa falhou.
assert.equal(rk.isPartial({criticas:[]}),false,'Semantic Scholar sozinho não segura o artigo');
assert.equal(rk.isPartial({criticas:['Crossref: indisponível (HTTP 503)']}),true);

// ---------------------------------------------------------------------------
// 1. Semantic Scholar: nova tentativa, lote e chave.
// ---------------------------------------------------------------------------
function falsoFetch(respostas){const chamadas=[];const f=async(u,init)=>{chamadas.push({u:String(u),init});const r=respostas.shift();return typeof r==='function'?r(u,init):r;};f.chamadas=chamadas;return f;}
const json=(d,status=200,h={})=>new Response(JSON.stringify(d),{status,headers:{'content-type':'application/json',...h}});
const esperas=[];const sleep=async ms=>{esperas.push(ms)};

// Cada caso começa sem cache: o mesmo DOI é reaproveitado entre eles.
const sem=()=>s2.limparCacheS2();

// 429 duas vezes e depois sucesso: tenta de novo, respeitando Retry-After.
sem();let f=falsoFetch([json({},429,{'retry-after':'2'}),json({},429),json({title:'T',isOpenAccess:true})]);
let r=await s2.fetchS2Paper('10.1/x',{fetch:f,sleep});
assert.equal(r.data.title,'T');assert.equal(f.chamadas.length,3);
assert.equal(esperas[0],2000,'Retry-After em segundos');assert.ok(esperas[1]>=1000&&esperas[1]<=4000,'depois, espera crescente');

// Desiste depois de 3 tentativas e diz por quê.
sem();esperas.length=0;f=falsoFetch([json({},429),json({},429),json({},429)]);
r=await s2.fetchS2Paper('10.1/x',{fetch:f,sleep});
assert.equal(r.data,null);assert.equal(r.issue,'Semantic Scholar: limite temporário de consultas');assert.equal(f.chamadas.length,3);

// 404 não é falha: o artigo só não está lá.
sem();f=falsoFetch([json({error:'Paper not found'},404)]);
r=await s2.fetchS2Paper('10.1/x',{fetch:f,sleep});assert.deepEqual(r,{data:null});

// Retry-After absurdo não trava a consulta.
sem();esperas.length=0;f=falsoFetch([json({},429,{'retry-after':'3600'}),json({title:'T'})]);
await s2.fetchS2Paper('10.1/x',{fetch:f,sleep});assert.ok(esperas[0]<=10000);

// Chave vai no cabeçalho x-api-key, nunca na URL.
sem();f=falsoFetch([json({title:'T'})]);
await s2.fetchS2Paper('10.1/x',{fetch:f,sleep,apiKey:'k-s2'});
assert.equal(new Headers(f.chamadas[0].init.headers).get('x-api-key'),'k-s2');assert.ok(!f.chamadas[0].u.includes('k-s2'));

// Lote: uma chamada para muitos DOIs; resposta na mesma ordem, null = não achado.
s2.limparCacheS2();
f=falsoFetch([async(u,init)=>{const ids=JSON.parse(init.body).ids;return json(ids.map((id,i)=>i===1?null:{title:'P'+i}));}]);
const n=await s2.prefetchS2(['10.1/A','10.1/b','10.1/c'],{fetch:f,sleep});
assert.equal(n,3);assert.equal(f.chamadas.length,1);assert.equal(f.chamadas[0].init.method,'POST');
assert.deepEqual(JSON.parse(f.chamadas[0].init.body).ids,['DOI:10.1/A','DOI:10.1/b','DOI:10.1/c']);
assert.deepEqual(s2.cachedS2('10.1/a'),{data:{title:'P0'}},'cache ignora caixa do DOI');
assert.deepEqual(s2.cachedS2('10.1/b'),{data:null},'não achado também fica no cache');
assert.equal(s2.cachedS2('10.1/z'),undefined);

// Com cache, a consulta individual não sai para a rede.
f=falsoFetch([]);r=await s2.fetchS2Paper('10.1/c',{fetch:f,sleep});assert.equal(r.data.title,'P2');assert.equal(f.chamadas.length,0);

// Lote grande vai em pedaços de 500.
s2.limparCacheS2();
f=falsoFetch([async(u,init)=>json(JSON.parse(init.body).ids.map(()=>null)),async(u,init)=>json(JSON.parse(init.body).ids.map(()=>null))]);
await s2.prefetchS2(Array.from({length:501},(_,i)=>'10.1/'+i),{fetch:f,sleep});
assert.deepEqual(f.chamadas.map(c=>JSON.parse(c.init.body).ids.length),[500,1]);

// O lote é um pedido que vale por centenas: insiste mais que a consulta
// individual (5 tentativas, esperas maiores) antes de desistir.
s2.limparCacheS2();esperas.length=0;
f=falsoFetch([json({},429),json({},429),json({},429),json({},429),async(u,init)=>json([{title:'Q'}])]);
assert.equal(await s2.prefetchS2(['10.1/q'],{fetch:f,sleep}),1);assert.equal(f.chamadas.length,5);
assert.ok(esperas.every(ms=>ms>=2000),'esperas do lote começam em 2 s');

// Lote que falha não quebra nada: sem cache, cai na consulta individual.
s2.limparCacheS2();
f=falsoFetch(Array.from({length:5},()=>json({},429)));
assert.equal(await s2.prefetchS2(['10.1/q'],{fetch:f,sleep}),0);assert.equal(s2.cachedS2('10.1/q'),undefined);

console.log('resolver-rules: ok');
