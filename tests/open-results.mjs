import {readFile} from 'node:fs/promises';import {stripTypeScriptTypes} from 'node:module';import assert from 'node:assert/strict';
const src=stripTypeScriptTypes(await readFile('lib/open-results.ts','utf8'));
const r=await import('data:text/javascript;base64,'+Buffer.from(src).toString('base64'));
const row=(doi,result,extra={})=>({doi,status:'done',error:null,result:{doi,found:true,pdfUrls:[],...result},...extra});
const searches=[
 row('10.1/motor-inc',{motor:{ok:true}}),        // incorporado pelo motor, sem PDF no R2
 row('10.1/worker-inc',{pdfUrls:['u']}),         // incorporado pelo Worker, com PDF no R2
 row('10.1/sem-link',{}),                        // achado, sem link do Worker
 row('10.1/motor-falhou',{motor:{ok:false}},{error:'Nenhuma fonte entregou o PDF.'}),
 row('10.1/pendente',{},{status:'waiting',result:null}),
];
const articles=[{id:'a1',doi:'10.1/motor-inc',source:{kind:'motor'}},{id:'a2',doi:'10.1/worker-inc'}],docs=[{article:'a2'}];
const nomes=g=>Object.fromEntries(Object.entries(g).map(([k,v])=>[k,v.map(x=>x.doi)]));
// Sem motor: comportamento de antes, exceto que o artigo do motor conta como incorporado.
assert.deepEqual(nomes(r.resultGroups(searches,articles,docs,false)),{
 incorporated:['10.1/motor-inc','10.1/worker-inc'],ready:[],failed:[],missing:['10.1/sem-link','10.1/motor-falhou'],pending:['10.1/pendente']});
// Com motor: todo DOI achado pode ser buscado pelo motor.
assert.deepEqual(nomes(r.resultGroups(searches,articles,docs,true)),{
 incorporated:['10.1/motor-inc','10.1/worker-inc'],ready:['10.1/sem-link'],failed:['10.1/motor-falhou'],missing:[],pending:['10.1/pendente']});
console.log('open-results: ok');
