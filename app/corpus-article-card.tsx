'use client';
import {useState} from 'react';
import {Button} from '@/components/ui/button';
import {motorNote,textoExt} from '@/lib/motor-download';
import {AlertDialog,AlertDialogAction,AlertDialogCancel,AlertDialogContent,AlertDialogDescription,AlertDialogFooter,AlertDialogHeader,AlertDialogTitle} from '@/components/ui/alert-dialog';
import {DOCUMENT_TYPES,effectiveDocumentRoute,effectiveDocumentTypeId,suggestedDocumentTypeId,documentTypeLabel,titleIndicatesStudyProtocol} from '@/lib/document-type';

export default function CorpusArticleCard({article,documents,projectId,protocol,disabled,attemptReason,onStorePdf,onEditAbstract,onRoute,onRemove}:any){
 const [confirming,setConfirming]=useState(false);
 const suggestedType=suggestedDocumentTypeId(article),hasAutomaticType=!!article.documentType?.detected||titleIndicatesStudyProtocol(article.title);
 const [type,setType]=useState(effectiveDocumentTypeId(article)),[decision,setDecision]=useState(article.documentType?.humanDecision||effectiveDocumentRoute(article,protocol)),[routeReason,setRouteReason]=useState(article.documentType?.humanReason||'');
 const pdfs=documents.filter((d:any)=>d.article===article.id);
 const texto='/api/projects/'+projectId+'/texto?article='+encodeURIComponent(article.id);
 return <section className="panel article">
  <div><h2>{article.title}</h2><p>{article.authors} {article.year&&'· '+article.year}</p><small>{article.doi||article.filename}</small>{article.legacyDecision&&<details><summary>Decisões preservadas do ORBIS local</summary><pre className="proposal">{JSON.stringify(article.legacyDecision,null,2)}</pre></details>}</div>
  <div className="actions">
   {pdfs.map((d:any)=><a className="text-link" key={d.id} href={'/api/projects/'+projectId+'/pdf?document='+d.id}>Baixar PDF ({(d.size/1024/1024).toFixed(1)} MB)</a>)}
   {article.texto?.key&&<><a className="text-link" target="_blank" rel="noopener noreferrer" href={texto}>Abrir texto</a><a className="text-link" download={(article.filename||'artigo').replace(/\.pdf$/i,'')+'.'+textoExt(article)} href={texto}>Baixar .{textoExt(article)}</a></>}
   <Button disabled={disabled||!article.doi} variant="outline" onClick={()=>onStorePdf(article)}>Buscar e salvar PDF</Button>
   <label className="upload">Anexar PDF<input type="file" accept="application/pdf" disabled={disabled} onChange={e=>{if(e.target.files?.[0])onStorePdf(article,e.target.files[0]);e.currentTarget.value=''}}/></label>
   <Button variant="ghost" disabled={disabled} onClick={()=>onEditAbstract(article)}>Conferir resumo</Button>
   <Button variant="destructive" disabled={disabled} onClick={()=>setConfirming(true)}>Excluir do corpus</Button>
  </div>
  <div className="document-routing"><div className="section-top"><div><strong>Tipo documental e encaminhamento</strong><p>{hasAutomaticType?<>Sugestão automática: {documentTypeLabel(suggestedType)} · confiança {suggestedType==='study_protocol'||article.documentType?.confidence==='high'?'alta':article.documentType?.confidence==='moderate'?'moderada':'baixa'}</>:'Classificação automática ainda não disponível. Anexe ou atualize o PDF.'}</p></div><span className={'assessment-status status-'+(decision==='include'?'incluir':decision==='exclude'?'excluir':'pending')}>{decision==='include'?'Vai para a triagem':decision==='exclude'?'Não vai para a triagem':'Aguardando decisão'}</span></div><div className="document-routing-controls"><label>Tipo confirmado<select value={type} disabled={disabled} onChange={e=>setType(e.target.value)}>{DOCUMENT_TYPES.map(x=><option key={x.id} value={x.id}>{x.label}</option>)}</select></label><label>Decisão<select value={decision} disabled={disabled} onChange={e=>setDecision(e.target.value)}><option value="include">Encaminhar à triagem</option><option value="exclude">Não encaminhar</option><option value="review">Decidir manualmente</option></select></label><label>Justificativa<input value={routeReason} disabled={disabled} onChange={e=>setRouteReason(e.target.value)} placeholder="Motivo da confirmação ou alteração"/></label><Button variant="outline" disabled={disabled} onClick={()=>onRoute(article,{type,decision,reason:routeReason})}>Confirmar classificação</Button></div>{article.documentType?.evidence&&<small>{suggestedType==='study_protocol'&&article.documentType.detected!=='study_protocol'?'O título identifica este documento como protocolo de estudo ou revisão.':article.documentType.evidence}</small>}</div>
  {article.texto?.key&&<p className="muted">Texto extraído: {article.texto.formato==='markdown'?'Markdown':'texto simples'}{article.texto.paginas?' · '+article.texto.paginas+' página(s)':''}{article.texto.truncado?' · cortado no limite de tamanho':''}{article.source?.avisoExtracao?' · '+article.source.avisoExtracao:''}</p>}
  <p className="muted">{motorNote(article)||attemptReason||(article.source?.kind==='local'?'PDF local pendente. Selecione o mesmo arquivo novamente em Importar do computador.':'Download ainda não realizado.')} Confira se o arquivo corresponde ao artigo antes de avaliar o texto completo.</p>
  <AlertDialog open={confirming} onOpenChange={setConfirming}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>Excluir este artigo do corpus?</AlertDialogTitle><AlertDialogDescription>O artigo “{article.title}”, seus PDFs, decisões de triagem, pareceres PCC e análises de IA serão removidos deste projeto. Os demais artigos e o protocolo serão preservados. Esta ação só poderá ser recuperada por um backup anterior.</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel disabled={disabled}>Cancelar</AlertDialogCancel><AlertDialogAction disabled={disabled} onClick={()=>onRemove(article)}>Excluir artigo e arquivos</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
 </section>
}
