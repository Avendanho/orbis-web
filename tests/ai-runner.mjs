import {readFile} from 'node:fs/promises';import {stripTypeScriptTypes} from 'node:module';import assert from 'node:assert/strict';
const cache=new Map();
const load=async p=>{
 if(cache.has(p))return cache.get(p);
 let src=stripTypeScriptTypes(await readFile(p,'utf8'));
 // `stripTypeScriptTypes` deixa espaços onde removeu os tipos, então o padrão
 // precisa tolerar espaço antes e depois de `from`.
 for(const m of [...src.matchAll(/from\s*'(\.\/[^']+)'/g)]){
  const alvo=p.replace(/[^/]+$/,'')+m[1].slice(2)+'.ts';
  src=src.replace(m[0],"from'"+(await load(alvo)).__url+"'");
 }
 const u='data:text/javascript;base64,'+Buffer.from(src).toString('base64');
 const mod={...await import(u),__url:u};cache.set(p,mod);return mod;
};
const prov=await load('lib/ai-provider.ts');
const runner=await load('lib/ai-runner.ts');
const ai=await load('lib/ai-analysis.ts');

// ---------------------------------------------------------------------------
// Classificação da falha
//
// A distinção que importa: "não agora" (vale repetir) contra "não" (repetir só
// gasta cota). Errar para o lado de repetir o irrepetível trava a execução.
// ---------------------------------------------------------------------------
for(const m of ['429 rate limit','503 Service Unavailable','500 internal server error',
                'connection reset by peer','request timed out','529 Overloaded'])
 assert.equal(prov.isRetryable(new Error(m)),true,'deveria repetir: '+m);

for(const m of ['401 unauthenticated','403 permission denied','api key not valid',
                'maximum token limit exceeded','blocked by content filter'])
 assert.equal(prov.isRetryable(new Error(m)),false,'não deveria repetir: '+m);

// A espera cresce e tem teto.
const esperas=[0,1,2,3,4,5,6,7].map(i=>prov.backoffDelay(i));
assert.ok(esperas[0]<esperas[3],'a espera cresce');
assert.ok(esperas.every(d=>d<=prov.MAX_DELAY),'a espera respeita o teto');

// ---------------------------------------------------------------------------
// callWithRetry
// ---------------------------------------------------------------------------
const semDormir=async()=>{};

let n=0;
assert.equal(await prov.callWithRetry(async()=>{n++;return 'ok';},4,semDormir),'ok');
assert.equal(n,1,'sucesso de primeira não repete');

n=0;
assert.equal(await prov.callWithRetry(async()=>{n++;if(n<3)throw new Error('429 quota');return 'ok';},4,semDormir),'ok');
assert.equal(n,3,'insiste até dar certo');

n=0;
await assert.rejects(
 prov.callWithRetry(async()=>{n++;throw new Error('401 unauthenticated');},4,semDormir),
 e=>e instanceof prov.LlmCallFailed);
assert.equal(n,1,'não insiste no que não melhora');

await assert.rejects(
 prov.callWithRetry(async()=>{throw new Error('429 quota');},3,semDormir),
 e=>e.name==='LlmCallFailed'&&e.attempts===3&&/429/.test(e.message));

// ---------------------------------------------------------------------------
// Provedor: sem chave, não há triagem automática
// ---------------------------------------------------------------------------
assert.equal(prov.pickProvider({}),null,'sem chave nenhuma, a triagem automática não é oferecida');
assert.equal(prov.pickProvider({OPENAI_API_KEY:'k'}).name,'OpenAI');
assert.equal(prov.pickProvider({GEMINI_API_KEY:'k'}).name,'Google');
assert.equal(prov.pickProvider({ANTHROPIC_API_KEY:'k',OPENAI_API_KEY:'k'}).name,'Anthropic','há ordem de preferência');

// ---------------------------------------------------------------------------
// runAITriage — o ponto do desenho: a saída tem de passar por `importAI`
// ---------------------------------------------------------------------------
assert.deepEqual(runner.chunk([1,2,3,4,5],2),[[1,2],[3,4],[5]]);
assert.deepEqual(runner.chunk([],3),[]);
assert.deepEqual(runner.chunk([1,2],0),[[1],[2]],'lote zero não trava');

const protocol={population:'P',concept:'C',context:'X',questions:['Corresponde à população?','Corresponde ao conceito?'],
 inclusion:'População adequada',exclusion:'Editorial',version:1,approved:true};
const artigos=[
 {id:'a1',filename:'a1.pdf',title:'Artigo Um',authors:'Souza A',doi:'10.1/a',abstract:'Resumo um'},
 {id:'a2',filename:'a2.pdf',title:'Artigo Dois',authors:'Lima B',doi:'10.1/b',abstract:'Resumo dois'},
];
const novoProjeto=()=>({id:'p1',name:'Projeto',documents:[],state:{protocol,articles:structuredClone(artigos),notes:''}});

// Um provedor de mentira que devolve o formato combinado.
const fake=(responder)=>({name:'IA Teste',model:'m1',complete:async(_s,user)=>responder(JSON.parse(user))});
const respondeTudo=fake(p=>JSON.stringify({items:p.artigos.map(a=>({
 ...a,titulo:a.titulo||'T',resumo:'R',
 triagem_nivel1:[{pergunta:1,resposta:'sim',motivo:'P presente'},{pergunta:2,resposta:'sim',motivo:'C presente'}],
}))}));

let projeto=novoProjeto();
let saida=await runner.runAITriage(projeto,'triagem',respondeTudo,{batchSize:1});
assert.equal(saida.analysed,2,'os dois artigos foram avaliados');
assert.equal(saida.failures.length,0);
assert.equal(saida.response.provider,'IA Teste');

// O teste que justifica todo o desenho: a resposta automática entra por
// `importAI` como se tivesse sido colada, e a interface não muda.
const r=ai.importAI(projeto.state,'p1',saida.response,'triagem');
assert.equal(r.applied,2,'importAI aceitou a resposta gerada automaticamente');
assert.equal(projeto.state.articles[0].aiAnalyses[0].provider,'IA Teste');
assert.equal(ai.aiConsensus(projeto.state.articles[0],'triagem',protocol).status,'single');

// E não decide nada sozinha: a triagem humana continua vazia (regra 2).
assert.equal(projeto.state.articles[0].triage,undefined,
 'a IA não aplicou decisão; ficou como rascunho para o pesquisador');

// ---------------------------------------------------------------------------
// Falha técnica não vira parecer
// ---------------------------------------------------------------------------
projeto=novoProjeto();
saida=await runner.runAITriage(projeto,'triagem',fake(()=>{throw new Error('401 unauthenticated');}),{batchSize:1});
assert.equal(saida.response,null,'nada a importar');
assert.equal(saida.analysed,0);
assert.equal(saida.failures.length,2,'cada lote falho fica registrado');
assert.ok(/401|falhou/i.test(saida.failures[0].reason));

// Lote que falha no meio não derruba os que deram certo.
projeto=novoProjeto();
let chamada=0;
saida=await runner.runAITriage(projeto,'triagem',fake(p=>{
 if(++chamada===1)throw new Error('401 unauthenticated');
 return JSON.stringify({items:p.artigos.map(a=>({...a,triagem_nivel1:[{pergunta:1,resposta:'sim',motivo:'ok'},{pergunta:2,resposta:'sim',motivo:'ok'}]}))});
}),{batchSize:1});
assert.equal(saida.analysed,1,'o lote que funcionou foi aproveitado');
assert.equal(saida.failures.length,1,'o que falhou ficou registrado');

// Resposta ilegível é falha técnica, não parecer.
projeto=novoProjeto();
saida=await runner.runAITriage(projeto,'triagem',fake(()=>'desculpe, não posso ajudar'),{batchSize:2});
assert.equal(saida.response,null);
assert.equal(saida.failures.length,1);

// Projeto sem artigos não chama o provedor.
let chamou=false;
saida=await runner.runAITriage({id:'p',name:'x',documents:[],state:{protocol,articles:[],notes:''}},'triagem',
 fake(()=>{chamou=true;return '{}';}));
assert.equal(chamou,false,'sem artigo, não gasta chamada');
assert.equal(saida.analysed,0);

console.log('ai-runner: ok');

// ---------------------------------------------------------------------------
// Texto completo: só na PCC, cortado em TEXT_LIMIT, lotes de 2
// ---------------------------------------------------------------------------
{
 const triado={version:1,decision:'incluir',reason:'',answers:['Sim'],actor:'x'};
 const project={id:'p',documents:[],state:{notes:'',protocol:{population:'P',concept:'C',context:'C',questions:['Q1'],inclusion:'I1',exclusion:'E1',version:1,approved:true},articles:[
  {id:'a',filename:'a.pdf',title:'A',authors:'',doi:'10.1/a',abstract:'R',triage:triado,texto:{key:'k/a'}},
  {id:'b',filename:'b.pdf',title:'B',authors:'',doi:'10.1/b',abstract:'R',triage:triado,texto:{key:'k/b',truncado:false}},
  {id:'c',filename:'c.pdf',title:'C',authors:'',doi:'10.1/c',abstract:'R',triage:triado}]}};
 const longo='x'.repeat(runner.TEXT_LIMIT+10),textos={'k/a':'curto','k/b':longo},lotes=[];
 const provider={name:'fake',model:'m',complete:async(_s,u)=>{lotes.push(JSON.parse(u).artigos);return '{"items":[]}'}};
 await runner.runAITriage(project,'pcc',provider,{readText:async k=>textos[k]??null});
 assert.deepEqual(lotes.map(l=>l.length),[2,1],'PCC em lotes de 2');
 const item=id=>lotes.flat().find(i=>i.article_id===id);
 assert.equal(item('a').texto_completo,'curto');assert.equal(item('a').texto_truncado,false);
 assert.equal(item('b').texto_completo.length,runner.TEXT_LIMIT);assert.equal(item('b').texto_truncado,true);
 assert.equal('texto_completo' in item('c'),false,'sem texto, sem campo');

 lotes.length=0;
 await runner.runAITriage(project,'triagem',provider,{readText:async k=>textos[k]});
 assert.ok(lotes.flat().length&&lotes.flat().every(i=>!('texto_completo' in i)),'triagem não recebe texto');

 lotes.length=0;
 await runner.runAITriage(project,'pcc',provider,{readText:async()=>{throw new Error('R2 fora')}});
 assert.equal(lotes.flat().length,3,'falha ao ler texto não derruba a PCC');
 assert.ok(lotes.flat().every(i=>!('texto_completo' in i)));

 assert.match(ai.makeAIPackage(project,'pcc').instrucoes,/texto_completo/);
}
console.log('ai-runner (texto completo): ok');
