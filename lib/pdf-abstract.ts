import {getDocumentProxy} from 'unpdf';
import {cleanAbstract} from './article-discovery';
import {classifyDocumentType} from './document-type';

function tidyPdfText(value:string){
 return value.replace(/\r/g,'').replace(/([A-Za-zÀ-ÿ])-[ \t]*\n[ \t]*([a-zà-ÿ]+)/g,(_,a,next)=>a+(/^(?:and|or)$/i.test(next)?'-':'')+next).replace(/[ \t]+/g,' ').replace(/ *\n */g,'\n').replace(/\n{3,}/g,'\n\n').trim();
}

export function abstractFromFirstPages(text:string){
 const source=tidyPdfText(text).slice(0,80000);
 const lineNumber='(?:\\d{1,3}(?:\\s+|\\s*\\n\\s*))?';
 const endMarker=`\\n\\s*${lineNumber}(?:(?:key\\s*words?|keywords?|palavras[ -]chave|index terms?)\\s*(?::|a{2})?|doi\\s*:|(?:i|1)[.\\s]+(?:introduction|introdução)|introduction|introdução)\\b`;
 const explicit=source.match(new RegExp(`(?:^|\\n)\\s*${lineNumber}(?:abstract|a\\s+b\\s+s\\s+t\\s+r\\s+a\\s+c\\s+t|summary|s\\s+u\\s+m\\s+m\\s+a\\s+r\\s+y|resumo|r\\s+e\\s+s\\s+u\\s+m\\s+o)\\s*[:.\\-–—]?(?:\\s+\\d{1,3})?\\s*\\n?([\\s\\S]{100,}?)(?=${endMarker})`,'i'));
 // Muitos periódicos omitem a palavra "Abstract" e começam diretamente por
 // Background/Objective/Methods. O bloco só é aceito antes da introdução e
 // precisa conter ao menos dois marcadores estruturados, evitando confundir
 // seções normais do artigo com o resumo.
 const structured=source.match(new RegExp(`(?:^|\\n)\\s*${lineNumber}((?:introduction\\s*:|background(?:\\s+and\\s+(?:objective|aim))?|objective|aims?|purpose)\\s*(?::|a{2})?)\\s*([\\s\\S]{100,}?)(?=\\n\\s*${lineNumber}(?:(?:key\\s*words?|keywords?|palavras[ -]chave|index terms?)\\s*(?::|a{2})?|graphical abstract|(?:i|1)[.\\s]+(?:introduction|introdução)|introduction|introdução)\\b)`,'i'));
 const structuredText=structured&&((structured[0].match(/\b(?:methods?|results?|conclusions?)\s*(?::|a{2})?/gi)||[]).length>=2?structured[1]+' '+structured[2]:'');
 const editorial=source.match(/(?:^|\n)\s*\(?received\b[^\n]{0,500}(?:accepted|revised|published)\b[^\n]*\)?\s*\n([\s\S]{100,}?)(?=\n\s*doi\s*:)/i);
 // Artigos em formato narrativo (por exemplo, alguns Nature Communications)
 // trazem um único parágrafo de síntese entre autores e afiliações, sem rótulo.
 const beforeBody=source.split(/\n\s*(?:(?:i|1)[.\s]+)?(?:introduction|introdução|results?)\b/i)[0];
 const narrative=(beforeBody.split(/\n{2,}/).map(x=>x.replace(/\n+/g,' ').trim()).find(x=>x.length>=350&&x.length<=4000&&/[.!?]\s+[A-ZÀ-Ý]/.test(x)&&!/(?:copyright|creative commons|correspondence|department|university|received|accepted|published|doi\s*:)/i.test(x)&&x.split(/\s+/).length>=55)||'');
 // Em muitos PDFs de Frontiers e periódicos em duas colunas não existe o
 // rótulo Abstract. O resumo fica entre as afiliações e Keywords. Usamos a
 // última linha institucional como limite e exigimos prosa com várias frases.
 const keywordIndex=source.search(new RegExp(endMarker,'i'));let preKeyword='';
 if(keywordIndex>0){const lines=source.slice(0,keywordIndex).split('\n').map(x=>x.trim()).filter(Boolean);let boundary=-1;for(let i=0;i<lines.length;i++)if(/\b(?:department|university|universidade|institute|instituto|school|faculty|faculdade|hospital|centre|center|laborator(?:y|ies)|academy|college)\b/i.test(lines[i]))boundary=i;const candidates=lines.slice(boundary+1).filter(x=>!/^\d{1,3}$/.test(x)&&!/(?:https?:|doi\s*:|orcid|correspondence|received|accepted|published|copyright|creative commons|citation:|edited by|reviewed by)/i.test(x));const first=candidates.findIndex(x=>x.split(/\s+/).length>=5&&!/^(?:special|original|review)\s+(?:article|research|paper)/i.test(x)&&!/(?:Chile|Germany|States|China|Canada|Australia|Brazil|Türkiye|Turkey|Kingdom)\.?$/i.test(x));const prose=candidates.slice(Math.max(0,first)).join(' ');if(prose.length>=300&&prose.length<=8000&&(prose.match(/[.!?](?:\s|$)/g)||[]).length>=2)preKeyword=prose}
 let raw=explicit?.[1]||structuredText||editorial?.[1]||preKeyword||narrative;
 if(raw===narrative&&raw){const prose=raw.match(/\b[A-ZÀ-Ý][a-zà-ÿ]+(?:\s+[a-zà-ÿ][a-zà-ÿ-]+){3,}/);if(prose&&prose.index)raw=raw.slice(prose.index)}
 if(raw){const numbered=raw.split('\n'),density=numbered.filter(x=>/^\s*\d{1,3}(?:\s+|\s*$)|\s+\d{1,3}\s*$/.test(x)).length;if(density>=5)raw=numbered.map(x=>x.replace(/^\s*\d{1,3}(?:\s+|\s*$)/,'').replace(/\s+\d{1,3}\s*$/,'')).join('\n')}
 return cleanAbstract(tidyPdfText(raw).replace(/\b(Objective|Methods?|Results?|Conclusions?)a{2}\b/gi,'$1:').replace(/\n+/g,' '));
}

const abstractHeading=/^(?:abstract|a\s*b\s*s\s*t\s*r\s*a\s*c\s*t|summary|s\s*u\s*m\s*m\s*a\s*r\s*y|resumo|r\s*e\s*s\s*u\s*m\s*o)\s*[:.\-–—]?$/i;
const abstractEnd=/^(?:https?:\/\/(?:dx\.)?doi\.org\/|doi\s*:|\*?\s*corresponding author\b|received\b|keywords?\s*:|palavras[ -]chave\s*:|(?:i|1)[.\s]+(?:introduction|introdução)\b|introduction\b|introdução\b)/i;

function abstractFromItems(items:any[]){
 const start=items.findIndex(item=>typeof item.str==='string'&&abstractHeading.test(item.str.trim()));
 if(start<0)return '';
 const parts:string[]=[];let length=0;
 for(let index=start+1;index<items.length;index++){
  const item=items[index];if(typeof item.str!=='string')continue;
  const value=item.str;
  if(length>=100&&value.trim()==='*'&&items.slice(index+1,index+4).some(next=>typeof next?.str==='string'&&/^\s*corresponding author\b/i.test(next.str)))break;
  if(length>=100&&abstractEnd.test(value.trim()))break;
  parts.push(value);length+=value.length;
  if(item.hasEOL)parts.push('\n');
 }
 return cleanAbstract(tidyPdfText(parts.join('')).replace(/\n+/g,' '));
}

function cleanTitle(value:unknown){
 if(typeof value!=='string')return '';
 const title=value.replace(/<[^>]*>/g,' ').replace(/\s+/g,' ').trim().replace(/\s+[1-9]\d?$/,'').trim();
 if(title.length<12||title.length>1000||/^(?:untitled|sem título|microsoft word|document|article|full text)$/i.test(title))return '';
 return title;
}

function titleKey(value:string){return value.normalize('NFKD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]+/g,' ').trim()}
function titleSimilarity(a:string,b:string){const aa=new Set(titleKey(a).split(' ').filter(x=>x.length>2)),bb=new Set(titleKey(b).split(' ').filter(x=>x.length>2));if(!aa.size||!bb.size)return 0;let shared=0;for(const x of aa)if(bb.has(x))shared++;return shared/Math.min(aa.size,bb.size)}
function faithfulTitle(metadata:string,visible:string){
 if(!visible)return metadata;if(!metadata)return visible;
 // Metadados internos frequentemente contêm o nome do arquivo ou do editor.
 // O título visível vence quando é bibliograficamente compatível; em conflito,
 // conservamos o mais informativo e evitamos substituir por um metadado curto.
 const score=titleSimilarity(metadata,visible);
 if(score>=.72){const mk=titleKey(metadata),vk=titleKey(visible);if(mk.startsWith(vk)&&metadata.length>visible.length)return metadata;if(vk.startsWith(mk)&&visible.length>metadata.length)return visible;return visible}
 if(/(?:microsoft|acrobat|proof|manuscript|untitled|document|full.?text)/i.test(metadata))return visible;
 return visible.split(/\s+/).length>=5&&visible.length>=metadata.length*.65?visible:metadata;
}

function visibleTitle(items:any[]){
 const lines=new Map<number,{text:string;height:number,y:number}>();
 for(const item of items){if(typeof item.str!=='string'||!item.str.trim())continue;const y=Math.round(Number(item.transform?.[5]||0)*2)/2,height=Number(item.height||Math.abs(item.transform?.[3]||0));const row=lines.get(y)||{text:'',height,y};row.text+=(row.text?' ':'')+item.str.trim();row.height=Math.max(row.height,height);lines.set(y,row);}
 const candidates=[...lines.values()].map(x=>({...x,text:cleanTitle(x.text)})).filter(x=>x.text&&!/^(?:doi\b|https?:|www\.|received\b|accepted\b|published\b|copyright\b|©|issn\b)/i.test(x.text)&&!(/^[A-Z\d ,.():-]+$/.test(x.text)&&x.text.length<80)&&!/@/.test(x.text)).sort((a,b)=>b.height-a.height||b.y-a.y);
 const first=candidates[0];if(!first)return '';
 const continuation=candidates.filter(x=>x!==first&&Math.abs(x.height-first.height)<.8&&x.y<first.y&&first.y-x.y<first.height*5).sort((a,b)=>b.y-a.y);
 return cleanTitle([first,...continuation].map(x=>x.text).join(' '));
}

export async function extractPdfDetails(bytes:Uint8Array){
 let pdf:any,timeout:ReturnType<typeof setTimeout>|undefined;
 try{
  pdf=await Promise.race([getDocumentProxy(bytes.slice()),new Promise<never>((_,reject)=>{timeout=setTimeout(()=>reject(new Error('Tempo limite ao ler o PDF.')),15000)})]);clearTimeout(timeout);
  const pages:string[]=[];let firstItems:any[]=[];
  for(let number=1;number<=Math.min(5,pdf.numPages);number++){
   const page=await pdf.getPage(number),content=await page.getTextContent(),parts:string[]=[];
   if(number===1)firstItems=content.items as any[];
   for(const item of content.items as any[]){if(typeof item.str!=='string')continue;parts.push(item.str);parts.push(item.hasEOL?'\n':' ')}
   pages.push(parts.join(''));page.cleanup?.();
  }
  let metadataTitle='';try{const metadata=await pdf.getMetadata?.();metadataTitle=cleanTitle(metadata?.info?.Title)}catch{}
  const title=faithfulTitle(metadataTitle,visibleTitle(firstItems)),text=pages.join('\n\n');
  let abstract=abstractFromItems(firstItems)||abstractFromFirstPages(text);
  if(title&&titleKey(abstract).startsWith(titleKey(title))){abstract=abstract.slice(title.length).trim();const prose=abstract.match(/\b[A-ZÀ-Ý][a-zà-ÿ]+(?:\s+[a-zà-ÿ][a-zà-ÿ-]+){3,}/);if(prose&&prose.index)abstract=abstract.slice(prose.index)}
  return {title,abstract,documentType:classifyDocumentType(title,text)};
 }finally{if(timeout)clearTimeout(timeout);await pdf?.destroy?.().catch(()=>{});}
}

export async function extractPdfAbstract(bytes:Uint8Array){return (await extractPdfDetails(bytes)).abstract;}
