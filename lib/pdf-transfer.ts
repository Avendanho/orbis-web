import {checkedFetch} from './remote';
export const MAX_PDF_BYTES=30*1024*1024;
export function publicPdfUrl(value:string){
 const u=new URL(value);const host=u.hostname.toLowerCase();
 if(!['https:','http:'].includes(u.protocol)||u.username||u.password||(u.port&&u.port!=='443'&&u.port!=='80')||!host.includes('.')||/^[\d.]+$/.test(host)||host.includes(':')||host.startsWith('[')||/\.(localhost|local|internal|test|invalid)$/.test(host))throw new Error('Endereço de PDF não permitido. Abra o link original.');
 return u;
}
export async function retrievePdf(url:string,signal:AbortSignal):Promise<Uint8Array>{
 let target=publicPdfUrl(url);const visited=new Set<string>();
 for(let redirect=0;redirect<8;redirect++){
  if(visited.has(target.href))throw new Error('A fonte entrou em um ciclo de redirecionamento. Abra o PDF manualmente.');visited.add(target.href);
  const response=await checkedFetch(target,{redirect:'manual',signal,headers:{Accept:'application/pdf,application/octet-stream;q=0.9,*/*;q=0.5','User-Agent':'ORBIS-Web/1.0 (scientific open-access retrieval)'}});
  if([301,302,303,307,308].includes(response.status)){
   const location=response.headers.get('location');await response.body?.cancel();if(!location)throw new Error('Redirecionamento sem destino.');target=publicPdfUrl(new URL(location,target).href);continue;
  }
  if(!response.ok){await response.body?.cancel();throw new Error(response.status===401?'A fonte exige autenticação (HTTP 401); isso não confirma acesso pago.':response.status===403?'A fonte bloqueou o download automático (HTTP 403). Abra o PDF manualmente.':response.status===404||response.status===410?'Link de PDF removido ou indisponível (HTTP '+response.status+').':response.status===429?'A fonte limitou os downloads (HTTP 429). Tente novamente mais tarde.':'A fonte não entregou o PDF (HTTP '+response.status+').');}
  if(Number(response.headers.get('content-length')||0)>MAX_PDF_BYTES){await response.body?.cancel();throw new Error('PDF maior que 30 MB. Use o link para baixar manualmente.');}
  if(!response.body)throw new Error('A fonte retornou um arquivo vazio.');
  const reader=response.body.getReader();const chunks:Uint8Array[]=[];let size=0;
  try {while(true){const part=await reader.read();if(part.done)break;size+=part.value.length;if(size>MAX_PDF_BYTES)throw new Error('PDF maior que 30 MB. Use o link para baixar manualmente.');chunks.push(part.value);}}
  catch(e){await reader.cancel().catch(()=>{});throw e;}
  const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
  if(!new TextDecoder().decode(bytes.subarray(0,1024)).includes('%PDF-'))throw new Error('O endereço retornou uma página ou bloqueio, não um PDF. Abra o link manualmente.');
  return bytes;
 }
 throw new Error('A fonte redirecionou muitas vezes. Abra o PDF manualmente.');
}
