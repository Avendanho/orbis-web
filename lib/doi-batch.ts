export const MAX_DOIS = 500;
export type Article = { doi:string; abstract?:string; title:string; authors:string; year:string; journal:string; pdf:string|null; pdfUrls?:string[]; source:string; partial:boolean; found:boolean; reasonCode?:string; reason?:string; reasonDetail?:string; freeUrl?:string; sourceIssues?:string[]; pageDoi?:string };
export type BatchItem = { doi:string; state:'waiting'|'searching'|'done'|'error'; article?:Article; error?:string };
export function normalizeDoi(value:string) {
 let doi=value.trim().replace(/^doi:\s*/i,'').replace(/^https?:\/\/(?:dx\.)?doi\.org\//i,'');
 try { doi=decodeURIComponent(doi); } catch {}
 return doi.trim();
}
export function parseDois(text:string) {
 const lines=text.split(/\r?\n/).map(line=>line.trim()).filter(Boolean);
 const seen=new Set<string>(); const dois:string[]=[]; const invalid:number[]=[]; let duplicates=0;
 text.split(/\r?\n/).forEach((line,index)=>{
  if(!line.trim())return;
  const doi=normalizeDoi(line);
  if(doi.length>500||!/^10\.\d{4,9}\/\S+$/i.test(doi)){invalid.push(index+1);return;}
  const key=doi.toLowerCase();
  if(seen.has(key)){duplicates++;return;}
  seen.add(key);dois.push(doi);
 });
 return {dois,invalid,duplicates,lineCount:lines.length,overLimit:dois.length>MAX_DOIS};
}
// Keep the batch in the browser and send only two requests at a time.
// Pausing finishes active requests without losing completed results.
export async function runBatch(items:BatchItem[], lookup:(doi:string)=>Promise<Article>, update:(index:number,item:BatchItem)=>void, shouldPause:()=>boolean) {
 let next=0;
 async function worker() {
  while(!shouldPause()) {
   const index=next++;
   if(index>=items.length)return;
   const item=items[index];
   if(item.state!=='waiting')continue;
   update(index,{doi:item.doi,state:'searching'});
   try { const article=await lookup(item.doi);update(index,{doi:item.doi,state:'done',article}); }
   catch(e) { update(index,{doi:item.doi,state:'error',error:e instanceof Error?e.message:'Não foi possível buscar este DOI. Tente novamente.'}); }
  }
 }
 await Promise.all([worker(),worker()]);
}
