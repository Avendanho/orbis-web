// Agrupa o lote do Artigo Aberto para a tabela de resultados.
//
// Artigo que entrou pelo motor não tem PDF no R2 (ficou na pasta local ou foi
// descartado), então "incorporado" é ter o artigo no corpus — com PDF no R2
// ou vindo do motor. Com o motor no ar, todo DOI achado pode ser buscado,
// mesmo sem link do Worker.
export function resultGroups(searches:any[],articles:any[],docs:any[],motorOnline:boolean){
 const articleFor=(doi:string)=>articles.find((a:any)=>a.doi===doi);
 const incorporated=(x:any)=>{const a=articleFor(x.doi);return !!a&&(a.source?.kind==='motor'||docs.some((d:any)=>d.article===a.id))};
 const hasLink=(x:any)=>(x.result?.pdfUrls||[]).length>0||(motorOnline&&!!x.result?.found);
 return {
  incorporated:searches.filter(incorporated),
  ready:searches.filter((x:any)=>!incorporated(x)&&!x.error&&x.result?.found&&hasLink(x)),
  failed:searches.filter((x:any)=>!incorporated(x)&&!!x.error&&hasLink(x)),
  missing:searches.filter((x:any)=>x.status==='done'&&!incorporated(x)&&!hasLink(x)),
  pending:searches.filter((x:any)=>x.status!=='done'&&!incorporated(x)),
 };
}
