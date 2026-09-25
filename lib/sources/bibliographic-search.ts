// Uma porta só para as bases bibliográficas que alimentam o lote de DOIs.
//
// Cada base tem sua sintaxe e seu acesso, mas todas terminam no mesmo lugar:
// registros com (ou sem) DOI. A rota de busca não precisa saber qual é qual.
import {searchPubmed} from './pubmed-search';
import {searchLilacs} from './lilacs-search';
import {searchEmbase} from './embase-search';
import {cochraneTerm} from './cochrane-search';
import type {Base} from './bases';
export {BASES,isBase,type Base} from './bases';

type Env={NCBI_API_KEY?:string;NCBI_EMAIL?:string;EMBASE_API_KEY?:string;ELSEVIER_API_KEY?:string;ELSEVIER_INST_TOKEN?:string;EMBASE_INST_TOKEN?:string};

export async function searchBase(base:Base,term:string,retmax:number,env:Env):Promise<{records:{doi:string}[];total:number;withoutDoi:number}>{
 const ncbi={retmax,apiKey:env.NCBI_API_KEY,email:env.NCBI_EMAIL};
 if(base==='pubmed')return searchPubmed(term,ncbi);
 if(base==='cochrane')return searchPubmed(cochraneTerm(term),ncbi);
 if(base==='lilacs')return searchLilacs(term,{retmax});
 return searchEmbase(term,{retmax,apiKey:env.EMBASE_API_KEY||env.ELSEVIER_API_KEY,instToken:env.EMBASE_INST_TOKEN||env.ELSEVIER_INST_TOKEN});
}
