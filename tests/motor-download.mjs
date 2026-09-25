import {readFile} from 'node:fs/promises';import {stripTypeScriptTypes} from 'node:module';import assert from 'node:assert/strict';
const src=stripTypeScriptTypes(await readFile('lib/motor-download.ts','utf8'));
const md=await import('data:text/javascript;base64,'+Buffer.from(src).toString('base64'));

assert.ok(md.isMode('baixar')&&md.isMode('analisar')&&!md.isMode('tudo')&&!md.isMode(undefined));

const k1=await md.textKey('p1','10.1/ABC');
assert.equal(k1,await md.textKey('p1','10.1/abc'),'DOI sem diferença de caixa');
assert.match(k1,/^p1\/texto\/[0-9a-f]{32}\.txt$/);
assert.notEqual(k1,await md.textKey('p2','10.1/abc'));

const okRes={ok:true,fonte:'pmc',fontes_tentadas:['pmc'],arquivo:'X.pdf',identidade:{ok:true,metodo:'doi_in_pdf',score:1,detalhe:'identidade confirmada'},texto:'abc',paginas:2,chars:3,texto_truncado:false};
let s=md.motorSummary(okRes,'analisar',{key:'k',bytes:3});
assert.equal(s.ok,true);assert.equal(s.arquivo,undefined,'modo analisar não guarda nome de arquivo');
assert.deepEqual(s.texto,{key:'k',bytes:3,chars:3,paginas:2,truncado:false});
s=md.motorSummary(okRes,'baixar',null);
assert.equal(s.arquivo,'X.pdf');assert.equal(s.texto,null);
s=md.motorSummary({ok:false,erro:'e'.repeat(5000),fontes_tentadas:['a']},'baixar',null);
assert.equal(s.ok,false);assert.equal(s.erro.length,3000);assert.deepEqual(s.fontes,['a']);

const meta={title:'T',authors:'A',year:'2021',abstract:'R',motor:md.motorSummary(okRes,'baixar',{key:'k',bytes:3})};
const art=md.motorArticle('10.1/a',meta,'id1','artigo_1_10.1_a.pdf','2026-01-01T00:00:00Z');
assert.equal(art.id,'id1');assert.equal(art.filename,'artigo_1_10.1_a.pdf');assert.equal(art.title,'T');
assert.equal(art.source.kind,'motor');assert.equal(art.source.modo,'baixar');assert.equal(art.source.arquivoLocal,'X.pdf');
assert.equal(art.identity.ok,true);assert.equal(art.identity.method,'conteudo_pdf:doi_in_pdf');
assert.deepEqual(art.texto,{key:'k',bytes:3,chars:3,paginas:2,truncado:false,origem:'motor'});

assert.match(md.motorNote(art),/pasta local: X\.pdf/);
assert.match(md.motorNote({...art,source:{kind:'motor',modo:'analisar'}}),/lido e descartado; texto disponível/);
assert.match(md.motorNote({...art,texto:undefined}),/Sem texto extraído/);
assert.equal(md.motorNote({source:{kind:'local'}}),'');

const rows2=[{result:JSON.stringify({motor:{texto:{key:'p1/texto/k1',bytes:10}}})},{result:JSON.stringify({motor:{texto:{key:'p1/texto/k2',bytes:5}}})},{result:JSON.stringify({motor:{texto:{key:'OUTRO/texto/k3',bytes:7}}})},{result:null},{result:JSON.stringify({found:true})}];
assert.deepEqual(md.orphanTexts(rows2,[{texto:{key:'p1/texto/k1'}},{}],'p1'),{keys:['p1/texto/k2'],bytes:5},'chave de outro projeto nunca é apagada');
assert.equal(md.ownKey('p1','p1/texto/x.txt'),true);assert.equal(md.ownKey('p1','p10/texto/x.txt'),false);assert.equal(md.ownKey('p1',undefined),false);
console.log('motor-download: ok');
