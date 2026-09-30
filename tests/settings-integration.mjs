import {createRequire} from 'node:module';import {realpathSync,readFileSync,readdirSync} from 'node:fs';import assert from 'node:assert/strict';
const require=createRequire(realpathSync('./node_modules/wrangler')+'/package.json');const {Miniflare}=require('miniflare');

// Motor falso: guarda o que recebe e exige o token, como o servico-python.
// Ollama falso: lista um modelo e responde a chamada mínima do botão Testar.
const recebido={};let motorFora=false,ollamaFora=false;const tokensVistos=[],chats=[];
const fontes=[];
async function rede(req){
 const u=new URL(req.url);
 if(['eutils.ncbi.nlm.nih.gov','api.unpaywall.org'].includes(u.hostname)){fontes.push(u);
  return u.hostname==='eutils.ncbi.nlm.nih.gov'?Response.json({esearchresult:{idlist:[],count:'0'}}):Response.json({},{status:404});}
 if(u.origin==='http://ollama.test'){
  if(ollamaFora)throw new Error('connection refused');
  if(u.pathname==='/api/tags')return Response.json({models:[{name:'qwen3:14b'},{name:'llama3:8b'}]});
  if(u.pathname==='/api/chat'){const b=await req.json();chats.push(b);return Response.json({message:{content:'{"ok":true}'},done:true});}
  return new Response('?',{status:404});
 }
 // Fora do ar = conexão recusada, não uma resposta HTTP.
 if(motorFora)throw new Error('connection refused');
 if(u.origin!=='http://motor.test')return new Response('rede bloqueada no teste',{status:599});
 if(u.pathname==='/saude')return Response.json({ok:true,recursos:[],faltando:[],pasta_pdfs:'/d'});
 tokensVistos.push(req.headers.get('x-orbis-token'));
 if(req.headers.get('x-orbis-token')!=='tok')return Response.json({detail:'Token do ORBIS ausente ou inválido.'},{status:403});
 if(u.pathname==='/config'&&req.method==='PUT'){const b=await req.json();Object.assign(recebido,b.mudancas);return Response.json({salvas:Object.keys(b.mudancas).sort(),reiniciar:Object.keys(b.mudancas).filter(k=>['CORE_API_KEY','UNPAYWALL_EMAIL'].includes(k)).sort(),itens:{}});}
 if(u.pathname==='/config')return Response.json({itens:{CORE_API_KEY:{preenchido:!!recebido.CORE_API_KEY,valor:recebido.CORE_API_KEY?'••••'+recebido.CORE_API_KEY.slice(-4):''}}});
 return new Response('?',{status:404});
}
const modulos=[{type:'ESModule',path:'dist/server/index.js'},...readdirSync('dist/server',{recursive:true}).filter(f=>/\.(js|mjs)$/.test(f)&&f!=='index.js').map(f=>({type:'ESModule',path:'dist/server/'+f}))];
const mf=new Miniflare({modules:modulos,compatibilityDate:'2026-05-15',compatibilityFlags:['nodejs_compat'],d1Databases:['DB'],r2Buckets:['BUCKET'],
 bindings:{ORBIS_ENGINE_URL:'http://motor.test',ORBIS_ENGINE_TOKEN:'tok',NCBI_API_KEY:'ambiente-ncbi-123456',OLLAMA_URL:'http://ollama.test'},outboundService:rede});
try{
 const db=await mf.getD1Database('DB');
 for(const f of readdirSync('drizzle').filter(f=>f.endsWith('.sql')).sort())for(const sql of readFileSync('drizzle/'+f,'utf8').split('--> statement-breakpoint'))if(sql.trim())await db.prepare(sql).run();
 const auth={'oai-authenticated-user-id':'test-a','oai-authenticated-user-email':'test-a@example.test'};
 // Configurações só mudam na instalação local: o ORBIS do start.py responde em localhost.
 async function req(method='GET',data,headers=auth,path='/api/settings',origem='http://localhost:5173'){const h={...headers};if(data!==undefined)h['content-type']='application/json';const r=await mf.dispatchFetch(origem+path,{method,headers:h,body:data!==undefined?JSON.stringify(data):undefined});const raw=await r.text();return {status:r.status,raw,data:raw?JSON.parse(raw):null};}

 // Login obrigatório.
 assert.equal((await req('GET',undefined,{})).status,401);
 // Hospedado (outro endereço): qualquer pessoa logada leria e mudaria a
 // instalação inteira — por exemplo, apontar o Ollama para um servidor seu e
 // receber os textos dos projetos dos outros. Lá, só leitura.
 const fora='https://orbis.exemplo.org';
 let h=await req('GET',undefined,auth,'/api/settings',fora);assert.equal(h.status,200);assert.equal(h.data.editavel,false);
 h=await req('PUT',{mudancas:{OLLAMA_URL:'http://atacante.exemplo',ORBIS_IA_PROVEDOR:'local'}},auth,'/api/settings',fora);assert.equal(h.status,403);assert.match(h.data.message,/instalação local/);
 assert.equal((await req('POST',{acao:'testar',alvo:'ollama'},auth,'/api/settings',fora)).status,403,'Testar também sai para a rede');
 assert.equal((await req()).data.orbis.OLLAMA_URL.valor,'http://ollama.test','nada gravado');
 assert.equal((await req()).data.editavel,true);

 // Ambiente aparece mascarado, com origem; motor e Ollama no ar.
 let r=await req();assert.equal(r.status,200,r.raw);
 assert.equal(r.data.orbis.NCBI_API_KEY.origem,'ambiente');
 assert.equal(r.data.orbis.NCBI_API_KEY.valor,'••••3456');
 assert.ok(!r.raw.includes('ambiente-ncbi-123456'),'segredo do ambiente não sai inteiro');
 assert.equal(r.data.motor.online,true);
 assert.deepEqual(r.data.ollama,{online:true,modelos:['qwen3:14b','llama3:8b']},'modelos instalados para a tela escolher');
 assert.equal(r.data.orbis.OLLAMA_URL.origem,'ambiente');

 // Tela vence o ambiente.
 r=await req('PUT',{mudancas:{NCBI_API_KEY:'tela-ncbi-abcdefghijk',ORBIS_LOTE_SIMULTANEOS:'3'}});
 assert.equal(r.status,200,r.raw);assert.ok(!r.raw.includes('tela-ncbi-abcdefghijk'));
 r=await req();assert.equal(r.data.orbis.NCBI_API_KEY.origem,'tela');assert.equal(r.data.orbis.NCBI_API_KEY.valor,'••••hijk');assert.equal(r.data.orbis.ORBIS_LOTE_SIMULTANEOS.valor,'3');

 // Tudo ou nada: um item inválido derruba o pedido inteiro.
 r=await req('PUT',{mudancas:{ORBIS_LOTE_SIMULTANEOS:'4',NCBI_EMAIL:'a@b.co\nHTTP_PROXY=x'}});assert.equal(r.status,400);assert.match(r.data.message,/quebra de linha/);
 assert.equal((await req()).data.orbis.ORBIS_LOTE_SIMULTANEOS.valor,'3','nada gravado');
 assert.equal((await req('PUT',{mudancas:{NAO_EXISTE:'1'}})).status,400);
 assert.equal((await req('PUT',{mudancas:[]})).status,400);

 // Itens do motor seguem com token; `ambos` fica nos dois lados; desligar
 // opção ligada por padrão vai como 0.
 r=await req('PUT',{mudancas:{CORE_API_KEY:'core-123456789012',UNPAYWALL_EMAIL:'a@b.co',ORBIS_SALVAR_IMAGENS:false}});
 assert.equal(r.status,200,r.raw);assert.equal(r.data.motor.enviado,true);assert.deepEqual(r.data.motor.reiniciar,['CORE_API_KEY','UNPAYWALL_EMAIL']);
 assert.deepEqual(recebido,{CORE_API_KEY:'core-123456789012',UNPAYWALL_EMAIL:'a@b.co',ORBIS_SALVAR_IMAGENS:'0'});
 assert.ok(tokensVistos.every(t=>t==='tok'));
 assert.deepEqual(r.data.salvasNoOrbis,['UNPAYWALL_EMAIL'],'CORE e extração são só do motor');
 assert.equal(r.data.orbis.UNPAYWALL_EMAIL.origem,'tela');
 assert.ok(!r.raw.includes('core-123456789012'),'segredo não volta na resposta');

 // Apagar volta ao ambiente.
 r=await req('PUT',{mudancas:{NCBI_API_KEY:null}});assert.equal(r.data.orbis.NCBI_API_KEY.origem,'ambiente');

 // Motor fora do ar: ORBIS salva, resposta diz o que ficou pendente.
 motorFora=true;
 r=await req('PUT',{mudancas:{UNPAYWALL_EMAIL:'c@d.co'}});
 assert.equal(r.status,200,r.raw);assert.equal(r.data.motor.enviado,false);assert.deepEqual(r.data.motor.pendentes,['UNPAYWALL_EMAIL']);assert.match(r.data.motor.erro,/motor/,'o Miniflare devolve 500 no lugar da falha de rede: basta dizer que foi o motor');
 assert.equal(r.data.orbis.UNPAYWALL_EMAIL.valor,'c@d.co');
 assert.equal((await req()).data.motor.online,false);
 motorFora=false;

 // Teste de credencial sem chave não sai para a rede.
 r=await req('POST',{acao:'testar',alvo:'embase'});assert.equal(r.status,200);assert.equal(r.data.ok,false);assert.match(r.data.detalhe,/Sem chave/);
 assert.equal((await req('POST',{acao:'testar',alvo:'nada'})).status,400);

 // Teste do Ollama: modelo não instalado é dito como tal; instalado, chamada mínima.
 await req('PUT',{mudancas:{OLLAMA_MODELO:'qwen9:1b'}});
 r=await req('POST',{acao:'testar',alvo:'ollama'});assert.equal(r.data.ok,false);assert.match(r.data.detalhe,/qwen9:1b.*não está instalado/);
 assert.equal(chats.length,0,'nem chama o modelo');
 await req('PUT',{mudancas:{OLLAMA_MODELO:'qwen3:14b'}});
 r=await req('POST',{acao:'testar',alvo:'ollama'});assert.equal(r.data.ok,true,r.raw);assert.match(r.data.detalhe,/qwen3:14b/);
 assert.equal(chats.length,1);assert.equal(chats[0].model,'qwen3:14b');
 ollamaFora=true;
 r=await req('POST',{acao:'testar',alvo:'ollama'});assert.equal(r.data.ok,false);assert.match(r.data.detalhe,/não respondeu/);
 assert.deepEqual((await req()).data.ollama,{online:false,modelos:[]});
 ollamaFora=false;

 // ---------------------------------------------------------------------------
 // Quem lê as configurações: busca, resolução por DOI e provedor de IA.
 // ---------------------------------------------------------------------------
 const proj=(await req('POST',{name:'Configurado'},auth,'/api/projects')).data,pp='/api/projects/'+proj.id;
 let pj=(await req('GET',undefined,auth,pp)).data;
 assert.equal((await req('PATCH',{revision:pj.revision,action:'protocol',protocol:{population:'P',concept:'C',context:'X',inclusion:'I',exclusion:'',questions:['Q?']},approve:true},auth,pp)).status,200);
 await req('PUT',{mudancas:{NCBI_API_KEY:'tela-ncbi-abcdefghijk',NCBI_EMAIL:'eu@exemplo.org',ORBIS_BUSCA_LIMITE:'7'}});
 fontes.length=0;
 r=await req('POST',{action:'database',base:'pubmed',term:'dengue'},auth,pp+'/search');
 const busca=fontes.find(u=>u.pathname.endsWith('esearch.fcgi'));
 assert.ok(busca,'a busca chegou ao PubMed: '+r.raw);
 assert.equal(busca.searchParams.get('api_key'),'tela-ncbi-abcdefghijk','chave salva na tela vai na busca');
 assert.equal(busca.searchParams.get('email'),'eu@exemplo.org');
 assert.equal(busca.searchParams.get('retmax'),'7','limite de resultados da tela');

 await db.prepare('INSERT INTO search_items(project,doi,status,result,updated) VALUES(?,?,?,?,?)').bind(proj.id,'10.5/r','done',JSON.stringify({doi:'10.5/r',found:true,title:'Registro',authors:'A',year:'2020',journal:'J',abstract:''}),new Date().toISOString()).run();
 fontes.length=0;
 r=await req('POST',{action:'refreshAbstract',doi:'10.5/r'},auth,pp+'/screening');assert.equal(r.status,200,r.raw);
 const up=fontes.find(u=>u.hostname==='api.unpaywall.org');
 assert.ok(up,'resolução consulta o Unpaywall');assert.equal(up.searchParams.get('email'),'c@d.co','e-mail do Unpaywall salvo na tela');

 await req('PUT',{mudancas:{ORBIS_IA_PROVEDOR:'anthropic'}});
 r=await req('POST',{action:'ai'},auth,pp+'/screening');assert.equal(r.status,400);assert.match(r.data.message,/escolhido em Configurações/);
 await req('PUT',{mudancas:{ORBIS_IA_PROVEDOR:'local'}});
 const antesChat=chats.length;
 r=await req('POST',{action:'ai'},auth,pp+'/screening');assert.equal(r.status,200,r.raw);assert.equal(r.data.provider,'Ollama');assert.equal(r.data.model,'qwen3:14b');
 assert.equal(chats.length,antesChat+1,'provedor local escolhido na tela');

 // Banco sem a tabela (migração não aplicada): continua respondendo pelo ambiente.
 await db.prepare('DROP TABLE settings').run();
 r=await req();assert.equal(r.status,200);assert.equal(r.data.orbis.NCBI_API_KEY.origem,'ambiente');

 console.log('PASS: configurações — login, máscara, precedência, tudo-ou-nada, motor com token, motor fora do ar, Ollama, banco sem tabela');
}finally{await mf.dispose()}
