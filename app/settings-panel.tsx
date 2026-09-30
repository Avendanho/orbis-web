'use client';
import {useEffect,useState} from 'react';
import {Button} from '@/components/ui/button';import {Input} from '@/components/ui/input';import {Badge} from '@/components/ui/badge';
import {NativeSelect,NativeSelectOption} from '@/components/ui/native-select';
import {toast} from 'sonner';
import {CATALOGO,GRUPOS,ROTULO_ALVO,ligado,type Item} from '@/lib/settings';

// Configurações da instalação: um cartão por grupo do catálogo, desenhado a
// partir dele. Segredos nunca voltam ao navegador; o campo só recebe o novo.
async function request(url:string,method='GET',data?:any){const r=await fetch(url,{method,headers:data?{'content-type':'application/json'}:undefined,body:data?JSON.stringify(data):undefined});const x:any=await r.json();if(!r.ok)throw Error(x.message);return x}
const ORIGEM:Record<string,string>={tela:'salvo aqui',ambiente:'do ambiente','padrão':'padrão'};

function Campo({item,atual,rascunho,desabilitado,modelos,mudar}:{item:Item;atual:any;rascunho:any;desabilitado:boolean;modelos:string[];mudar:(k:string,v:any)=>void}){
 const origem=atual?.origem?ORIGEM[atual.origem]:'';
 let controle;
 if(item.tipo==='boolean'){
  const on=rascunho!==undefined?!!rascunho:ligado(item,atual);
  controle=<label className="choices"><input type="checkbox" checked={on} disabled={desabilitado} onChange={e=>mudar(item.key,e.target.checked)}/>{on?'Ligado':'Desligado'}</label>;
 }else if(item.tipo==='select'){
  controle=<NativeSelect value={rascunho??atual?.valor??item.padrao??''} disabled={desabilitado} onChange={e=>mudar(item.key,e.target.value)}>{item.opcoes!.map(([v,r])=><NativeSelectOption key={v} value={v}>{r}</NativeSelectOption>)}</NativeSelect>;
 }else if(item.key==='OLLAMA_MODELO'&&modelos.length){
  // Os modelos instalados no Ollama; o atual entra na lista mesmo se sumiu dela.
  const valor=rascunho??atual?.valor??item.padrao??'';
  controle=<NativeSelect value={valor} disabled={desabilitado} onChange={e=>mudar(item.key,e.target.value)}>{[...new Set([valor,...modelos])].filter(Boolean).map(m=><NativeSelectOption key={m} value={m}>{m}{modelos.includes(m)?'':' (não instalado)'}</NativeSelectOption>)}</NativeSelect>;
 }else if(item.tipo==='secret'){
  const removivel=atual?.origem==='tela'||(item.destino==='motor'&&atual?.preenchido);
  controle=<div className="actions"><Input type="password" autoComplete="off" value={typeof rascunho==='string'?rascunho:''} disabled={desabilitado} placeholder={rascunho===null?'Será removida ao salvar':atual?.preenchido?atual.valor+' — digite para trocar':'Não cadastrada'} onChange={e=>mudar(item.key,e.target.value)}/>{removivel&&<Button variant="outline" disabled={desabilitado} onClick={()=>mudar(item.key,null)}>Remover</Button>}</div>;
 }else{
  controle=<Input type={item.tipo==='number'?'number':item.tipo==='email'?'email':'text'} min={item.min} max={item.max} value={rascunho??atual?.valor??''} disabled={desabilitado} placeholder={item.padrao||''} onChange={e=>mudar(item.key,e.target.value)}/>;
 }
 return <div className="field"><span>{item.rotulo} {origem&&<Badge variant="outline">{origem}</Badge>}</span>{controle}{item.ajuda&&<small className="muted">{item.ajuda}</small>}</div>;
}

export default function SettingsPanel({onChange}:{onChange?:(orbis:any)=>void}){
 const [dados,setDados]=useState<any>(null),[rascunho,setRascunho]=useState<Record<string,any>>({}),[salvando,setSalvando]=useState(''),[testes,setTestes]=useState<Record<string,any>>({}),[aviso,setAviso]=useState('');
 async function carregar(){const d=await request('/api/settings');setDados(d);onChange?.(d.orbis);}
 useEffect(()=>{carregar().catch(e=>setAviso(e.message))},[]);
 const atual=(i:Item)=>i.destino==='motor'?dados?.motor?.itens?.[i.key]:dados?.orbis?.[i.key];
 const mudar=(k:string,v:any)=>setRascunho(r=>({...r,[k]:v}));
 async function salvar(grupo:string){
  const mudancas=Object.fromEntries(Object.entries(rascunho).filter(([k])=>CATALOGO.find(i=>i.key===k)?.grupo===grupo));
  if(!Object.keys(mudancas).length)return;
  setSalvando(grupo);
  try{
   const r=await request('/api/settings','PUT',{mudancas});
   setRascunho(x=>Object.fromEntries(Object.entries(x).filter(([k])=>!(k in mudancas))));
   await carregar();
   if(r.motor?.enviado===false&&r.motor?.pendentes?.length)toast.warning('Salvo no ORBIS, mas o motor não recebeu: '+r.motor.pendentes.join(', ')+'. '+(r.motor.erro||''));
   else toast.success('Configurações salvas.');
   if(r.motor?.reiniciar?.length)setAviso('Para o motor aplicar '+r.motor.reiniciar.join(', ')+', feche e abra o ORBIS de novo (start.py).');
  }catch(e:any){toast.error(e.message)}
  finally{setSalvando('')}
 }
 async function testar(alvo:string){
  setTestes(t=>({...t,[alvo]:{carregando:true}}));
  try{const r=await request('/api/settings','POST',{acao:'testar',alvo});setTestes(t=>({...t,[alvo]:r}))}
  catch(e:any){setTestes(t=>({...t,[alvo]:{ok:false,detalhe:e.message}}))}
 }
 if(!dados)return <section className="panel"><h2>Configurações</h2><p className="muted">{aviso||'Carregando…'}</p></section>;
 return <>
  <div className="page-heading"><div><p className="eyebrow">Esta instalação</p><h1>Configurações</h1><p className="muted">Valem para todos os projetos. Chaves nunca aparecem inteiras. O que for salvo aqui vale antes das variáveis de ambiente.</p></div></div>
  {aviso&&<p className="notice">{aviso}</p>}
  {GRUPOS.map(([grupo,titulo,descricao,alvos])=>{
   const itens=CATALOGO.filter(i=>i.grupo===grupo);
   const motorFora=itens.some(i=>i.destino==='motor')&&!dados.motor?.online;
   const pendente=itens.some(i=>i.key in rascunho);
   return <section className="panel" key={grupo}>
    <div className="section-top"><div><h2>{titulo}</h2><p className="muted">{descricao}</p></div><Button disabled={!pendente||!!salvando} onClick={()=>salvar(grupo)}>{salvando===grupo?'Salvando…':'Salvar'}</Button></div>
    {motorFora&&<p className="notice">O motor não está no ar, ou foi iniciado fora do start.py. As opções só do motor ficam bloqueadas até ele subir pelo start.py.</p>}
    {grupo==='ia'&&<p className="muted">Ollama {dados.ollama?.online?'no ar: '+(dados.ollama.modelos.length?dados.ollama.modelos.length+' modelo(s) instalado(s).':'nenhum modelo instalado (ollama pull qwen3:14b).'):'fora do ar neste endereço.'}</p>}
    {itens.map(i=><Campo key={i.key} item={i} atual={atual(i)} rascunho={rascunho[i.key]} desabilitado={motorFora&&i.destino==='motor'} modelos={dados.ollama?.modelos||[]} mudar={mudar}/>)}
    {alvos.length>0&&<div className="actions">{alvos.map(a=><Button key={a} variant="outline" disabled={!!testes[a]?.carregando} onClick={()=>testar(a)}>{testes[a]?.carregando?'Testando…':'Testar '+ROTULO_ALVO[a]}</Button>)}</div>}
    {alvos.filter(a=>testes[a]&&!testes[a].carregando).map(a=><p key={a} className={testes[a].ok?'muted':'notice'}>{testes[a].ok?'✓':'✗'} {ROTULO_ALVO[a]}: {testes[a].detalhe}</p>)}
   </section>;
  })}
 </>;
}
