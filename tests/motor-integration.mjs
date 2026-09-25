import {createRequire} from 'node:module';import {realpathSync,readFileSync,readdirSync} from 'node:fs';import assert from 'node:assert/strict';
const require=createRequire(realpathSync('./node_modules/wrangler')+'/package.json');const {Miniflare}=require('miniflare');

// Motor falso: responde como o servico-python, por DOI. Toda saída de rede do
// Worker passa por aqui, então o teste não depende da internet.
const texto='Texto integral do artigo. '.repeat(40);
const identidade={ok:true,metodo:'doi_in_pdf',score:1,detalhe:'identidade confirmada'};
const respostas={
 '10.1234/ok':{ok:true,fonte:'unpaywall',fontes_tentadas:['unpaywall'],identidade,texto,paginas:3,chars:texto.length,texto_truncado:false},
 '10.1234/baixado':{ok:true,fonte:'pmc',fontes_tentadas:['pmc'],arquivo:'Silva_2021_Teste.pdf',identidade,texto:'',paginas:2,chars:0,texto_truncado:false,aviso:'sem_texto'},
 '10.1234/nada':{ok:false,erro:'Nenhuma fonte entregou o PDF.',fontes_tentadas:['unpaywall','pmc']},
};
const pedidos=[];
async function motor(req){
 const u=new URL(req.url);
 if(u.origin!=='http://motor.test')return new Response('rede bloqueada no teste',{status:599});
 if(u.pathname==='/saude')return Response.json({ok:true,recursos:['download_completo'],faltando:[],pasta_pdfs:'/dados/pdfs'});
 const b=await req.json();pedidos.push(b);
 if(b.doi==='10.1234/disco')return Response.json({detail:'Não foi possível usar a pasta /dados/pdfs: sem espaço'},{status:507});
 return Response.json(respostas[b.doi]);
}
const mf=new Miniflare({modules:[{type:'ESModule',path:'dist/server/index.js'},...readdirSync('dist/server',{recursive:true}).filter(f=>/\.(js|mjs)$/.test(f)&&f!=='index.js').map(f=>({type:'ESModule',path:'dist/server/'+f}))],compatibilityDate:'2026-05-15',compatibilityFlags:['nodejs_compat'],d1Databases:['DB'],r2Buckets:['BUCKET'],bindings:{ORBIS_ENGINE_URL:'http://motor.test'},outboundService:motor});
try{
 const db=await mf.getD1Database('DB'),r2=await mf.getR2Bucket('BUCKET');
 for(const f of readdirSync('drizzle').filter(f=>f.endsWith('.sql')).sort())for(const sql of readFileSync('drizzle/'+f,'utf8').split('--> statement-breakpoint'))if(sql.trim())await db.prepare(sql).run();
 const auth={'oai-authenticated-user-id':'test-a','oai-authenticated-user-email':'test-a@example.test'};
 async function req(path,method='GET',data){const headers={...auth};if(data!==undefined)headers['content-type']='application/json';const r=await mf.dispatchFetch('https://test.example'+path,{method,headers,body:data!==undefined?JSON.stringify(data):undefined});const raw=await r.text();return {status:r.status,data:raw.startsWith('{')||raw.startsWith('[')?JSON.parse(raw):raw}}
 const bytes=async id=>(await db.prepare('SELECT bytes FROM projects WHERE id=?').bind(id).first()).bytes;

 let r=await req('/api/projects','POST',{name:'Motor'});const id=r.data.id,path='/api/projects/'+id;
 const at=new Date().toISOString();
 for(const doi of ['10.1234/ok','10.1234/baixado','10.1234/nada','10.1234/disco'])
  await db.prepare('INSERT INTO search_items(project,doi,status,result,updated) VALUES(?,?,?,?,?)').bind(id,doi,'done',JSON.stringify({doi,found:true,title:'Artigo '+doi,authors:'Silva',year:'2021',journal:'Teste',pdfUrls:[]}),at).run();

 // Estado do motor para a interface.
 r=await req(path+'/motor');assert.equal(r.status,200);assert.equal(r.data.online,true);assert.equal(r.data.pdfDir,'/dados/pdfs');

 // Modo inválido e DOI fora do lote.
 assert.equal((await req(path+'/motor','POST',{doi:'10.1234/ok',modo:'tudo'})).status,400);
 assert.equal((await req(path+'/motor','POST',{doi:'10.9/fora',modo:'baixar'})).status,404);

 // Analisar: texto no R2, cota conta, metadados vão ao motor.
 r=await req(path+'/motor','POST',{doi:'10.1234/ok',modo:'analisar'});
 assert.equal(r.status,200,JSON.stringify(r.data));assert.equal(r.data.ok,true);
 const key=r.data.motor.texto.key;
 assert.equal(await (await r2.get(key)).text(),texto);
 assert.equal(await bytes(id),texto.length);
 assert.deepEqual([pedidos.at(-1).projeto,pedidos.at(-1).modo,pedidos.at(-1).titulo,pedidos.at(-1).autor],[id,'analisar','Artigo 10.1234/ok','Silva']);

 // Repetir o mesmo DOI não conta a cota duas vezes.
 await req(path+'/motor','POST',{doi:'10.1234/ok',modo:'analisar'});
 assert.equal(await bytes(id),texto.length);

 // Baixar sem texto extraível.
 r=await req(path+'/motor','POST',{doi:'10.1234/baixado',modo:'baixar'});
 assert.equal(r.data.motor.arquivo,'Silva_2021_Teste.pdf');assert.equal(r.data.motor.texto,null);assert.equal(r.data.motor.aviso,'sem_texto');

 // Não localizado é resultado, não erro; o motivo fica na linha.
 r=await req(path+'/motor','POST',{doi:'10.1234/nada',modo:'analisar'});
 assert.equal(r.status,200);assert.equal(r.data.ok,false);
 assert.equal((await req(path)).data.searches.find(x=>x.doi==='10.1234/nada').error,'Nenhuma fonte entregou o PDF.');

 // Disco cheio: 507 com a mensagem do motor.
 r=await req(path+'/motor','POST',{doi:'10.1234/disco',modo:'baixar'});
 assert.equal(r.status,507);assert.match(r.data.message,/sem espaço/);

 // Incorporar a partir do motor: sem PDF no R2.
 let p=(await req(path)).data;
 r=await req(path,'PATCH',{revision:p.revision,action:'incorporate',doi:'10.1234/ok'});assert.equal(r.status,200,JSON.stringify(r.data));
 p=(await req(path)).data;
 const art=p.state.articles.find(a=>a.doi==='10.1234/ok');
 assert.equal(art.source.kind,'motor');assert.equal(art.source.modo,'analisar');assert.equal(art.texto.key,key);assert.equal(art.identity.ok,true);
 assert.equal(p.documents.length,0,'nenhum PDF no R2');
 r=await req(path,'PATCH',{revision:p.revision,action:'incorporate',doi:'10.1234/baixado'});assert.equal(r.status,200);
 p=(await req(path)).data;
 assert.equal(p.state.articles.find(a=>a.doi==='10.1234/baixado').source.arquivoLocal,'Silva_2021_Teste.pdf');
 assert.equal((await req(path,'PATCH',{revision:p.revision,action:'incorporate',doi:'10.1234/nada'})).status,422);

 // Texto pela rota.
 r=await req(path+'/texto?article='+art.id);assert.equal(r.status,200);assert.equal(r.data,texto);
 assert.equal((await req(path+'/texto?article=inexistente')).status,404);

 // Modo salvo no projeto.
 p=(await req(path)).data;
 assert.equal((await req(path,'PATCH',{revision:p.revision,action:'downloadMode',mode:'x'})).status,400);
 assert.equal((await req(path,'PATCH',{revision:p.revision,action:'downloadMode',mode:'analisar'})).status,200);
 assert.equal((await req(path)).data.state.settings.downloadMode,'analisar');

 // Excluir o artigo: texto e cota liberados, DOI volta a exigir download.
 p=(await req(path)).data;
 r=await req(path,'PATCH',{revision:p.revision,action:'removeArticle',article:art.id,confirmation:art.id});assert.equal(r.status,200,JSON.stringify(r.data));
 assert.equal(await r2.get(key),null);assert.equal(await bytes(id),0);
 assert.equal((await req(path)).data.searches.find(x=>x.doi==='10.1234/ok').result.motor,undefined);

 // Limpar a busca apaga textos de DOIs não incorporados.
 await req(path+'/motor','POST',{doi:'10.1234/ok',modo:'analisar'});assert.equal(await bytes(id),texto.length);
 p=(await req(path)).data;
 assert.equal((await req(path,'PATCH',{revision:p.revision,action:'clearStage',stage:'search'})).status,200);
 assert.equal(await r2.get(key),null);assert.equal(await bytes(id),0);

 // Restauração: a chave do texto é reescrita para o projeto novo.
 r=await req('/api/projects','POST',{name:'Restaurado'});const id2=r.data.id,path2='/api/projects/'+id2;
 const st=(await req(path)).data.state;
 st.articles=[{id:'a1',filename:'a1.pdf',doi:'10.1234/ok',title:'T',authors:'',year:'',abstract:'',source:{kind:'motor',modo:'analisar'},texto:{key:id+'/texto/antigo.txt',chars:texto.length,paginas:3,truncado:false,bytes:texto.length,origem:'motor'}}];
 r=await req(path2,'PATCH',{revision:0,action:'restore',state:st,hash:'h',expected:[]});assert.equal(r.status,200,JSON.stringify(r.data));
 assert.ok((await req(path2)).data.state.articles[0].texto.key.startsWith(id2+'/texto/'));
 const up=await mf.dispatchFetch('https://test.example'+path2+'/texto?article=a1',{method:'POST',headers:{...auth,'content-type':'text/plain; charset=utf-8'},body:texto});
 assert.equal(up.status,200,await up.text());
 assert.equal((await req(path2+'/texto?article=a1')).data,texto);
 assert.equal(await bytes(id2),texto.length);
 // Reconsultar um DOI que o motor já resolveu não apaga o resultado do motor
 // (senão o lote baixa de novo e cobra o texto duas vezes).
 r=await req('/api/projects','POST',{name:'Reconsulta'});const id3=r.data.id,path3='/api/projects/'+id3;
 await db.prepare('INSERT INTO search_items(project,doi,status,result,updated) VALUES(?,?,?,?,?)').bind(id3,'10.1234/ok','done',JSON.stringify({doi:'10.1234/ok',found:true,title:'Artigo',pdfUrls:[]}),at).run();
 await req(path3+'/motor','POST',{doi:'10.1234/ok',modo:'analisar'});const antes=await bytes(id3);
 await req(path3+'/search','POST',{doi:'10.1234/ok',retry:true});
 assert.equal((await req(path3)).data.searches[0].result?.motor?.ok,true,'reconsulta preserva o motor');
 assert.equal(await bytes(id3),antes);

 // Backup restaurado não pode apontar para textos do projeto de origem.
 r=await req('/api/projects','POST',{name:'Origem'});const idA=r.data.id,pathA='/api/projects/'+idA;
 for(const doi of ['10.1234/ok','10.1234/baixado'])await db.prepare('INSERT INTO search_items(project,doi,status,result,updated) VALUES(?,?,?,?,?)').bind(idA,doi,'done',JSON.stringify({doi,found:true,title:'Artigo '+doi,pdfUrls:[]}),at).run();
 const oneMore={...respostas['10.1234/ok']};respostas['10.1234/baixado']={...oneMore};
 await req(pathA+'/motor','POST',{doi:'10.1234/ok',modo:'analisar'});await req(pathA+'/motor','POST',{doi:'10.1234/baixado',modo:'analisar'});
 let pA=(await req(pathA)).data;await req(pathA,'PATCH',{revision:pA.revision,action:'incorporate',doi:'10.1234/ok'});
 pA=(await req(pathA)).data;const chavesA=pA.searches.map(x=>x.result.motor.texto.key);
 r=await req('/api/projects','POST',{name:'Destino'});const idB=r.data.id,pathB='/api/projects/'+idB;
 r=await req(pathB,'PATCH',{revision:0,action:'restore',state:pA.state,hash:'h',expected:[],searches:pA.searches});assert.equal(r.status,200,JSON.stringify(r.data));
 const pB=(await req(pathB)).data;
 assert.ok(pB.searches.every(x=>!x.result?.motor),'motor da origem não vem junto');
 assert.equal((await req(pathB,'PATCH',{revision:pB.revision,action:'finishImport'})).status,200);
 let pB2=(await req(pathB)).data;
 assert.equal((await req(pathB,'PATCH',{revision:pB2.revision,action:'clearStage',stage:'search'})).status,200);
 pB2=(await req(pathB)).data;const artB=pB2.state.articles[0];
 assert.equal((await req(pathB,'PATCH',{revision:pB2.revision,action:'removeArticle',article:artB.id,confirmation:artB.id})).status,200);
 for(const k of chavesA)assert.ok(await r2.get(k),'texto da origem continua: '+k);
 console.log('motor-integration: ok');
}finally{await mf.dispose()}
