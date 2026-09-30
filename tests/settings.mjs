import {readFile} from 'node:fs/promises';import {stripTypeScriptTypes} from 'node:module';import assert from 'node:assert/strict';
const url=s=>'data:text/javascript;base64,'+Buffer.from(s).toString('base64');
const load=async p=>import(url(stripTypeScriptTypes(await readFile(p,'utf8'))));
const st=await load('lib/settings.ts');

// ---------------------------------------------------------------------------
// Catálogo
// ---------------------------------------------------------------------------
const chaves=st.CATALOGO.map(i=>i.key);
assert.equal(new Set(chaves).size,chaves.length,'chave repetida no catálogo');
for(const i of st.CATALOGO){
 assert.ok(st.GRUPOS.some(([g])=>g===i.grupo),i.key+' em grupo inexistente');
 if(i.tipo==='select')assert.ok(i.opcoes?.some(([v])=>v===i.padrao),i.key+': padrão fora das opções');
 if(i.tipo==='number')assert.ok(i.min!=null&&i.max!=null&&Number(i.padrao)>=i.min&&Number(i.padrao)<=i.max,i.key+': número sem limites ou padrão fora deles');
}
assert.ok(!chaves.includes('ORBIS_ENGINE_URL')&&!chaves.includes('ORBIS_DATA_DIR'),'o start.py precisa deles antes da tela');
for(const k of ['EZPROXY_BASE_URL','EZPROXY_USER','EZPROXY_PASSWORD','PAPER_FETCH_PROXY','PAPER_FETCH_INSTITUTIONAL','PAPER_FETCH_CLOAK','PAPER_FETCH_BROWSER','ORBIS_SESSAO_NAVEGADOR'])
 assert.ok(!chaves.includes(k),k+': acesso institucional segue só pelo motor/.env');
for(const k of ['OLLAMA_URL','OLLAMA_MODELO','OLLAMA_CONTEXTO','OLLAMA_PRAZO','ORBIS_IA_LOTE','ORBIS_EXTRAIR_MARKDOWN','ORBIS_SALVAR_IMAGENS','ORBIS_EXTRACAO_PRAZO','PAPER_FETCH_NO_SCIHUB'])
 assert.ok(chaves.includes(k),k+' falta no catálogo');
assert.deepEqual(st.GRUPOS.map(([g])=>g),['bases','ia','lote','extracao','fontes']);
assert.ok(st.ALVOS.includes('ollama'));
for(const i of st.CATALOGO.filter(i=>['extracao','fontes'].includes(i.grupo)))assert.notEqual(i.destino,'orbis',i.key+': grupo do motor');

// ---------------------------------------------------------------------------
// Validação
// ---------------------------------------------------------------------------
assert.throws(()=>st.validar('NAO_EXISTE','x'),/desconhecida/);
assert.throws(()=>st.validar('NCBI_EMAIL','a@b.co\nHTTP_PROXY=http://mal'),/quebra de linha/,'injeção de linha no .env');
assert.throws(()=>st.validar('NCBI_API_KEY','abc\rdef'),/quebra de linha/);
assert.throws(()=>st.validar('NCBI_EMAIL','sem-arroba'),/e-mail/);
assert.equal(st.validar('NCBI_EMAIL','  a@b.co '),'a@b.co','espaços nas pontas saem');
assert.equal(st.validar('NCBI_EMAIL',''),'','vazio = apagar');
assert.throws(()=>st.validar('ORBIS_LOTE_SIMULTANEOS','9'),/1 a 4/);
assert.throws(()=>st.validar('ORBIS_LOTE_SIMULTANEOS','2.5'),/inteiro/);
assert.equal(st.validar('ORBIS_LOTE_SIMULTANEOS',' 3'),'3');
assert.throws(()=>st.validar('ORBIS_BASE_PADRAO','scopus'),/opção/);
assert.equal(st.validar('ORBIS_BASE_PADRAO','lilacs'),'lilacs');
assert.equal(st.validar('ORBIS_IA_PROVEDOR','local'),'local');
assert.throws(()=>st.validar('OLLAMA_URL','ftp://x'),/http/);
assert.throws(()=>st.validar('OLLAMA_URL','não é endereço'),/endereço/);
assert.equal(st.validar('OLLAMA_URL','http://gpu.local:11434'),'http://gpu.local:11434');
assert.equal(st.validar('OLLAMA_MODELO','qwen3:14b'),'qwen3:14b');
assert.throws(()=>st.validar('OLLAMA_MODELO','qwen 3'),/formato/);
assert.throws(()=>st.validar('OLLAMA_CONTEXTO','1024'),/2048 a 131072/);
// Booleano desligado por padrão: desligar = apagar (o motor trata presença como ligado).
assert.equal(st.validar('PAPER_FETCH_NO_SCIHUB',true),'1');
assert.equal(st.validar('PAPER_FETCH_NO_SCIHUB',false),'','desligar = apagar a chave');
// Booleano ligado por padrão: apagar voltaria a ligar, então desligar grava 0.
assert.equal(st.validar('ORBIS_SALVAR_IMAGENS',false),'0');
assert.equal(st.validar('ORBIS_SALVAR_IMAGENS',true),'1');
assert.throws(()=>st.validar('ORBIS_SALVAR_IMAGENS','talvez'),/ligado ou desligado/);
assert.throws(()=>st.validar('ORBIS_IA_MODELO_GEMINI','x/../../y?key=1'),/formato/,'modelo vai na URL do Gemini');
assert.equal(st.validar('ORBIS_IA_MODELO_ANTHROPIC','claude-sonnet-5'),'claude-sonnet-5');

// ---------------------------------------------------------------------------
// Resolução: tela → ambiente (com alias) → padrão
// ---------------------------------------------------------------------------
const r=st.resolver({NCBI_API_KEY:'da-tela-123456789'},{NCBI_API_KEY:'do-ambiente',EMBASE_API_KEY:'alias-embase-12345',CORE_API_KEY:'x'});
assert.deepEqual(r.NCBI_API_KEY,{valor:'da-tela-123456789',origem:'tela'});
assert.deepEqual(r.ELSEVIER_API_KEY,{valor:'alias-embase-12345',origem:'ambiente'},'alias do ambiente vale');
assert.deepEqual(r.ORBIS_LOTE_SIMULTANEOS,{valor:'2',origem:'padrão'});
assert.deepEqual(r.OLLAMA_MODELO,{valor:'qwen3:14b',origem:'padrão'});
assert.deepEqual(r.NCBI_EMAIL,{valor:'',origem:''});
assert.equal(r.CORE_API_KEY,undefined,'item só do motor não é resolvido no ORBIS');
assert.equal(st.valores(r).NCBI_API_KEY,'da-tela-123456789');

// ---------------------------------------------------------------------------
// Máscara e visão pública
// ---------------------------------------------------------------------------
assert.equal(st.mascarar(''),'');
assert.equal(st.mascarar('curta-1234'),'••••','segredo curto não mostra nada');
assert.equal(st.mascarar('abcdefghijkl'),'••••ijkl');
const pub=st.visaoPublica(r);
assert.equal(pub.NCBI_API_KEY.valor,'••••6789');
assert.equal(pub.NCBI_API_KEY.preenchido,true);
assert.equal(pub.ORBIS_LOTE_SIMULTANEOS.valor,'2','não-segredo aparece inteiro');
assert.ok(!JSON.stringify(pub).includes('da-tela-123456789'));

// ---------------------------------------------------------------------------
// Destinos
// ---------------------------------------------------------------------------
const d=st.separarPorDestino({NCBI_API_KEY:'a',CORE_API_KEY:'b',UNPAYWALL_EMAIL:'c@d.ef',NCBI_EMAIL:'',ORBIS_SALVAR_IMAGENS:'0'});
assert.deepEqual(d.orbis,{NCBI_API_KEY:'a',UNPAYWALL_EMAIL:'c@d.ef',NCBI_EMAIL:null});
assert.deepEqual(d.motor,{CORE_API_KEY:'b',UNPAYWALL_EMAIL:'c@d.ef',ORBIS_SALVAR_IMAGENS:'0'});

// ---------------------------------------------------------------------------
// Erros de teste de credencial
// ---------------------------------------------------------------------------
assert.equal(st.traduzirErro('Gemini HTTP 403: permission denied'),'Chave recusada pelo serviço.');
assert.equal(st.traduzirErro('HTTP 429'),'Limite de consultas atingido; tente de novo em instantes.');
assert.equal(st.traduzirErro('TypeError: fetch failed'),'Sem conexão com o serviço.');
assert.equal(st.semSegredos('falhou em ?key=segredo-gemini-999',{GEMINI_API_KEY:'segredo-gemini-999'}),'falhou em ?key=••••');

// ---------------------------------------------------------------------------
// Lote
// ---------------------------------------------------------------------------
assert.equal(st.simultaneos(null),2);
assert.equal(st.simultaneos({ORBIS_LOTE_SIMULTANEOS:{valor:'4'}}),4);
assert.equal(st.simultaneos({ORBIS_LOTE_SIMULTANEOS:{valor:'40'}}),2,'fora da faixa cai no padrão');

// ---------------------------------------------------------------------------
// Booleano como a tela mostra: valor do motor ou, sem valor, o padrão.
// ---------------------------------------------------------------------------
assert.equal(st.ligado(st.ITENS.ORBIS_SALVAR_IMAGENS,{preenchido:false,valor:''}),true,'sem valor = padrão ligado');
assert.equal(st.ligado(st.ITENS.ORBIS_SALVAR_IMAGENS,{preenchido:true,valor:'0'}),false);
assert.equal(st.ligado(st.ITENS.PAPER_FETCH_NO_SCIHUB,{preenchido:false,valor:''}),false);
assert.equal(st.ligado(st.ITENS.PAPER_FETCH_NO_SCIHUB,{preenchido:true,valor:'1'}),true);

// ---------------------------------------------------------------------------
// Paridade: o que o ORBIS manda ao motor = o que o motor aceita, com as mesmas
// marcas de segredo e de reinício.
// ---------------------------------------------------------------------------
const py=await readFile('servico-python/config_env.py','utf8');
const doMotor=Object.fromEntries([...py.matchAll(/^\s+"([A-Z0-9_]+)":\s*\((True|False),\s*(True|False)\),/gm)].map(m=>[m[1],{segredo:m[2]==='True',reiniciar:m[3]==='True'}]));
const doCatalogo=Object.fromEntries(st.CATALOGO.filter(i=>i.destino!=='orbis').map(i=>[i.key,{segredo:i.tipo==='secret',reiniciar:!!i.reiniciar}]));
assert.deepEqual(doMotor,doCatalogo,'catálogo e PERMITIDAS do motor divergem');

console.log('settings: ok');
