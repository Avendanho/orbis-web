import {readFile} from 'node:fs/promises';import {stripTypeScriptTypes} from 'node:module';import assert from 'node:assert/strict';
const src=stripTypeScriptTypes(await readFile('lib/python-bridge.ts','utf8'));
const bridge=await import('data:text/javascript;base64,'+Buffer.from(src).toString('base64'));
const env={ORBIS_ENGINE_URL:'http://motor.test/'},pedido={doi:'10.1/a',projeto:'p',modo:'analisar'};
const chamadas=[];const responder=f=>{globalThis.fetch=async(url,init)=>{chamadas.push({url,init});return f(url,init)}};

// Sem URL configurada: nada é chamado.
responder(()=>{throw new Error('não deveria chamar')});
assert.equal(await bridge.downloadViaEngine({},pedido),null);

// Sucesso: URL sem barra dupla e prazo padrão.
responder(()=>Response.json({ok:true,fonte:'pmc'}));
let r=await bridge.downloadViaEngine(env,pedido);
assert.equal(r.fonte,'pmc');
assert.equal(chamadas.at(-1).url,'http://motor.test/baixar');
assert.equal(JSON.parse(chamadas.at(-1).init.body).prazo,90);

// Motor fora do ar: null, não exceção.
responder(()=>{throw new TypeError('fetch failed')});
assert.equal(await bridge.downloadViaEngine(env,pedido),null);

// Tempo esgotado: resultado do artigo, não "motor fora".
responder(()=>{throw new DOMException('timeout','TimeoutError')});
r=await bridge.downloadViaEngine(env,pedido);
assert.equal(r.ok,false);assert.match(r.erro,/Tempo esgotado/);

// Recusa do motor: exceção com o status e o detalhe do FastAPI.
responder(()=>Response.json({detail:'sem espaço'},{status:507}));
await assert.rejects(bridge.downloadViaEngine(env,pedido),e=>e instanceof bridge.EngineRefused&&e.status===507&&/sem espaço/.test(e.message));

// Status traz a pasta dos PDFs.
responder(()=>Response.json({ok:true,recursos:['download_completo'],faltando:[],pasta_pdfs:'/d/pdfs'}));
const st=await bridge.engineStatus(env);
assert.equal(st.online,true);assert.equal(st.pdfDir,'/d/pdfs');assert.ok(st.resources.includes('download_completo'));
console.log('python-bridge: ok');
