import {createRequire} from 'node:module';import {realpathSync,readFileSync,readdirSync} from 'node:fs';import assert from 'node:assert/strict';
const require=createRequire(realpathSync('./node_modules/wrangler')+'/package.json');const {Miniflare}=require('miniflare');
const mf=new Miniflare({modules:[{type:'ESModule',path:'dist/server/index.js'},...readdirSync('dist/server',{recursive:true}).filter(f=>/\.(js|mjs)$/.test(f)&&f!=='index.js').map(f=>({type:'ESModule',path:'dist/server/'+f}))],compatibilityDate:'2026-05-15',compatibilityFlags:['nodejs_compat'],d1Databases:['DB'],r2Buckets:['BUCKET']});
try{const db=await mf.getD1Database('DB');for(const f of readdirSync('drizzle').filter(f=>f.endsWith('.sql')).sort())for(const sql of readFileSync('drizzle/'+f,'utf8').split('--> statement-breakpoint'))if(sql.trim())await db.prepare(sql).run();
async function req(path,method='GET',data,user='test-a',extra={}){const headers=user?{'oai-authenticated-user-id':user,'oai-authenticated-user-email':user+'@example.test'}:{};if(data!==undefined)headers['content-type']=data instanceof Uint8Array?'application/pdf':'application/json';const r=await mf.dispatchFetch('https://test.example'+path,{method,headers:{...headers,...extra},body:data instanceof Uint8Array?data:data!==undefined?JSON.stringify(data):undefined});const raw=await r.text();return {status:r.status,data:raw.startsWith('{')||raw.startsWith('[')?JSON.parse(raw):raw}}
assert.equal((await req('/api/projects','GET',undefined,null)).status,401);
let r=await req('/api/projects','POST',{name:'Teste sintético'});assert.equal(r.status,201,JSON.stringify(r));const id=r.data.id,path='/api/projects/'+id;
assert.equal((await req(path,'GET',undefined,'test-b')).status,404);
const protocol={population:'P',concept:'C',context:'C',inclusion:'I',exclusion:'',questions:['A população atende?']};
assert.equal((await req(path,'PATCH',{revision:0,action:'protocol',protocol,approve:true})).status,200);
assert.equal((await req(path,'PATCH',{revision:0,action:'notes',notes:'old'})).status,409);
assert.equal((await req(path,'PATCH',{revision:1,action:'notes',notes:'foreign'},'test-a',{origin:'https://evil.example'})).status,403);
await db.prepare('INSERT INTO search_items(project,doi,status,result,error,updated) VALUES(?,?,?,?,?,?)').bind(id,'10.1234/sem-pdf','done',JSON.stringify({doi:'10.1234/sem-pdf',found:true,title:'Sem PDF',pdfUrls:[]}),null,new Date().toISOString()).run();
assert.equal((await req(path,'PATCH',{revision:1,action:'incorporate',doi:'10.1234/sem-pdf'})).status,422);
assert.equal((await req(path)).data.state.articles.length,0);
assert.equal((await req(path)).data.documents.length,0);
r=await req('/api/projects','POST',{name:'Restaurar teste'});const restored='/api/projects/'+r.data.id;const state=(await req(path)).data.state;state.articles=[{id:'article',filename:'test.pdf',doi:'10.1234/test',title:'Artigo sintético',authors:'Teste',year:'2026',abstract:'Resumo'}];assert.equal((await req(restored,'PATCH',{revision:0,action:'restore',state,expected:['test.pdf'],hash:'test'})).status,200);
assert.equal((await req(restored,'PATCH',{revision:1,action:'finishImport'})).status,409);
assert.equal((await req(restored+'/pdf?article=article','POST',new TextEncoder().encode('HTML'))).status,400);
r=await req(restored+'/pdf?article=article','POST',new TextEncoder().encode('%PDF-1.7\nsynthetic'));assert.equal(r.status,200,JSON.stringify(r));const doc=r.data.id;
assert.equal((await req(restored+'/pdf?document='+doc,'GET',undefined,'test-b')).status,404);assert.ok((await req(restored+'/pdf?document='+doc)).data.startsWith('%PDF-'));
assert.equal((await req(restored,'PATCH',{revision:1,action:'finishImport'})).status,200);
assert.equal((await req(restored,'PATCH',{revision:2,action:'triage',article:'article',answers:['Não'],reason:''})).status,400);
assert.equal((await req(restored,'PATCH',{revision:2,action:'triage',article:'article',answers:['Indeterminado'],reason:''})).status,200);
const opinion={reviewer:'Revisor A',decision:'incluir',reason:'Evidência sintética',evidence:'Texto',page:'1'};
assert.equal((await req(restored,'PATCH',{revision:3,action:'fulltext',article:'article',slot:'r1',opinion})).status,200);
assert.equal((await req(restored,'PATCH',{revision:4,action:'fulltext',article:'article',slot:'r2',opinion})).status,400);
assert.equal((await req(restored,'PATCH',{revision:4,action:'fulltext',article:'article',slot:'r2',opinion:{...opinion,reviewer:'Revisor B',decision:'excluir'}})).status,200);
assert.equal((await req(restored,'PATCH',{revision:5,action:'finalReview',article:'article',decision:'incluir',reason:'Sem consenso'})).status,400);
assert.equal((await req(restored,'PATCH',{revision:5,action:'fulltext',article:'article',slot:'adjudication',opinion:{...opinion,reviewer:'Revisor C'}})).status,200);
assert.equal((await req(restored,'PATCH',{revision:6,action:'finalReview',article:'article',decision:'incluir',reason:'Revisado'})).status,200);
let saved=(await req(restored)).data;assert.equal(saved.state.articles[0].finalReview.decision,'incluir');assert.equal(saved.documents.length,1);assert.equal(saved.bytes,18);assert.ok((await req(restored+'/history')).data.length>=7);
// Local PDFs: create without DOI/protocol, retry the same file, reject mismatches, and deduplicate.
const localProject=await req('/api/projects','POST',{name:'PDFs locais'}),lp='/api/projects/'+localProject.data.id;
const localBytes=new TextEncoder().encode('%PDF-1.7\nlocal synthetic');
const {createHash}=await import('node:crypto');const localHash=createHash('sha256').update(localBytes).digest('hex');
let local=await req(lp,'PATCH',{revision:0,action:'localArticle',filename:'meu artigo.pdf',hash:localHash});assert.equal(local.status,200,JSON.stringify(local));const localArticle=local.data.article;
assert.equal(local.data.state.articles[0].doi,'');assert.equal(local.data.state.articles[0].title,'meu artigo');
assert.equal((await req(lp,'PATCH',{revision:1,action:'localArticle',filename:'renomeado.pdf',hash:localHash})).data.article,localArticle);
assert.equal((await req(lp+'/pdf?article='+localArticle,'POST',new TextEncoder().encode('%PDF-1.7\nwrong'))).status,400);
assert.equal((await req(lp)).data.bytes,0);
assert.equal((await req(lp+'/pdf?article='+localArticle,'POST',localBytes)).status,200);
assert.equal((await req(lp+'/pdf?article='+localArticle,'POST',localBytes)).data.existing,true);
assert.equal((await req(lp,'PATCH',{revision:1,action:'localArticle',filename:'copia.pdf',hash:localHash})).data.article,localArticle);
const localSaved=(await req(lp)).data;assert.equal(localSaved.state.articles.length,1);assert.equal(localSaved.documents.length,1);assert.equal(localSaved.bytes,localBytes.length);
assert.equal((await req(lp,'PATCH',{revision:1,action:'localArticle',filename:'a.pdf',hash:localHash},'test-b')).status,404);
assert.equal((await req(lp,'PATCH',{revision:1,action:'localArticle',filename:'a.pdf',hash:'invalid'})).status,400);
assert.equal((await req(lp,'PATCH',{revision:1,action:'removeArticle',article:localArticle,confirmation:'wrong'})).status,409);
assert.equal((await req(lp,'PATCH',{revision:1,action:'removeArticle',article:localArticle,confirmation:localArticle},'test-b')).status,404);
assert.equal((await req(lp,'PATCH',{revision:1,action:'removeArticle',article:localArticle,confirmation:localArticle})).status,200);
const localRemoved=(await req(lp)).data;assert.equal(localRemoved.state.articles.length,0);assert.equal(localRemoved.documents.length,0);assert.equal(localRemoved.bytes,0);
// AI results must survive a reload and deletion must preserve the human verdict.
const {stripTypeScriptTypes}=await import('node:module');const aiUrl='data:text/javascript;base64,'+Buffer.from(stripTypeScriptTypes(readFileSync('lib/ai-analysis.ts','utf8'))).toString('base64');const ai=await import(aiUrl);
const aiPackage=ai.makeAIPackage({...saved,id:restored.split('/').at(-1)},'triagem');
const aiResponse={...aiPackage.formato_resposta,provider:'Teste IA',model:'Modelo sintético',items:[{...aiPackage.items[0],resumo:'Resumo sintético da IA',triagem_nivel1:[{pergunta:1,resposta:'nao',motivo:'Evidência sintética de exclusão'}]}]};
let imported=await req(restored,'PATCH',{revision:saved.revision,action:'importAI',stage:'triagem',response:aiResponse});assert.equal(imported.status,200,JSON.stringify(imported));assert.equal(imported.data.aiReport.applied,1);
let aiSaved=(await req(restored)).data;assert.equal(aiSaved.state.articles[0].aiAnalyses[0].decision,'excluir');assert.equal(aiSaved.state.articles[0].triage.decision,'incluir');assert.equal(aiSaved.state.articles[0].finalReview.decision,'incluir');
const aiBackup=await req('/api/projects','POST',{name:'IA backup'}),aiBackupPath='/api/projects/'+aiBackup.data.id;
const aiBackupState=structuredClone(aiSaved.state);delete aiBackupState.articles[0].triage;delete aiBackupState.articles[0].fulltext;delete aiBackupState.articles[0].finalReview;
assert.equal((await req(aiBackupPath,'PATCH',{revision:0,action:'restore',state:aiBackupState,expected:[],hash:'ai-backup'})).status,200);
assert.equal((await req(aiBackupPath,'PATCH',{revision:1,action:'finishImport'})).status,200);
let aiBackupSaved=(await req(aiBackupPath)).data;assert.equal(aiBackupSaved.state.articles[0].aiAnalyses[0].summary,'Resumo sintético da IA');
const acceptedAI=await req(aiBackupPath,'PATCH',{revision:2,action:'acceptAITriageBulk',choices:[{article:'article',analysis:aiBackupSaved.state.articles[0].aiAnalyses[0].id}]});assert.equal(acceptedAI.status,200,JSON.stringify(acceptedAI));assert.equal(acceptedAI.data.state.articles[0].triage.source,'ia_accepted');assert.equal(acceptedAI.data.state.articles[0].triage.decision,'excluir');
assert.equal((await req(restored,'PATCH',{revision:aiSaved.revision,action:'removeAI',selector:{scope:'article',article:'article',analysis:aiSaved.state.articles[0].aiAnalyses[0].id}},'test-b')).status,404);
assert.equal((await req(restored,'PATCH',{revision:aiSaved.revision,action:'removeAI',selector:{scope:'article',article:'article',analysis:aiSaved.state.articles[0].aiAnalyses[0].id}})).status,200);
saved=(await req(restored)).data;assert.equal(saved.state.articles[0].aiAnalyses.length,0);assert.equal(saved.state.articles[0].finalReview.decision,'incluir');assert.equal(saved.documents.length,1);
// Protocol planner must persist each phase and only apply approved PCC before triage.
const plannerCode=stripTypeScriptTypes(readFileSync('lib/protocol-planner.ts','utf8')).replace("'./ai-analysis'",JSON.stringify(aiUrl));
const plannerLib=await import('data:text/javascript;base64,'+Buffer.from(plannerCode).toString('base64'));
const plannerCreated=await req('/api/projects','POST',{name:'Plano por etapas'}),plannerPath='/api/projects/'+plannerCreated.data.id;
let plannerProject=(await req(plannerPath)).data,plan=plannerLib.newPlanner(plannerProject.state.protocol);
assert.equal((await req(plannerPath,'PATCH',{revision:0,action:'planner',planner:plan})).status,200);
assert.equal((await req(plannerPath,'PATCH',{revision:1,action:'plannerApply',stage:'triagem',planner:plan})).status,400);
for(const it of plan.stages.pcc.items){if(it.kind==='objetivo')it.text='Objetivo de teste';if(it.kind==='pergunta')it.text='Pergunta de teste';if(it.kind==='populacao')it.text='População';if(it.kind==='conceito')it.text='Conceito';if(it.kind==='contexto')it.text='Contexto';}plan.stages.pcc.items.push(...['inclusao','exclusao'].map(kind=>({id:crypto.randomUUID(),kind,text:kind,criterion:'',yes:'',no:'',unknown:'',example:'',reason:'',status:'rascunho'})));
for(const it of plan.stages.pcc.items){it.status='aprovado';it.approvedHash=plannerLib.itemHash(it)}
let planRes=await req(plannerPath,'PATCH',{revision:1,action:'plannerApply',stage:'pcc',planner:plan});assert.equal(planRes.status,200,JSON.stringify(planRes));assert.equal(planRes.data.state.protocol.approved,false);assert.equal(planRes.data.state.protocol.version,1);assert.ok(planRes.data.state.planner.stages.triagem.items.length>=5);assert.ok(planRes.data.state.planner.stages.triagem.items.every(x=>x.status==='rascunho'));
plan=planRes.data.state.planner;plan.stages.triagem.items=[{id:crypto.randomUUID(),kind:'questao',text:'É elegível?',criterion:'População',yes:'Sim',no:'Não',unknown:'Sem evidência',example:'Caso limítrofe',reason:'',status:'rascunho'}];plan.stages.triagem.items[0].status='aprovado';plan.stages.triagem.items[0].approvedHash=plannerLib.itemHash(plan.stages.triagem.items[0]);
planRes=await req(plannerPath,'PATCH',{revision:2,action:'plannerApply',stage:'triagem',planner:plan});assert.equal(planRes.status,200,JSON.stringify(planRes));assert.equal(planRes.data.state.protocol.approved,true);assert.equal((await req(plannerPath)).data.state.planner.history.length,2);
let cleared=await req(plannerPath,'PATCH',{revision:3,action:'clearStage',stage:'protocol'});assert.equal(cleared.status,200,JSON.stringify(cleared));assert.equal(cleared.data.state.protocol.population,'');assert.equal(cleared.data.state.protocol.approved,false);assert.equal(cleared.data.state.protocol.version,3);assert.equal(cleared.data.state.planner.stages.pcc.items.length,5);
const queue=Array.from({length:500},(_,i)=>'10.1234/item'+i).join('\n');assert.equal((await req(restored+'/search','POST',{action:'queue',text:queue})).status,200);assert.equal((await req(restored)).data.searches.length,500);
if(process.env.ORBIS_REAL_SOURCE_TEST==='1'){
 const doi='10.1371/journal.pone.0265123';await req(path+'/search','POST',{action:'queue',text:doi});let real=await req(path+'/search','POST',{doi});console.log(JSON.stringify({realLookup:real.status,title:real.data.title,candidates:real.data.pdfUrls?.length,issues:real.data.sourceIssues}));assert.equal(real.status,200);assert.ok(real.data.found,'Teste externo inconclusivo: bases não acessíveis neste ambiente');let rp=(await req(path)).data;await req(path,'PATCH',{revision:rp.revision,action:'incorporate',doi});rp=(await req(path)).data;const pdf=await req(path+'/pdf','POST',{article:rp.state.articles[0].id});console.log(JSON.stringify({realDownload:pdf.status,result:pdf.data}));
}
assert.equal((await req(restored,'DELETE',{revision:saved.revision,confirmation:'wrong'})).status,409);assert.equal((await req(restored,'DELETE',{revision:saved.revision,confirmation:'Restaurar teste'})).status,200);assert.equal((await req(restored)).status,404);
console.log('PASS: Worker real, D1/R2 locais, autenticação, isolamento, origem, conflito de edição, importação com retomada, PDF privado, 500 DOIs persistidos, triagem, PCC, adjudicação e revisão final.');
}finally{await mf.dispose()}
