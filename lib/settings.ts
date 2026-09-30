// O que uma instalação do ORBIS configura pela tela, e as regras puras sobre isso.
//
// É a única lista: a tela desenha os campos a partir dela e o servidor valida
// por ela. Sem imports, porque os testes carregam este arquivo direto.
//
// A chave de cada item é o nome da variável de ambiente que ele substitui. Assim
// quem já configurava pelo ambiente não perde nada: a resolução é
// tela → ambiente → padrão.
//
// Acesso institucional (EZproxy, proxy, sessão CAPES) fica fora de propósito:
// continua só no motor/.env.
export type Tipo='secret'|'text'|'email'|'url'|'number'|'select'|'boolean';
export type Destino='orbis'|'motor'|'ambos';
export type Grupo='bases'|'ia'|'lote'|'extracao'|'fontes';
export type Item={key:string;grupo:Grupo;rotulo:string;tipo:Tipo;destino:Destino;ajuda?:string;padrao?:string;min?:number;max?:number;opcoes?:[string,string][];aliases?:string[];reiniciar?:boolean;formato?:RegExp};

export const ALVOS=['ncbi','lilacs','embase','ollama','anthropic','openai','gemini','unpaywall'] as const;
export type Alvo=typeof ALVOS[number];
export const ROTULO_ALVO:Record<Alvo,string>={ncbi:'NCBI (PubMed/Cochrane)',lilacs:'LILACS',embase:'Embase',ollama:'Ollama (local)',anthropic:'Anthropic',openai:'OpenAI',gemini:'Gemini',unpaywall:'Unpaywall'};

export const GRUPOS:[Grupo,string,string,Alvo[]][]=[
 ['bases','Bases de busca','Credenciais e preferências da busca de artigos.',['ncbi','lilacs','embase']],
 ['ia','IA','Provedor da triagem e da análise PCC: modelo local pelo Ollama ou chave de nuvem.',['ollama','anthropic','openai','gemini']],
 ['lote','Triagem e lote','Ritmo das consultas e modo de download de projetos novos.',[]],
 ['extracao','Extração do texto','Como o motor transforma o PDF em texto (gravado em motor/.env).',[]],
 ['fontes','Fontes de download','Chaves e fontes do motor de download (gravadas em motor/.env).',['unpaywall']],
];

const MODELO=/^[\w.:/-]{1,100}$/;
const MODELO_URL=/^[\w.:-]{1,100}$/;
// Fontes que o motor consulta por padrão; cada uma desliga com PAPER_FETCH_NO_*.
const DESLIGAVEIS:[string,string][]=[['SCIHUB','Sci-Hub'],['LIBGEN','Library Genesis'],['WAYBACK','Wayback Machine'],['OSTI','OSTI'],['SCHOLAR','Google Scholar'],['FATCAT','Fatcat'],['BASE','BASE'],['OPENALEX_CONTENT','OpenAlex Content']];

export const CATALOGO:Item[]=[
 {key:'NCBI_API_KEY',grupo:'bases',rotulo:'Chave da API do NCBI',tipo:'secret',destino:'orbis',ajuda:'Eleva o limite de consultas do PubMed e da Cochrane. Crie em ncbi.nlm.nih.gov/account.'},
 {key:'NCBI_EMAIL',grupo:'bases',rotulo:'E-mail para o NCBI',tipo:'email',destino:'orbis'},
 {key:'ELSEVIER_API_KEY',grupo:'bases',rotulo:'Chave da Elsevier',tipo:'secret',destino:'ambos',aliases:['EMBASE_API_KEY'],ajuda:'Busca no Embase e PDFs da Elsevier pelo motor. Crie em dev.elsevier.com.'},
 {key:'ELSEVIER_INST_TOKEN',grupo:'bases',rotulo:'Token institucional da Elsevier',tipo:'secret',destino:'ambos',aliases:['EMBASE_INST_TOKEN'],ajuda:'Fornecido pela biblioteca; é ele que libera o Embase.'},
 {key:'ORBIS_BASE_PADRAO',grupo:'bases',rotulo:'Base aberta ao entrar na busca',tipo:'select',destino:'orbis',padrao:'pubmed',opcoes:[['pubmed','PubMed'],['lilacs','LILACS'],['cochrane','Cochrane (revisões CDSR)'],['embase','Embase']]},
 {key:'ORBIS_BUSCA_LIMITE',grupo:'bases',rotulo:'Máximo de resultados por busca',tipo:'number',destino:'orbis',padrao:'500',min:1,max:500},

 {key:'ORBIS_IA_PROVEDOR',grupo:'ia',rotulo:'Provedor preferido',tipo:'select',destino:'orbis',padrao:'automatico',opcoes:[['automatico','Automático (local se o Ollama responde; senão a primeira chave de nuvem)'],['local','Local (Ollama)'],['anthropic','Anthropic'],['openai','OpenAI'],['gemini','Gemini']],ajuda:'Um provedor fixo sem chave não cai em outro: a triagem automática fica indisponível até cadastrar a chave.'},
 {key:'OLLAMA_URL',grupo:'ia',rotulo:'Endereço do Ollama',tipo:'url',destino:'orbis',padrao:'http://localhost:11434'},
 {key:'OLLAMA_MODELO',grupo:'ia',rotulo:'Modelo local',tipo:'text',destino:'orbis',padrao:'qwen3:14b',formato:MODELO,ajuda:'Um dos modelos instalados no Ollama (ollama pull <modelo> para instalar outro).'},
 {key:'OLLAMA_CONTEXTO',grupo:'ia',rotulo:'Contexto do modelo local (tokens)',tipo:'number',destino:'orbis',padrao:'16384',min:2048,max:131072,ajuda:'Maior = mais texto completo por artigo na PCC, e mais memória da GPU.'},
 {key:'OLLAMA_PRAZO',grupo:'ia',rotulo:'Prazo por chamada ao modelo local (segundos)',tipo:'number',destino:'orbis',padrao:'600',min:30,max:3600},
 {key:'ANTHROPIC_API_KEY',grupo:'ia',rotulo:'Chave da Anthropic',tipo:'secret',destino:'orbis'},
 {key:'ORBIS_IA_MODELO_ANTHROPIC',grupo:'ia',rotulo:'Modelo da Anthropic',tipo:'text',destino:'orbis',padrao:'claude-sonnet-5',formato:MODELO},
 {key:'OPENAI_API_KEY',grupo:'ia',rotulo:'Chave da OpenAI',tipo:'secret',destino:'orbis'},
 {key:'ORBIS_IA_MODELO_OPENAI',grupo:'ia',rotulo:'Modelo da OpenAI',tipo:'text',destino:'orbis',padrao:'gpt-4o-mini',formato:MODELO},
 {key:'GEMINI_API_KEY',grupo:'ia',rotulo:'Chave do Gemini',tipo:'secret',destino:'orbis'},
 {key:'ORBIS_IA_MODELO_GEMINI',grupo:'ia',rotulo:'Modelo do Gemini',tipo:'text',destino:'orbis',padrao:'gemini-2.5-flash',formato:MODELO_URL},
 {key:'ORBIS_IA_LOTE',grupo:'ia',rotulo:'Artigos por lote de IA',tipo:'number',destino:'orbis',padrao:'10',min:1,max:50,ajuda:'A tela repete os lotes com progresso; pausar vale entre um lote e outro.'},

 {key:'ORBIS_LOTE_SIMULTANEOS',grupo:'lote',rotulo:'Consultas simultâneas',tipo:'number',destino:'orbis',padrao:'2',min:1,max:4,ajuda:'Mais que 4 só gera bloqueio (HTTP 429) nas fontes.'},
 {key:'ORBIS_DOWNLOAD_PADRAO',grupo:'lote',rotulo:'Modo de download de projetos novos',tipo:'select',destino:'orbis',padrao:'baixar',opcoes:[['baixar','Analisar e baixar os PDFs'],['analisar','Só analisar no sistema']]},

 {key:'ORBIS_EXTRAIR_MARKDOWN',grupo:'extracao',rotulo:'Extrair o texto em Markdown',tipo:'boolean',destino:'motor',padrao:'1',ajuda:'Preserva seções, títulos e tabelas. Desligado, vale o texto simples.'},
 {key:'ORBIS_SALVAR_IMAGENS',grupo:'extracao',rotulo:'Salvar as imagens ao lado do PDF',tipo:'boolean',destino:'motor',padrao:'1',ajuda:'Só no modo "baixar": <nome>_imagens/ ao lado do .md.'},
 {key:'ORBIS_EXTRACAO_PRAZO',grupo:'extracao',rotulo:'Prazo da extração por artigo (segundos)',tipo:'number',destino:'motor',padrao:'90',min:5,max:600,ajuda:'Passou disso, vale o texto simples.'},

 {key:'UNPAYWALL_EMAIL',grupo:'fontes',rotulo:'E-mail do Unpaywall',tipo:'email',destino:'ambos',reiniciar:true,ajuda:'A fonte de maior rendimento. Obrigatório para consultá-la.'},
 {key:'OPENALEX_API_KEY',grupo:'fontes',rotulo:'Chave da OpenAlex',tipo:'secret',destino:'motor',ajuda:'Libera o PDF em cache da OpenAlex.'},
 {key:'SEMANTIC_SCHOLAR_API_KEY',grupo:'fontes',rotulo:'Chave do Semantic Scholar',tipo:'secret',destino:'ambos',ajuda:'Cota própria (1 pedido/s) em vez da cota anônima compartilhada, que recusa pedidos quando está cheia.'},
 {key:'CORE_API_KEY',grupo:'fontes',rotulo:'Chave do CORE',tipo:'secret',destino:'motor',reiniciar:true},
 {key:'SPRINGER_API_KEY',grupo:'fontes',rotulo:'Chave da Springer Nature',tipo:'secret',destino:'motor',reiniciar:true},
 {key:'WILEY_TDM_TOKEN',grupo:'fontes',rotulo:'Token TDM da Wiley',tipo:'secret',destino:'motor'},
 ...DESLIGAVEIS.map(([k,nome]):Item=>({key:'PAPER_FETCH_NO_'+k,grupo:'fontes',rotulo:'Desligar '+nome,tipo:'boolean',destino:'motor'})),
];

export const ITENS:Record<string,Item>=Object.fromEntries(CATALOGO.map(i=>[i.key,i]));

export type Origem='tela'|'ambiente'|'padrão'|'';
export type Resolvido=Record<string,{valor:string;origem:Origem}>;

const DESLIGADO=['0','false','nao','não','off'];

export function validar(key:string,bruto:unknown):string{
 const item=ITENS[key];
 if(!item)throw new Error('Configuração desconhecida: '+key+'.');
 if(item.tipo==='boolean'){
  // Ligado por padrão: apagar voltaria a ligar, então desligar grava 0.
  // Desligado por padrão: desligar é apagar (o motor trata presença como ligado).
  if(bruto===true||bruto==='1'||bruto==='true')return '1';
  if(bruto===false||bruto===''||bruto==='0'||bruto==='false'||bruto==null)return item.padrao==='1'?'0':'';
  throw new Error(item.rotulo+': use ligado ou desligado.');
 }
 const v=String(bruto??'').trim();
 if(!v)return '';
 // Uma quebra de linha num valor viraria uma linha nova no motor/.env.
 if(/[\r\n\0]/.test(v))throw new Error(item.rotulo+': não pode ter quebra de linha.');
 if(v.length>2000)throw new Error(item.rotulo+': valor longo demais.');
 if(item.tipo==='email'&&!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v))throw new Error(item.rotulo+': e-mail inválido.');
 if(item.tipo==='url'){
  let u:URL;try{u=new URL(v)}catch{throw new Error(item.rotulo+': endereço inválido.')}
  if(!['http:','https:'].includes(u.protocol))throw new Error(item.rotulo+': use http ou https.');
 }
 if(item.tipo==='number'){
  const n=Number(v);
  if(!Number.isInteger(n)||n<item.min!||n>item.max!)throw new Error(item.rotulo+': use um número inteiro de '+item.min+' a '+item.max+'.');
  return String(n);
 }
 if(item.tipo==='select'&&!item.opcoes!.some(([valor])=>valor===v))throw new Error(item.rotulo+': opção inválida.');
 if(item.formato&&!item.formato.test(v))throw new Error(item.rotulo+': formato inválido.');
 return v;
}

export function resolver(salvos:Record<string,string>,env:Record<string,unknown>):Resolvido{
 const out:Resolvido={};
 for(const i of CATALOGO){
  if(i.destino==='motor')continue;
  const salvo=String(salvos[i.key]??'').trim();
  const doAmbiente=[i.key,...(i.aliases||[])].map(k=>String(env[k]??'').trim()).find(Boolean)||'';
  out[i.key]=salvo?{valor:salvo,origem:'tela'}:doAmbiente?{valor:doAmbiente,origem:'ambiente'}:i.padrao?{valor:i.padrao,origem:'padrão'}:{valor:'',origem:''};
 }
 return out;
}

export function valores(r:Resolvido):Record<string,string>{
 return Object.fromEntries(Object.entries(r).map(([k,x])=>[k,x.valor]));
}

// Os 4 últimos caracteres ajudam a reconhecer qual chave está lá; num segredo
// curto eles seriam uma fração grande demais dele.
export function mascarar(v:string):string{
 if(!v)return '';
 return v.length<12?'••••':'••••'+v.slice(-4);
}

export function visaoPublica(r:Resolvido){
 return Object.fromEntries(Object.entries(r).map(([k,x])=>[k,{valor:ITENS[k]?.tipo==='secret'?mascarar(x.valor):x.valor,origem:x.origem,preenchido:!!x.valor}]));
}

export function separarPorDestino(m:Record<string,string|null>){
 const orbis:Record<string,string|null>={},motor:Record<string,string|null>={};
 for(const [k,v] of Object.entries(m)){
  const d=ITENS[k]?.destino,valor=v===''?null:v;
  if(d==='orbis'||d==='ambos')orbis[k]=valor;
  if(d==='motor'||d==='ambos')motor[k]=valor;
 }
 return {orbis,motor};
}

// Como a tela mostra um booleano: o valor presente ou, sem valor, o padrão.
export function ligado(item:Item,atual:{preenchido?:boolean;valor?:string}|null|undefined):boolean{
 if(!atual?.preenchido)return item.padrao==='1';
 return !DESLIGADO.includes(String(atual.valor||'').trim().toLowerCase());
}

export function traduzirErro(msg:string):string{
 const t=msg.toLowerCase();
 if(/\b(401|403)\b|invalid api key|api key not valid|unauthorized|permission denied|credenciais/.test(t))return 'Chave recusada pelo serviço.';
 if(/\b429\b|rate limit|limitou/.test(t))return 'Limite de consultas atingido; tente de novo em instantes.';
 if(/fetch failed|network|timeout|timed out|abort|enotfound|econnrefused|fora do ar/.test(t))return 'Sem conexão com o serviço.';
 return msg.slice(0,200);
}

// O Gemini recebe a chave na URL; uma mensagem de erro pode trazê-la de volta.
export function semSegredos(msg:string,v:Record<string,string>):string{
 let s=msg;
 for(const i of CATALOGO)if(i.tipo==='secret'&&(v[i.key]||'').length>=4)s=s.split(v[i.key]).join('••••');
 return s;
}

export function simultaneos(orbis:any):number{
 const n=Number(orbis?.ORBIS_LOTE_SIMULTANEOS?.valor);
 return Number.isInteger(n)&&n>=1&&n<=4?n:2;
}
