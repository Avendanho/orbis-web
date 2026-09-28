'use client';
import {useEffect,useState} from 'react';
import {Users} from 'lucide-react';
import {Button} from '@/components/ui/button';
import {Input} from '@/components/ui/input';
import {Badge} from '@/components/ui/badge';
import {Select,SelectTrigger,SelectValue,SelectContent,SelectItem} from '@/components/ui/select';

async function call(url:string,method='GET',data?:any){const r=await fetch(url,{method,headers:data?{'content-type':'application/json'}:undefined,body:data?JSON.stringify(data):undefined});const x:any=await r.json();if(!r.ok)throw Error(x.message);return x}
const roleName=(x:string)=>x==='editor'?'Colaborador':x==='reviewer'?'Revisor':'Somente leitura';

export default function TeamPanel({project,busy,currentUser,rename}:any){
 const [rows,setRows]=useState<any[]>([]),[email,setEmail]=useState(''),[role,setRole]=useState('editor'),[message,setMessage]=useState(''),[projectName,setProjectName]=useState(project.name);
 const owner=project.access_role==='owner',url='/api/projects/'+project.id;
 async function load(){setRows(await call(url+'/members'))}
 useEffect(()=>{load().catch(e=>setMessage(e.message))},[project.id]);
 useEffect(()=>setProjectName(project.name),[project.name]);
 async function invite(){try{await call(url+'/members','POST',{email,role});setEmail('');setMessage('Convite criado. Envie o link do ORBIS ao pesquisador.');await load()}catch(e:any){setMessage(e.message)}}
 async function remove(value:string){try{await call(url+'/members','DELETE',{email:value});await load()}catch(e:any){setMessage(e.message)}}
 async function renameProject(){if(await rename(projectName.trim()))setMessage('Projeto renomeado com sucesso.')}
 const coordinatorName=project.coordinator_name||(owner?currentUser:'Nome do coordenador ainda não identificado');
 return <section className="panel team-panel">
  <div className="section-top"><div><p className="eyebrow">EQUIPE DO PROJETO</p><h2><Users/> Pesquisadores</h2></div><Badge variant="secondary">{rows.filter(x=>x.status==='accepted').length+1} participantes</Badge></div>
  {owner&&<div><h3>Nome do projeto</h3><div className="actions"><Input aria-label="Novo nome do projeto" value={projectName} maxLength={150} onChange={e=>setProjectName(e.target.value)}/><Button disabled={busy||!projectName.trim()||projectName.trim()===project.name} onClick={renameProject}>Renomear projeto</Button></div></div>}
  <div className="team-list"><div className="section-top"><div><strong>{coordinatorName}</strong><p>{project.coordinator_email||'Responsável pela equipe, permissões e exclusão.'}</p></div><Badge>Coordenador</Badge></div>{rows.map(m=><div className="section-top" key={m.email}><div><strong>{m.name||m.email}</strong><p>{m.email}</p></div><div className="actions"><Badge variant="secondary">{roleName(m.role)}</Badge><span>{m.status==='accepted'?'Ativo':m.status==='pending'?'Convite pendente':'Convite recusado'}</span>{owner&&<Button variant="ghost" disabled={busy} onClick={()=>remove(m.email)}>Remover</Button>}</div></div>)}</div>
  {owner&&<><h3>Convidar pesquisador</h3><div className="actions"><Input type="email" aria-label="E-mail do pesquisador" value={email} onChange={e=>setEmail(e.target.value)} placeholder="pesquisador@instituicao.br"/><Select value={role} onValueChange={setRole}><SelectTrigger aria-label="Função"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="editor">Colaborador</SelectItem><SelectItem value="reviewer">Revisor</SelectItem><SelectItem value="viewer">Somente leitura</SelectItem></SelectContent></Select><Button disabled={busy||!email.trim()} onClick={invite}>Enviar convite</Button></div></>}
  {message&&<p className="notice">{message}</p>}{!rows.length&&<p className="muted">Somente o coordenador participa deste projeto por enquanto.</p>}
 </section>
}
