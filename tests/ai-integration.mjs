import {createRequire} from 'node:module';import {realpathSync,readFileSync,readdirSync} from 'node:fs';import assert from 'node:assert/strict';
const require=createRequire(realpathSync('./node_modules/wrangler')+'/package.json');const {Miniflare}=require('miniflare');

// Ollama simulado: toda saída de rede do Worker passa por aqui, então o teste
// não depende da internet nem da GPU. Responde "sim" para toda pergunta.
let ollamaNoAr=true,falhar=false;const chamadas=[];
async function rede(req){
 const u=new URL(req.url);
 if(u.origin!=='http://ollama.test'||!ollamaNoAr)return new Response('rede bloqueada no teste',{status:599});
 if(u.pathname==='/api/tags')return Response.json({models:[{name:'qwen3:14b'}]});
 if(u.pathname!=='/api/chat')return new Response('?',{status:404});
 const b=await req.json();chamadas.push(b);
 if(falhar)return Response.json({error:'model crashed'},{status:500});
 const pedido=JSON.parse(b.messages[1].content),n=pedido.criterios?.perguntas?.length||pedido.criterios?.questions?.length||1;
 return Response.json({message:{role:'assistant',content:JSON.stringify({items:pedido.artigos.map(a=>({article_id:a.article_id,arquivo:a.arquivo,source_hash:a.source_hash,
  triagem_nivel1:Array.from({length:n},(_,i)=>({pergunta:i+1,resposta:'sim',motivo:'Atende.',evidencia:''}))}))})},done:true});
}
const mf=new Miniflare({modules:[{type:'ESModule',path:'dist/server/index.js'},...readdirSync('dist/server',{recursive:true}).filter(f=>/\.(js|mjs)$/.test(f)&&f!=='index.js').map(f=>({type:'ESModule',path:'dist/server/'+f}))],compatibilityDate:'2026-05-15',compatibilityFlags:['nodejs_compat'],d1Databases:['DB'],r2Buckets:['BUCKET'],bindings:{OLLAMA_URL:'http://ollama.test',ORBIS_IA_LOTE:'2'},outboundService:rede});
try{
 const db=await mf.getD1Database('DB');
 for(const f of readdirSync('drizzle').filter(f=>f.endsWith('.sql')).sort())for(const sql of readFileSync('drizzle/'+f,'utf8').split('--> statement-breakpoint'))if(sql.trim())await db.prepare(sql).run();
 const auth={'oai-authenticated-user-id':'test-a','oai-authenticated-user-email':'test-a@example.test'};
 async function req(path,method='GET',data){const headers={...auth};if(data!==undefined)headers['content-type']='application/json';const r=await mf.dispatchFetch('https://test.example'+path,{method,headers,body:data!==undefined?JSON.stringify(data):undefined});const raw=await r.text();return {status:r.status,data:raw.startsWith('{')||raw.startsWith('[')?JSON.parse(raw):raw}}
 const protocolo={population:'P',concept:'C',context:'X',inclusion:'I',exclusion:'',questions:['A população atende?']};

 // Projeto com 3 artigos no corpus e protocolo aprovado.
 let r=await req('/api/projects','POST',{name:'IA local'});const id=r.data.id,path='/api/projects/'+id;
 let p=(await req(path)).data;
 assert.equal((await req(path,'PATCH',{revision:p.revision,action:'protocol',protocol:protocolo,approve:true})).status,200);
 p=(await req(path)).data;const st=p.state;
 st.articles=['a','b','c'].map(x=>({id:x,filename:x+'.pdf',doi:'10.1/'+x,title:'Artigo '+x,authors:'Silva',year:'2021',abstract:'Resumo '+x,source:{kind:'local'}}));
 const novo=(await req('/api/projects','POST',{name:'IA local 2'})).data,path2='/api/projects/'+novo.id;
 r=await req(path2,"PATCH",{revision:0,action:"restore",state:st,hash:"h",expected:[]});assert.equal(r.status,200,JSON.stringify(r.data));
 r=await req(path2,'PATCH',{revision:(await req(path2)).data.revision,action:'finishImport'});assert.equal(r.status,200,JSON.stringify(r.data));

 // Triagem do corpus em lotes de 2 (ORBIS_IA_LOTE) até não faltar nada.
 const rodar=async(extra={})=>{const q=(await req(path2)).data;return req(path2,'PATCH',{revision:q.revision,action:'runAI',stage:'triagem',...extra})};
 r=await rodar();assert.equal(r.status,200,JSON.stringify(r.data));
 assert.equal(r.data.ai.provider,'Ollama');assert.equal(r.data.ai.model,'qwen3:14b');
 assert.equal(r.data.ai.analysed,2);assert.equal(r.data.ai.remaining,1);
 assert.equal(chamadas[0].think,false);assert.equal(chamadas[0].format,'json');assert.equal(chamadas[0].options.num_ctx,16384);assert.equal(chamadas[0].stream,false);
 r=await rodar();assert.equal(r.data.ai.analysed,1);assert.equal(r.data.ai.remaining,0);
 const antes=chamadas.length;
 r=await rodar();assert.equal(r.status,200,'nada pendente não é erro');assert.equal(r.data.ai.analysed,0);assert.equal(r.data.ai.remaining,0);
 assert.equal(chamadas.length,antes,'nada pendente: o Ollama não é chamado');
 p=(await req(path2)).data;
 assert.ok(p.state.articles.every(a=>a.aiAnalyses?.some(x=>x.provider==='Ollama'&&x.model==='qwen3:14b')),'cada artigo com análise do modelo local');
 assert.ok(p.state.articles.every(a=>!a.triage),'IA não decide: nenhuma triagem humana criada');
 r=await rodar({limit:1,stage:'triagem'});assert.equal(r.data.ai.analysed,0,'limit explícito também respeita os já feitos');

 // Triagem de títulos e resumos pelo mesmo provedor.
 const at=new Date().toISOString();
 for(const doi of ['10.9/x','10.9/y'])await db.prepare('INSERT INTO search_items(project,doi,status,result,updated) VALUES(?,?,?,?,?)').bind(novo.id,doi,'done',JSON.stringify({doi,found:true,title:'Registro '+doi,authors:'Lima',year:'2020',journal:'J',abstract:'Resumo de '+doi}),at).run();
 r=await req(path2+'/screening','POST',{action:'ai'});assert.equal(r.status,200,JSON.stringify(r.data));
 assert.equal(r.data.provider,'Ollama');assert.equal(r.data.analysed,2);assert.equal(r.data.remaining,0);
 p=(await req(path2)).data;assert.ok(p.screening.every(l=>l.ai?.provider==='Ollama'&&!l.decision),'sugestão, não decisão');

 // Lote que falha: pendente continua, a tela recebe o motivo.
 falhar=true;
 r=await req(path2+'/screening','POST',{action:'ai'});assert.equal(r.status,200);assert.equal(r.data.analysed,0);
 falhar=false;

 // Ollama fora e nenhuma chave de nuvem: nenhum provedor.
 ollamaNoAr=false;
 await db.prepare("UPDATE screening SET ai=NULL WHERE project=?").bind(novo.id).run();
 r=await req(path2+'/screening','POST',{action:'ai'});assert.equal(r.status,400);assert.match(r.data.message,/Nenhum provedor/);
 const q=(await req(path2)).data;
 r=await req(path2,'PATCH',{revision:q.revision,action:'runAI',stage:'triagem'});assert.equal(r.status,400);assert.match(r.data.message,/Nenhum provedor/);
 console.log('ai-integration: ok');
}finally{await mf.dispose()}
