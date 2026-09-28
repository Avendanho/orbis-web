export type DocumentRoute='include'|'exclude'|'review';
export type DocumentTypePolicy={id:string;label:string;decision:DocumentRoute;reason:string};
const STUDY_PROTOCOL_TITLE=/(?:\b(?:study|review|research)\s+protocol\b|\bprotocol\s+(?:for|of)\b.{0,80}\b(?:review|study|research)\b|\bprotocolo\s+(?:de|da|para)\b.{0,80}\b(?:revis[aã]o|estudo|pesquisa)\b)/i;
export const DOCUMENT_TYPES=[
 {id:'study_protocol',label:'Protocolo de estudo ou revisão',patterns:[STUDY_PROTOCOL_TITLE]},
 {id:'systematic_review',label:'Revisão sistemática',patterns:[/systematic review|revisão sistemática/i]},
 {id:'meta_analysis',label:'Metanálise',patterns:[/meta[- ]analysis|metanálise|meta análise/i]},
 {id:'scoping_review',label:'Revisão de escopo',patterns:[/scoping review|revisão de escopo/i]},
 {id:'narrative_review',label:'Revisão narrativa',patterns:[/narrative review|revisão narrativa|literature review/i]},
 {id:'guideline_consensus',label:'Diretriz ou consenso',patterns:[/clinical practice guideline|practice guideline|consensus statement|diretriz|consenso/i]},
 {id:'poster',label:'Pôster ou resumo de congresso',patterns:[/\bposter\b|conference abstract|congress abstract|resumo de congresso|anais de congresso/i]},
 {id:'dissertation',label:'Dissertação',patterns:[/master'?s dissertation|master'?s thesis|dissertação/i]},
 {id:'thesis',label:'Tese',patterns:[/doctoral thesis|phd thesis|tese de doutorado|\bthesis\b/i]},
 {id:'patent',label:'Patente',patterns:[/\bpatent\b|\bpatente\b|international publication number|inventor\(s\)/i]},
 {id:'editorial',label:'Editorial, carta ou comentário',patterns:[/^editorial\b|\beditorial\b|letter to the editor|commentary|carta ao editor/i]},
 {id:'book',label:'Livro ou capítulo de livro',patterns:[/book chapter|chapter in|capítulo de livro|\bisbn\b/i]},
 {id:'preprint',label:'Preprint',patterns:[/\bpreprint\b|not peer reviewed/i]},
 {id:'case_report',label:'Relato ou série de casos',patterns:[/case report|case series|relato de caso|série de casos/i]},
 {id:'original_article',label:'Artigo científico original',patterns:[/original article|research article|original research/i]},
 {id:'other',label:'Outro ou não identificado',patterns:[]},
] as const;
export function defaultDocumentTypePolicies():DocumentTypePolicy[]{const decisions:Record<string,DocumentRoute>={study_protocol:'review',systematic_review:'include',meta_analysis:'include',scoping_review:'include',narrative_review:'include',guideline_consensus:'include',original_article:'include',case_report:'include',poster:'review',dissertation:'review',thesis:'review',patent:'exclude',editorial:'exclude',book:'review',preprint:'review',other:'review'};return DOCUMENT_TYPES.map(x=>({id:x.id,label:x.label,decision:decisions[x.id]||'review',reason:''}));}
export function normalizedPolicies(value:any):DocumentTypePolicy[]{const current=Array.isArray(value)?value:[];return defaultDocumentTypePolicies().map(base=>{const found=current.find((x:any)=>x?.id===base.id);return found?{...base,decision:['include','exclude','review'].includes(found.decision)?found.decision:base.decision,reason:String(found.reason||'').slice(0,2000)}:base});}
export function titleIndicatesStudyProtocol(title:string=''){return STUDY_PROTOCOL_TITLE.test(title.replace(/\s+/g,' ').trim())}
export function suggestedDocumentTypeId(article:any){return titleIndicatesStudyProtocol(article?.title)?'study_protocol':article?.documentType?.detected||'other'}
export function effectiveDocumentTypeId(article:any){return article?.documentType?.humanType||suggestedDocumentTypeId(article)}
export function classifyDocumentType(title:string='',text:string=''){const cleanTitle=title.replace(/\s+/g,' ').trim(),source=(cleanTitle+'\n'+text.slice(0,50000)).replace(/\s+/g,' ');if(titleIndicatesStudyProtocol(cleanTitle)){const type=DOCUMENT_TYPES.find(x=>x.id==='study_protocol')!;return {detected:type.id,label:type.label,confidence:'high',evidence:'O título identifica este documento como protocolo de estudo ou revisão.',source:'Título do PDF ou metadados',detectedAt:new Date().toISOString()};}for(const type of DOCUMENT_TYPES){if(type.id==='other'||type.id==='study_protocol')continue;if(type.patterns.some(pattern=>pattern.test(source)))return {detected:type.id,label:type.label,confidence:/^(?:systematic_review|meta_analysis|scoping_review|poster|dissertation|thesis|patent)$/.test(type.id)?'high':'moderate',evidence:'Identificado no título, metadados ou primeiras páginas do PDF.',source:'PDF e metadados',detectedAt:new Date().toISOString()};}const looksArticle=/\babstract\b|\bintroduction\b|\bmethods?\b|\bresults?\b|\bdiscussion\b/i.test(source),type=DOCUMENT_TYPES.find(x=>x.id===(looksArticle?'original_article':'other'))!;return {detected:type.id,label:type.label,confidence:looksArticle?'moderate':'low',evidence:looksArticle?'Estrutura compatível com artigo científico.':'Não foram localizados indicadores suficientes para definir o tipo.',source:'PDF e metadados',detectedAt:new Date().toISOString()};}
export function effectiveDocumentRoute(article:any,protocol:any):DocumentRoute{if(article.documentType?.humanDecision)return article.documentType.humanDecision;const inferredFromTitle=titleIndicatesStudyProtocol(article?.title);if(!article.documentType?.humanType&&!article.documentType?.detected&&!inferredFromTitle)return 'include';const type=effectiveDocumentTypeId(article);return normalizedPolicies(protocol?.documentTypes).find(x=>x.id===type)?.decision||'review';}
export function documentTypeLabel(id:string){return DOCUMENT_TYPES.find(x=>x.id===id)?.label||'Outro ou não identificado'}
