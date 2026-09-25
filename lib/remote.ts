// Every discovered page and redirect must resolve to public addresses.
const checked=new Map<string,number>();
export function publicAddress(value:string){if(value.includes(':'))return /^[23][a-f0-9:]+$/i.test(value);const a=value.split('.').map(Number);if(a.length!==4||a.some(n=>!Number.isInteger(n)||n<0||n>255))return false;const [x,y]=a;return !(x===0||x===10||x===127||x>=224||x===169&&y===254||x===172&&y>=16&&y<=31||x===192&&y===168||x===100&&y>=64&&y<=127||x===198&&(y===18||y===19));}
export async function checkedFetch(target:URL,options:RequestInit){const host=target.hostname;if((checked.get(host)||0)<Date.now()){
 const replies=await Promise.all(['A','AAAA'].map(async type=>{const r=await fetch('https://cloudflare-dns.com/dns-query?name='+encodeURIComponent(host)+'&type='+type,{headers:{accept:'application/dns-json'},signal:AbortSignal.timeout(5000)});if(!r.ok)throw Error('Não foi possível verificar o endereço público da fonte.');return await r.json() as any}));
 const addresses=replies.flatMap(r=>(r.Answer||[]).filter((x:any)=>x.type===1||x.type===28).map((x:any)=>x.data));if(!addresses.length||addresses.some((x:string)=>!publicAddress(x)))throw Error('A fonte não resolveu para um endereço público permitido.');if(checked.size>500)checked.clear();checked.set(host,Date.now()+60000);
 }return fetch(target,options);}
