import {readFile} from 'node:fs/promises';import {stripTypeScriptTypes} from 'node:module';import assert from 'node:assert/strict';
const prov=await import('data:text/javascript;base64,'+Buffer.from(stripTypeScriptTypes(await readFile('lib/ai-provider.ts','utf8'))).toString('base64'));

// Um fetch de mentira que guarda o pedido e responde como o Ollama.
function falso(responder){const pedidos=[];const f=async(url,init={})=>{pedidos.push({url:String(url),init,body:init.body?JSON.parse(init.body):null});return responder(String(url),init)};f.pedidos=pedidos;return f}
const json=(x,status=200)=>new Response(JSON.stringify(x),{status,headers:{'content-type':'application/json'}});

// ---------------------------------------------------------------------------
// Chamada ao Ollama
// ---------------------------------------------------------------------------
let f=falso(()=>json({message:{role:'assistant',content:'{"items":[]}'},done:true}));
let p=prov.ollamaProvider({url:'http://ia.local:11434/',modelo:'qwen3:14b',contexto:16384,prazo:600},f);
assert.equal(p.name,'Ollama');assert.equal(p.model,'qwen3:14b');assert.equal(p.local,true);
assert.equal(await p.complete('SISTEMA','USUARIO'),'{"items":[]}');
const pedido=f.pedidos[0];
assert.equal(pedido.url,'http://ia.local:11434/api/chat','barra final do endereço não duplica');
assert.equal(pedido.init.method,'POST');
assert.deepEqual(pedido.body,{model:'qwen3:14b',stream:false,format:'json',think:false,options:{temperature:0,num_ctx:16384},
 messages:[{role:'system',content:'SISTEMA'},{role:'user',content:'USUARIO'}]},'corpo da chamada');

// O texto completo precisa caber no contexto do modelo local.
assert.equal(p.limiteTexto,(16384-6000)*3);
assert.equal(prov.ollamaProvider({contexto:4096},f).limiteTexto,8000,'contexto pequeno tem piso');
assert.equal(prov.ollamaProvider({contexto:131072},f).limiteTexto,60000,'nunca passa do limite geral');

// Erros do Ollama viram falha técnica, sem repetir o que não melhora.
const semDormir=async()=>{};
f=falso(()=>json({error:'model "qwen9" not found, try pulling it first'},404));
p=prov.ollamaProvider({modelo:'qwen9'},f);
await assert.rejects(prov.callWithRetry(()=>p.complete('s','u'),4,semDormir),e=>e instanceof prov.LlmCallFailed&&/Ollama HTTP 404/.test(e.message)&&/not found/.test(e.message));
assert.equal(f.pedidos.length,1,'modelo inexistente não é repetido');

f=falso(()=>json({error:'unexpected server error'}));
await assert.rejects(prov.ollamaProvider({},f).complete('s','u'),/Ollama: unexpected server error/,'erro no corpo de um 200');

f=falso(()=>{throw Object.assign(new Error('The operation was aborted due to timeout'),{name:'TimeoutError'})});
p=prov.ollamaProvider({prazo:30},f);
await assert.rejects(prov.callWithRetry(()=>p.complete('s','u'),4,semDormir),e=>e instanceof prov.LlmCallFailed&&/não respondeu em 30 s/.test(e.message));
assert.equal(f.pedidos.length,1,'prazo estourado não é repetido: seria o mesmo prazo de novo');

f=falso(()=>{throw new TypeError('fetch failed')});
await assert.rejects(prov.ollamaProvider({},f).complete('s','u'),/Ollama fora do ar/);

// ---------------------------------------------------------------------------
// Modelos instalados
// ---------------------------------------------------------------------------
f=falso(()=>json({models:[{name:'qwen3:14b',size:1},{name:'llama3:8b'}]}));
assert.deepEqual(await prov.listLocalModels('http://x:11434',f),['qwen3:14b','llama3:8b']);
assert.equal(f.pedidos[0].url,'http://x:11434/api/tags');
assert.equal(await prov.listLocalModels('http://x:11434',falso(()=>{throw new TypeError('fetch failed')})),null,'fora do ar');
assert.equal(await prov.listLocalModels('http://x:11434',falso(()=>json({},500))),null);
assert.equal(await prov.ollamaOnline('http://x',falso(()=>json({models:[]}))),true);
assert.equal(await prov.ollamaOnline('http://x',falso(()=>{throw new TypeError('fetch failed')})),false);

// ---------------------------------------------------------------------------
// Configuração do Ollama a partir das chaves
// ---------------------------------------------------------------------------
assert.deepEqual(prov.configOllama({}),{url:'http://localhost:11434',modelo:'qwen3:14b',contexto:16384,prazo:600});
assert.deepEqual(prov.configOllama({OLLAMA_URL:'http://gpu:1',OLLAMA_MODELO:'qwen3:8b',OLLAMA_CONTEXTO:'32768',OLLAMA_PRAZO:'120'}),{url:'http://gpu:1',modelo:'qwen3:8b',contexto:32768,prazo:120});
assert.deepEqual(prov.configOllama({OLLAMA_CONTEXTO:'abc',OLLAMA_PRAZO:'99999'}),{url:'http://localhost:11434',modelo:'qwen3:14b',contexto:16384,prazo:3600},'inválido cai no padrão; fora da faixa é limitado');

// ---------------------------------------------------------------------------
// Escolha do provedor
// ---------------------------------------------------------------------------
const no=async()=>true,fora=async()=>false;
assert.equal((await prov.pickProvider({},{online:no})).name,'Ollama','automático: local quando o Ollama responde');
assert.equal((await prov.pickProvider({OPENAI_API_KEY:'o'},{online:no})).name,'Ollama','local vem antes da nuvem no automático');
assert.equal((await prov.pickProvider({OPENAI_API_KEY:'o',GEMINI_API_KEY:'g'},{online:fora})).name,'OpenAI','sem Ollama, a ordem de hoje');
assert.equal((await prov.pickProvider({ANTHROPIC_API_KEY:'a',OPENAI_API_KEY:'o'},{online:fora})).name,'Anthropic');
assert.equal(await prov.pickProvider({},{online:fora}),null,'sem Ollama e sem chave, nenhum provedor');
assert.equal((await prov.pickProvider({ORBIS_IA_PROVEDOR:'auto',GEMINI_API_KEY:'g'},{online:fora})).name,'Google','auto = automático');
let perguntou=false;
const local=await prov.pickProvider({ORBIS_IA_PROVEDOR:'local',OLLAMA_MODELO:'qwen3:8b'},{online:async()=>{perguntou=true;return false}});
assert.equal(local.name,'Ollama');assert.equal(local.model,'qwen3:8b');assert.equal(perguntou,false,'local escolhido não depende do teste de conexão');
assert.equal((await prov.pickProvider({ORBIS_IA_PROVEDOR:'gemini',OPENAI_API_KEY:'o',GEMINI_API_KEY:'g'},{online:no})).name,'Google','preferência explícita');
assert.equal(await prov.pickProvider({ORBIS_IA_PROVEDOR:'anthropic',OPENAI_API_KEY:'o'},{online:no}),null,'preferido sem chave não cai em outro às escondidas');
assert.equal((await prov.pickProvider({ORBIS_IA_PROVEDOR:'anthropic',ANTHROPIC_API_KEY:'a',ORBIS_IA_MODELO_ANTHROPIC:'claude-opus-5-5'})).model,'claude-opus-5-5');
assert.equal((await prov.pickProvider({ORBIS_IA_PROVEDOR:'openai',OPENAI_API_KEY:'o',ORBIS_IA_MODELO_OPENAI:''})).model,prov.MODELOS_PADRAO.openai,'modelo vazio usa o padrão');
assert.equal((await prov.pickProvider({ORBIS_IA_PROVEDOR:'anthropic',ANTHROPIC_API_KEY:'a'})).local,undefined,'nuvem não é local');
console.log('ai-provider: ok');
