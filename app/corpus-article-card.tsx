'use client';
import {useState} from 'react';
import {Button} from '@/components/ui/button';
import {motorNote} from '@/lib/motor-download';
import {AlertDialog,AlertDialogAction,AlertDialogCancel,AlertDialogContent,AlertDialogDescription,AlertDialogFooter,AlertDialogHeader,AlertDialogTitle} from '@/components/ui/alert-dialog';

export default function CorpusArticleCard({article,documents,projectId,disabled,attemptReason,onStorePdf,onEditAbstract,onRemove}:any){
 const [confirming,setConfirming]=useState(false);
 const pdfs=documents.filter((d:any)=>d.article===article.id);
 return <section className="panel article">
  <div><h2>{article.title}</h2><p>{article.authors} {article.year&&'· '+article.year}</p><small>{article.doi||article.filename}</small>{article.legacyDecision&&<details><summary>Decisões preservadas do ORBIS local</summary><pre className="proposal">{JSON.stringify(article.legacyDecision,null,2)}</pre></details>}</div>
  <div className="actions">
   {pdfs.map((d:any)=><a className="text-link" key={d.id} href={'/api/projects/'+projectId+'/pdf?document='+d.id}>Baixar PDF ({(d.size/1024/1024).toFixed(1)} MB)</a>)}
   <Button disabled={disabled||!article.doi} variant="outline" onClick={()=>onStorePdf(article)}>Buscar e salvar PDF</Button>
   <label className="upload">Anexar PDF<input type="file" accept="application/pdf" disabled={disabled} onChange={e=>{if(e.target.files?.[0])onStorePdf(article,e.target.files[0]);e.currentTarget.value=''}}/></label>
   <Button variant="ghost" disabled={disabled} onClick={()=>onEditAbstract(article)}>Conferir resumo</Button>
   <Button variant="destructive" disabled={disabled} onClick={()=>setConfirming(true)}>Excluir do corpus</Button>
  </div>
  <p className="muted">{motorNote(article)||attemptReason||(article.source?.kind==='local'?'PDF local pendente. Selecione o mesmo arquivo novamente em Importar do computador.':'Download ainda não realizado.')} Confira se o arquivo corresponde ao artigo antes de avaliar o texto completo.</p>
  <AlertDialog open={confirming} onOpenChange={setConfirming}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>Excluir este artigo do corpus?</AlertDialogTitle><AlertDialogDescription>O artigo “{article.title}”, seus PDFs, decisões de triagem, pareceres PCC e análises de IA serão removidos deste projeto. Os demais artigos e o protocolo serão preservados. Esta ação só poderá ser recuperada por um backup anterior.</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel disabled={disabled}>Cancelar</AlertDialogCancel><AlertDialogAction disabled={disabled} onClick={()=>onRemove(article)}>Excluir artigo e arquivos</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
 </section>
}
