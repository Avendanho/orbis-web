import {getChatGPTUser,chatGPTSignInPath} from './chatgpt-auth';
import Workspace from './workspace';
export const dynamic='force-dynamic';
export default async function Page(){const user=await getChatGPTUser();if(!user)return <main className="signin"><span className="brand"><img className="brand-mark" src="/favicon.svg" width={40} height={40} alt=""/>ORBIS<span className="brand-label">WEB</span></span><h1>Sua revisão, em um só lugar.</h1><p>Planeje os critérios, encontre artigos e organize as evidências em projetos privados.</p><a className="signin-link" target="_top" href={chatGPTSignInPath('/')}>Entrar com ChatGPT</a><small>Os projetos e os PDFs ficam disponíveis apenas para sua conta.</small></main>;return <Workspace user={user.displayName}/>;}
