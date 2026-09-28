import {readFile} from 'node:fs/promises';
import {stripTypeScriptTypes} from 'node:module';
import assert from 'node:assert/strict';

const source=stripTypeScriptTypes(await readFile('lib/document-type.ts','utf8'));
const moduleUrl='data:text/javascript;base64,'+Buffer.from(source).toString('base64');
const types=await import(moduleUrl);

const articleTitle='Variability in quantitative parameters of instrumental swallowing assessments: a scoping review protocol';
assert.equal(types.classifyDocumentType(articleTitle,'Abstract Methods Final considerations').detected,'study_protocol');
assert.equal(types.classifyDocumentType('Protocolo de estudo para uma pesquisa multicêntrica','Resumo Introdução Métodos').detected,'study_protocol');
assert.equal(types.classifyDocumentType('A systematic review of swallowing assessments','The authors mention a study protocol in the discussion.').detected,'systematic_review');
assert.equal(types.defaultDocumentTypePolicies().find(x=>x.id==='study_protocol').decision,'review');

const legacy={title:articleTitle,documentType:{detected:'scoping_review'}};
assert.equal(types.suggestedDocumentTypeId(legacy),'study_protocol');
assert.equal(types.effectiveDocumentTypeId(legacy),'study_protocol');
assert.equal(types.effectiveDocumentRoute(legacy,{documentTypes:[] }),'review');
assert.equal(types.effectiveDocumentRoute({...legacy,documentType:{...legacy.documentType,humanDecision:'include'}},{documentTypes:[]}),'include');
assert.equal(types.effectiveDocumentRoute({title:'Artigo sem classificação'},{documentTypes:[]}),'include');

console.log('PASS: protocolos identificados pelo título, projetos existentes corrigidos e encaminhamento manual preservado.');
