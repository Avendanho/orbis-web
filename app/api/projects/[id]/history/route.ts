import {identity,owned,database,ok,fail} from '@/lib/server';
export async function GET(r:Request,{params}:any){try{const actor=await identity(r),{id}=await params;await owned(id,actor);return ok((await database().prepare('SELECT id,action,revision,created FROM events WHERE project=? ORDER BY revision DESC LIMIT 200').bind(id).all()).results)}catch(e){return fail(e)}}
