import {readFile} from 'node:fs/promises';import {stripTypeScriptTypes} from 'node:module';import assert from 'node:assert/strict';
const cache=new Map();
const load=async p=>{
 if(cache.has(p))return cache.get(p);
 let src=stripTypeScriptTypes(await readFile(p,'utf8'));
 for(const m of [...src.matchAll(/from\s*'(\.\/[^']+)'/g)]){
  const alvo=p.replace(/[^/]+$/,'')+m[1].slice(2)+'.ts';
  src=src.replace(m[0],"from'"+(await load(alvo)).__url+"'");
 }
 const u='data:text/javascript;base64,'+Buffer.from(src).toString('base64');
 const mod={...await import(u),__url:u};cache.set(p,mod);return mod;
};
const sc=await load('lib/screening.ts');
const ai=await load('lib/ai-analysis.ts');

const protocolo={population:'P',concept:'C',context:'X',inclusion:'I',exclusion:'',questions:['A população é adulta?','Avalia o conceito?'],questionRules:[{},{}],approved:true,version:2};
const achou=(title,abstract,extra={})=>({found:true,title,authors:'Silva',year:'2020',journal:'J',abstract,abstractSource:abstract?'Crossref':'',...extra});
const searches=[
 {doi:'10.1/a',status:'done',result:achou('Alpha trial','Adults with X')},
 {doi:'10.1/b',status:'done',result:achou('Beta study','')},
 {doi:'10.1/c',status:'partial',result:achou('Gamma cohort','Kids')},
 {doi:'10.1/d',status:'done',result:{found:false}},
 {doi:'10.1/e',status:'waiting',result:null},
 {doi:'10.1/f',status:'error',result:achou('Erro','x')},
];
const linha=(doi,decision,version,extra={})=>({doi,decision,answers:[],reasons:[],reason:'',actor:'u1',version,source:'',ai:null,...extra});
const linhas=[linha('10.1/a','incluir',2),linha('10.1/c','excluir',1)];

// ---------------------------------------------------------------------------
// Registros
// ---------------------------------------------------------------------------
assert.equal(sc.registroDe(searches[3]),null,'consulta sem metadados não é registro');
assert.equal(sc.registroDe(searches[4]),null,'consulta pendente não é registro');
assert.equal(sc.registroDe(searches[5]),null,'consulta com erro não é registro');
const regA=sc.registroDe(searches[0]);
assert.deepEqual(regA,{doi:'10.1/a',title:'Alpha trial',authors:'Silva',year:'2020',journal:'J',abstract:'Adults with X',abstractSource:'Crossref'});
assert.equal(sc.registroHash(regA),ai.articleHash(sc.pseudoArtigo(regA)));

// ---------------------------------------------------------------------------
// Situação, listagem, filtros e paginação
// ---------------------------------------------------------------------------
assert.equal(sc.situacao(linhas[0],2),'incluir');
assert.equal(sc.situacao(linhas[1],2),'pendente','decisão de versão anterior conta como pendente');
assert.equal(sc.situacao(undefined,2),'pendente');
let l=sc.listar(searches,linhas,protocolo,{filtro:'todos'});
assert.equal(l.total,3);
assert.deepEqual(l.contagens,{todos:3,pendente:2,incluir:1,excluir:0,sem_resumo:1,nao_obtido:0});
l=sc.listar(searches,linhas,protocolo,{filtro:'pendente'});
assert.deepEqual(l.itens.map(x=>x.registro.doi),['10.1/b','10.1/c']);
assert.equal(l.itens[1].antiga,true,'mostra que há decisão de versão anterior');
assert.equal(l.itens[0].antiga,false);
assert.deepEqual(sc.listar(searches,linhas,protocolo,{filtro:'sem_resumo'}).itens.map(x=>x.registro.doi),['10.1/b']);
assert.deepEqual(sc.listar(searches,linhas,protocolo,{filtro:'todos',busca:'GAMMA'}).itens.map(x=>x.registro.doi),['10.1/c']);
const muitos=Array.from({length:120},(_,i)=>({doi:'10.9/'+i,status:'done',result:achou('T'+i,'r')}));
l=sc.listar(muitos,[],protocolo,{filtro:'todos',pagina:3});
assert.equal(l.paginas,3);assert.equal(l.itens.length,20);assert.equal(l.itens[0].registro.doi,'10.9/100');
assert.equal(sc.listar(muitos,[],protocolo,{filtro:'todos',pagina:99}).itens.length,20,'página além do fim volta à última');

// ---------------------------------------------------------------------------
// Decisão humana: as mesmas regras da triagem de hoje
// ---------------------------------------------------------------------------
const d=sc.decidir(protocolo,['Sim','Sim'],'',['',''],'u1');
assert.deepEqual({decision:d.decision,version:d.version,actor:d.actor},{decision:'incluir',version:2,actor:'u1'});
assert.throws(()=>sc.decidir(protocolo,['Não','Sim'],'',[],'u1'),/Justifique/);
assert.equal(sc.decidir(protocolo,['Não','Sim'],'Não é adulta',[],'u1').decision,'excluir');
assert.throws(()=>sc.decidir(protocolo,['Sim'],'',[],'u1'),/Responda todas/);
assert.throws(()=>sc.decidir({...protocolo,approved:false},['Sim','Sim'],'',[],'u1'),/Aprove o protocolo/);
assert.equal(sc.decidir({...protocolo,questionRules:[{noDecision:'include'},{}]},['Não','Sim'],'',[],'u1').decision,'incluir','regra por pergunta vale');

// ---------------------------------------------------------------------------
// Pacote de IA: mesmo formato da triagem do corpus
// ---------------------------------------------------------------------------
const regB=sc.registroDe(searches[1]);
const pkg=sc.pacoteIA('p1',protocolo,[regA,regB]);
assert.equal(pkg.schema,'ORBIS_AI_PACKAGE_MD_V1');assert.equal(pkg.etapa,'triagem');
assert.equal(pkg.criteria_hash,ai.criteriaHash(protocolo,'triagem'));
assert.deepEqual(pkg.items.map(x=>[x.article_id,x.arquivo,x.resumo_status]),[['10.1/a','10.1/a','disponivel'],['10.1/b','10.1/b','nao_localizado']]);
assert.equal(pkg.items[0].source_hash,sc.registroHash(regA));
assert.equal(pkg.items[0].abstract_original,'Adults with X');

// ---------------------------------------------------------------------------
// Sugestão da IA
// ---------------------------------------------------------------------------
const item={article_id:'10.1/a',triagem_nivel1:[{pergunta:1,resposta:'sim',motivo:'adultos',evidencia:'Adults'},{pergunta:2,resposta:'nao',motivo:'outro conceito',evidencia:''}]};
const s=sc.sugestaoDe(item,protocolo,regA,'Local','qwen3:14b','2026-09-30T10:00:00Z');
assert.equal(s.decision,'excluir','"Não" leva à ação de exclusão padrão');
assert.deepEqual(s.questions.map(q=>q.answer),['sim','nao']);
assert.deepEqual([s.provider,s.model,s.version],['Local','qwen3:14b',2]);
assert.equal(s.sourceHash,sc.registroHash(regA));
assert.throws(()=>sc.sugestaoDe({triagem_nivel1:[{pergunta:1,resposta:'sim'}]},protocolo,regA,'Local','m','x'),/Quantidade/);
assert.equal(sc.sugestaoAtual(s,regA,protocolo),true);
assert.equal(sc.sugestaoAtual(s,{...regA,abstract:'Resumo atualizado'},protocolo),false,'resumo mudou depois da sugestão');
assert.equal(sc.sugestaoAtual(s,regA,{...protocolo,version:3}),false,'protocolo mudou');
assert.equal(sc.sugestaoAtual(null,regA,protocolo),false);
const aceita=sc.aceitar(s,protocolo,'u2');
assert.deepEqual([aceita.decision,aceita.answers,aceita.source,aceita.actor],['excluir',['Sim','Não'],'ia_accepted','u2']);
assert.match(aceita.reason,/Decisão aceita da análise Local \/ qwen3:14b/);assert.match(aceita.reason,/outro conceito/);

// ---------------------------------------------------------------------------
// Do registro ao corpus
// ---------------------------------------------------------------------------
const t=sc.triagemParaArtigo(linha('10.1/a','incluir',2,{answers:['Sim','Sim'],reason:'ok'}));
assert.deepEqual(t,{answers:['Sim','Sim'],reasons:[],reason:'ok',decision:'incluir',actor:'u1',version:2,source:'triagem_pre_download'});
assert.equal(sc.triagemParaArtigo(linha('10.1/a','incluir',2,{source:'ia_accepted'})).source,'ia_accepted');
const paraBaixar=sc.incluidosParaBaixar([linha('10.1/a','incluir',2),linha('10.1/x','incluir',2),linha('10.1/c','excluir',2),linha('10.1/g','incluir',1)],2,[{doi:'10.1/x'}]);
assert.deepEqual(paraBaixar,['10.1/a'],'só incluídos na versão atual e ainda fora do corpus');

// ---------------------------------------------------------------------------
// PRISMA
// ---------------------------------------------------------------------------
const local={doi:'',source:{kind:'local'},triage:{version:2,decision:'incluir'}};
assert.deepEqual(sc.contagensPrisma(searches,linhas,[{doi:'10.1/a'},local],2),
 {identificados:3,triados:1,excluidosTriagem:0,incluidosTriagem:1,pendentesTriagem:2,buscados:1,obtidos:1,naoObtidos:0,locais:1});
assert.equal(sc.contagensPrisma(searches,linhas,[],2).naoObtidos,1);
// Projeto de antes desta etapa: triagem feita no corpus, sem linha em `screening`
// (e às vezes sem o DOI no lote). O fluxograma aproveita essa triagem.
assert.deepEqual(sc.contagensPrisma(searches,linhas,[{doi:'10.1/b',triage:{version:2,decision:'excluir'}},{doi:'10.1/z',triage:{version:2,decision:'incluir'}}],2),
 {identificados:4,triados:3,excluidosTriagem:1,incluidosTriagem:2,pendentesTriagem:1,buscados:2,obtidos:1,naoObtidos:1,locais:0});
// ---------------------------------------------------------------------------
// Incluído que não baixou: "PDF não obtido", com o motivo — na lista, na ficha
// e num filtro próprio, para a pessoa não ter de caçar no Artigo Aberto.
// ---------------------------------------------------------------------------
{
 const s2=[{...searches[0],error:'Nenhuma fonte entregou o PDF.'},
  {doi:'10.1/g',status:'done',result:achou('Gama','g',{motor:{ok:false,erro:'A editora bloqueia o download (HTTP 403).'}})},
  {doi:'10.1/h',status:'done',result:achou('Eta','h')},
  {doi:'10.1/i',status:'done',result:achou('Iota','i')}];
 const ls=['10.1/a','10.1/g','10.1/h','10.1/i'].map(d=>linha(d,'incluir',2));
 const arts=[{doi:'10.1/I'}];
 assert.deepEqual(sc.obtencao(s2[0],arts),{estado:'nao_obtido',motivo:'Nenhuma fonte entregou o PDF.'});
 assert.deepEqual(sc.obtencao(s2[1],arts),{estado:'nao_obtido',motivo:'A editora bloqueia o download (HTTP 403).'});
 assert.deepEqual(sc.obtencao(s2[2],arts),{estado:'aguardando',motivo:''},'ainda não tentado');
 assert.deepEqual(sc.obtencao(s2[3],arts),{estado:'no_corpus',motivo:''},'DOI sem diferença de caixa');
 const r=sc.listar(s2,ls,protocolo,{filtro:'nao_obtido',articles:arts});
 assert.deepEqual(r.itens.map(x=>x.registro.doi),['10.1/a','10.1/g']);
 assert.equal(r.contagens.nao_obtido,2);
 assert.equal(r.itens[1].obtencao.motivo,'A editora bloqueia o download (HTTP 403).');
 assert.equal(sc.listar(s2,ls,protocolo,{filtro:'todos',articles:arts}).itens.find(x=>x.registro.doi==='10.1/h').obtencao.estado,'aguardando');
 assert.equal(sc.listar(searches,linhas,protocolo,{filtro:'todos'}).itens.find(x=>x.registro.doi==='10.1/b').obtencao,null,'pendente não tem obtenção');
}
console.log('screening: ok');
