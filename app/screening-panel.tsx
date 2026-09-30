'use client';
import {useRef,useState} from 'react';
import {toast} from 'sonner';
import {Button} from '@/components/ui/button';import {Input} from '@/components/ui/input';import {Badge} from '@/components/ui/badge';
import {Table,TableHeader,TableBody,TableRow,TableHead,TableCell} from '@/components/ui/table';
import {TriageForm} from './assessment-workspace';
import {listar,registroDe,situacao,sugestaoAtual,incluidosParaBaixar,obtencao,type Filtro} from '@/lib/screening';

// Triagem de títulos e resumos antes do download. Os registros vêm do lote do
// Artigo Aberto (título e resumo já buscados na web); só os incluídos são
// baixados e entram no corpus, levando a decisão junto.
async function api(url:string,data:any){const r=await fetch(url,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(data)});const x:any=await r.json();if(!r.ok)throw Object.assign(Error(x.message),{status:r.status});return x}
const FILTROS:[Filtro,string][]=[['pendente','Pendentes'],['incluir','Incluídos'],['excluir','Excluídos'],['nao_obtido','PDF não obtido'],['sem_resumo','Sem resumo'],['todos','Todos']];
const OBTENCAO:Record<string,string>={no_corpus:'no corpus',aguardando:'aguardando download',nao_obtido:'PDF não obtido'};
const ROTULO:Record<string,string>={pendente:'Pendente',incluir:'Incluído',excluir:'Excluído'};
const RESPOSTA:Record<string,string>={sim:'Sim',nao:'Não',indeterminado:'Indeterminado'};

export default function ScreeningPanel({project,busy,running,refresh,baixarIncluidos,goSearch}:any){
 const protocolo=project.state.protocol,base='/api/projects/'+project.id+'/screening';
 const podeEditar=['owner','editor'].includes(project.access_role),podeTriar=podeEditar||project.access_role==='reviewer';
 const [filtro,setFiltro]=useState<Filtro>('pendente'),[busca,setBusca]=useState(''),[pagina,setPagina]=useState(1),[doi,setDoi]=useState('');
 const [ia,setIa]=useState(false),[progresso,setProgresso]=useState(''),[ocupado,setOcupado]=useState(false),[dirty,setDirty]=useState(false);
 const parar=useRef(false);
 const linhas=project.screening||[],searches=project.searches||[];
 const lista=listar(searches,linhas,protocolo,{filtro,busca,pagina,articles:project.state.articles});
 const porDoi=new Map(linhas.map((l:any)=>[l.doi,l]));
 const registros=searches.map(registroDe).filter(Boolean) as any[];
 const comSugestao=registros.filter(r=>{const l:any=porDoi.get(r.doi);return situacao(l,protocolo.version)==='pendente'&&sugestaoAtual(l?.ai,r,protocolo)}).map(r=>r.doi);
 const aBaixar=incluidosParaBaixar(linhas,protocolo.version,project.state.articles);
 const bloqueado=busy||running||ocupado||ia;

 const sel=(()=>{const reg=registros.find(r=>r.doi===doi);if(!reg)return null;const linha:any=porDoi.get(reg.doi)||null;
  const s=situacao(linha,protocolo.version);
  return {registro:reg,linha,situacao:s,sugestao:linha?.ai&&sugestaoAtual(linha.ai,reg,protocolo)?linha.ai:null,
   obtencao:s==='incluir'?obtencao(searches.find((r:any)=>r.doi===reg.doi),project.state.articles):null}})();

 function abrir(d:string){if(dirty&&d!==doi&&!window.confirm('Há alterações não salvas na ficha atual. Descartar?'))return;setDirty(false);setDoi(d)}
 async function agir(fn:()=>Promise<void>){if(ocupado)return;setOcupado(true);try{await fn()}catch(e:any){toast.error(e.message)}finally{setOcupado(false)}}
 // `TriageForm` chama save('triage', {article, answers, reasons, reason}); aqui o "artigo" é o DOI.
 async function decidir(_:string,data:any){
  const ids=lista.itens.map(x=>x.registro.doi),i=ids.indexOf(data.article);let ok=false;
  await agir(async()=>{const r=await api(base,{action:'decide',doi:data.article,answers:data.answers,reasons:data.reasons,reason:data.reason});
   toast.success(r.decision==='incluir'?'Registro incluído: será baixado.':'Registro excluído na triagem.');ok=true;await refresh();
   setDirty(false);setDoi(filtro==='pendente'&&ids[i+1]?ids[i+1]:data.article)});
  return ok;
 }
 const aceitar=(dois:string[])=>agir(async()=>{const r=await api(base,{action:'acceptAI',dois});toast.success(r.applied.length+' sugestão(ões) aceita(s) como decisão.');await refresh()});
 const buscarResumo=(d:string)=>agir(async()=>{const r=await api(base,{action:'refreshAbstract',doi:d});(r.abstractFound?toast.success:toast.info)(r.abstractFound?'Resumo encontrado.':'Nenhuma base trouxe o resumo.');await refresh()});
 async function sugerir(){
  if(bloqueado)return;setIa(true);parar.current=false;let feitos=0;
  try{
   while(!parar.current){
    const r=await api(base,{action:'ai'});feitos+=r.analysed;
    setProgresso(feitos+' registro(s) com sugestão · '+r.remaining+' na fila · '+r.provider+(r.model?' / '+r.model:''));
    if(r.failures?.length)toast.warning(r.failures.map((f:any)=>f.reason).join(' | ').slice(0,500));
    await refresh();
    if(!r.remaining||!r.analysed)break;
   }
   if(parar.current)toast.info('Sugestões pausadas. Retome quando quiser.');
  }catch(e:any){toast.error(e.message)}finally{setIa(false)}
 }

 return <>
  <section className="panel">
   <div className="section-top"><h2>Triagem de títulos e resumos</h2><div className="actions"><Badge variant="secondary">{lista.contagens.incluir} incluídos · {lista.contagens.excluir} excluídos · {lista.contagens.pendente} pendentes</Badge></div></div>
   <p>Cada DOI consultado no Artigo Aberto vira um registro com o título e o resumo buscados na web. Trie aqui; só os incluídos são baixados e entram no corpus, já com a decisão registrada.</p>
   {!protocolo.approved&&<p className="notice">Aprove o PCC e as perguntas de triagem antes de triar.</p>}
   {!lista.contagens.todos&&<div className="notice">Nenhum registro ainda. <Button variant="link" onClick={goSearch}>Consultar DOIs no Artigo Aberto</Button></div>}
   <div className="stats open-result-stats">{FILTROS.map(([f,rotulo])=><button key={f} className={filtro===f?'active':''} onClick={()=>{setFiltro(f);setPagina(1)}}><strong>{lista.contagens[f]}</strong><span>{rotulo}</span></button>)}</div>
   <div className="actions">
    <Input aria-label="Buscar nos registros" placeholder="Buscar por título, autor, DOI, periódico ou resumo" value={busca} onChange={e=>{setBusca(e.target.value);setPagina(1)}}/>
    {podeEditar&&(ia?<Button variant="outline" onClick={()=>{parar.current=true}}>Pausar após o lote atual</Button>
     :<Button variant="outline" disabled={bloqueado||!protocolo.approved||!lista.contagens.pendente} onClick={sugerir}>Sugerir com IA</Button>)}
    {podeTriar&&<Button variant="outline" disabled={bloqueado||!protocolo.approved||!comSugestao.length} onClick={()=>{if(window.confirm('Aceitar '+comSugestao.length+' sugestão(ões) da IA como decisão? Confira uma amostra antes.'))aceitar(comSugestao.slice(0,500))}}>Aceitar {comSugestao.length} sugestão(ões)</Button>}
    {podeEditar&&<Button disabled={bloqueado||!aBaixar.length} onClick={baixarIncluidos}>{running?'Baixando os incluídos…':'Baixar os incluídos ('+aBaixar.length+')'}</Button>}
   </div>
   {progresso&&<p className="muted" role="status">{progresso}</p>}
   <Table><TableHeader><TableRow><TableHead>Registro</TableHead><TableHead>Situação</TableHead><TableHead>IA</TableHead><TableHead>Ação</TableHead></TableRow></TableHeader>
    <TableBody>{lista.itens.map(x=><TableRow key={x.registro.doi} className={x.registro.doi===doi?'selected':''}>
     <TableCell><strong>{x.registro.title||x.registro.doi}</strong><p className="muted">{[x.registro.year,x.registro.journal,x.registro.doi].filter(Boolean).join(' · ')}{!x.registro.abstract.trim()&&' · sem resumo'}</p></TableCell>
     <TableCell><span className={'assessment-status status-'+x.situacao}>{ROTULO[x.situacao]}</span>{x.antiga&&<small className="muted"> decisão de versão anterior</small>}{x.obtencao&&<small className={x.obtencao.estado==='nao_obtido'?'notice':'muted'} title={x.obtencao.motivo||undefined}> {OBTENCAO[x.obtencao.estado]}</small>}</TableCell>
     <TableCell>{x.sugestao?<Badge variant="outline">{x.sugestao.decision==='incluir'?'sugere incluir':'sugere excluir'}</Badge>:'—'}</TableCell>
     <TableCell><Button variant="outline" disabled={busy} onClick={()=>abrir(x.registro.doi)}>{x.situacao==='pendente'?'Triar':'Ver / alterar'}</Button></TableCell>
    </TableRow>)}</TableBody></Table>
   {!lista.itens.length&&!!lista.contagens.todos&&<p className="assessment-empty">Nenhum registro neste filtro.</p>}
   {lista.paginas>1&&<div className="actions"><Button variant="outline" disabled={lista.pagina<=1} onClick={()=>setPagina(lista.pagina-1)}>Anterior</Button><span>Página {lista.pagina} de {lista.paginas} · {lista.total} registro(s)</span><Button variant="outline" disabled={lista.pagina>=lista.paginas} onClick={()=>setPagina(lista.pagina+1)}>Próxima</Button></div>}
  </section>
  {sel&&<section className="panel">
   <div className="section-top"><div><p className="eyebrow">{ROTULO[sel.situacao]}{sel.linha?.decision&&sel.linha.version!==protocolo.version?' · há decisão de versão anterior do protocolo':''}</p><h2>{sel.registro.title||sel.registro.doi}</h2><p>{[sel.registro.authors,sel.registro.year,sel.registro.journal].filter(Boolean).join(' · ')}</p><a href={'https://doi.org/'+sel.registro.doi} target="_blank" rel="noopener noreferrer">{sel.registro.doi}</a></div><Button variant="outline" onClick={()=>abrir('')}>Fechar</Button></div>
   {sel.obtencao?.estado==='nao_obtido'&&<p className="notice">PDF não obtido: {sel.obtencao.motivo} <strong>Baixar os incluídos</strong> tenta de novo. Artigos de assinatura dependem do acesso institucional configurado no motor.</p>}
   {sel.obtencao?.estado==='no_corpus'&&<p className="muted">Já está no corpus, com esta decisão de triagem.</p>}
   <h3>Resumo</h3>
   {sel.registro.abstract.trim()?<><p>{sel.registro.abstract}</p><small className="muted">Fonte: {sel.registro.abstractSource||'—'}</small></>:<p className="notice">Resumo não localizado nas bases consultadas. Decida pelo título ou tente buscar de novo.</p>}
   {podeEditar&&<div className="actions"><Button variant="outline" disabled={bloqueado} onClick={()=>buscarResumo(sel.registro.doi)}>Buscar resumo de novo</Button></div>}
   {sel.sugestao&&<div className="notice"><strong>Sugestão da IA ({sel.sugestao.provider}{sel.sugestao.model?' / '+sel.sugestao.model:''}): {sel.sugestao.decision==='incluir'?'incluir':'excluir'}</strong>
    <ol>{sel.sugestao.questions.map((q:any,i:number)=><li key={i}><strong>{RESPOSTA[q.answer]||q.answer}</strong> — {q.reason}{q.evidence?<em> “{q.evidence}”</em>:null}</li>)}</ol>
    {podeTriar&&sel.situacao==='pendente'&&<Button disabled={bloqueado} onClick={()=>aceitar([sel.registro.doi])}>Aceitar esta sugestão</Button>}
    <p className="muted">A sugestão só vira decisão quando alguém a aceita ou registra a própria triagem.</p></div>}
  </section>}
  {sel&&podeTriar&&<TriageForm key={sel.registro.doi+':'+(sel.linha?.version||0)+':'+(sel.linha?.decision||'')}
   article={{id:sel.registro.doi,triage:sel.situacao!=='pendente'?sel.linha:undefined}} protocol={protocolo}
   guidance={project.state.planner?.stages?.triagem?.items||[]} busy={bloqueado} save={decidir} onDirty={setDirty}/>}
 </>;
}
