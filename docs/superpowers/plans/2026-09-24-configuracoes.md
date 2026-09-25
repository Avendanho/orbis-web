# Seção de Configurações — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Uma seção Configurações na interface do ORBIS onde a pessoa ajusta credenciais das bases, IA de triagem, ritmo do lote e chaves/opções do motor, sem editar ambiente nem `motor/.env`.

**Architecture:** Um catálogo único (`lib/settings.ts`, sem imports) descreve cada item configurável e concentra as regras puras (validar, resolver, mascarar). O ORBIS guarda os seus itens numa tabela `settings` do D1 e resolve cada valor como tela → ambiente → padrão. Os itens do motor vão por `PUT /config` ao `servico-python`, que grava `motor/.env` preservando comentários e passa a carregar esse arquivo na partida; a rota exige o token `ORBIS_ENGINE_TOKEN` que o `start.py` gera.

**Tech Stack:** Next (vinext) em Cloudflare Workers/workerd, D1, TypeScript sem build nos testes (`stripTypeScriptTypes`), Miniflare nos testes de integração; FastAPI + pytest no motor.

**Spec:** `docs/superpowers/specs/2026-09-24-configuracoes-design.md`

## Global Constraints

- Instalação local por pessoa: configurações valem para a instalação inteira; **sem coluna de usuário**.
- Ordem de resolução no ORBIS: **valor salvo na tela → variável de ambiente (incluindo aliases) → padrão do catálogo**.
- Segredo nunca volta inteiro ao navegador: `••••` + 4 últimos caracteres, e só se o segredo tiver 12+ caracteres; abaixo disso, `••••`.
- Valor com `\r`, `\n` ou `\0` é recusado (protege o `.env` contra injeção de linha).
- `ORBIS_ENGINE_URL` e `ORBIS_DATA_DIR` **não** são editáveis pela tela.
- `/config` do motor exige o cabeçalho `X-Orbis-Token` igual a `ORBIS_ENGINE_TOKEN`; sem token configurado no motor, recusa sempre (403).
- Texto de interface e mensagens em português; código segue o estilo compacto do repositório (sem ponto e vírgula a mais, comentários explicam o porquê).
- `lib/settings.ts` **não pode ter imports**: os testes o carregam por data URL.
- Esta pasta **não é repositório git**: onde um passo diria "commit", o checkpoint é rodar a suíte indicada e ela passar.
- Testes do ORBIS: `for t in tests/*.mjs; do node "$t"; done` (os `*-integration.mjs` exigem `pnpm build` antes). Motor: `cd servico-python && ../motor/.venv/bin/python -m pytest -q`.

## Review Focus

1. **Quebra de linha num valor** (ex.: e-mail colado com `\n`) → 400 no ORBIS e `ValueError` no motor; nada gravado, `.env` intacto. Testes: Task 1 (`validar`), Task 3 (`gravar`), Task 5 (PUT all-or-nothing).
2. **Motor fora do ar num PUT com itens `ambos`** → parte do ORBIS salva, resposta com `motor.enviado:false` e `pendentes`. Teste: Task 5 (integração, motor derrubado).
3. **Segredo vazando** no GET, no PUT ou na mensagem de erro de um teste (a chave do Gemini vai na URL) → nunca aparece. Testes: Task 1 (`semSegredos`), Task 5 (corpo das respostas não contém a chave).
4. **Banco antigo sem a tabela `settings`** (migração não aplicada) → busca e GET continuam funcionando com o ambiente. Teste: Task 5 (`DROP TABLE settings` e GET 200 com origem `ambiente`).
5. **Motor iniciado à mão, sem token** → `/config` responde 403 com orientação, a tela mostra o bloco do motor desabilitado. Testes: Task 3 (rota sem token), Task 5 (motor recusando → `engineConfig` nulo).

---

### Task 1: Catálogo e regras puras

**Files:**
- Create: `lib/settings.ts`
- Test: `tests/settings.mjs`

**Interfaces:**
- Produces:
  - `type Tipo='secret'|'text'|'email'|'url'|'number'|'select'|'boolean'`, `type Destino='orbis'|'motor'|'ambos'`, `type Grupo='bases'|'ia'|'lote'|'motor'`
  - `type Item={key;grupo;rotulo;tipo;destino;ajuda?;padrao?;min?;max?;opcoes?:[string,string][];aliases?:string[];reiniciar?:boolean;formato?:RegExp}`
  - `const ALVOS` (tupla) e `type Alvo='ncbi'|'lilacs'|'embase'|'unpaywall'|'anthropic'|'openai'|'gemini'`, `ROTULO_ALVO:Record<Alvo,string>`
  - `GRUPOS:[Grupo,string,string,Alvo[]][]` (grupo, título, descrição, alvos testáveis)
  - `CATALOGO:Item[]`, `ITENS:Record<string,Item>`
  - `type Origem='tela'|'ambiente'|'padrão'|''`, `type Resolvido=Record<string,{valor:string;origem:Origem}>`
  - `validar(key:string,bruto:unknown):string` — normalizado; `''` significa apagar; lança `Error` com mensagem em português
  - `resolver(salvos:Record<string,string>,env:Record<string,unknown>):Resolvido` — só itens `orbis`/`ambos`
  - `valores(r:Resolvido):Record<string,string>`
  - `mascarar(v:string):string`
  - `visaoPublica(r:Resolvido):Record<string,{valor:string;origem:Origem;preenchido:boolean}>`
  - `separarPorDestino(m:Record<string,string|null>):{orbis:Record<string,string|null>;motor:Record<string,string|null>}` — `''` vira `null`
  - `traduzirErro(msg:string):string`, `semSegredos(msg:string,v:Record<string,string>):string`
  - `simultaneos(orbis:any):number` — 1..4, padrão 2

- [ ] **Step 1: Escrever o teste que falha**

`tests/settings.mjs`:

```js
import {readFile} from 'node:fs/promises';import {stripTypeScriptTypes} from 'node:module';import assert from 'node:assert/strict';
const url=s=>'data:text/javascript;base64,'+Buffer.from(s).toString('base64');
const load=async p=>import(url(stripTypeScriptTypes(await readFile(p,'utf8'))));
const st=await load('lib/settings.ts');

// ---------------------------------------------------------------------------
// Catálogo
// ---------------------------------------------------------------------------
const chaves=st.CATALOGO.map(i=>i.key);
assert.equal(new Set(chaves).size,chaves.length,'chave repetida no catálogo');
for(const i of st.CATALOGO){
 assert.ok(st.GRUPOS.some(([g])=>g===i.grupo),i.key+' em grupo inexistente');
 if(i.tipo==='select')assert.ok(i.opcoes?.some(([v])=>v===i.padrao),i.key+': padrão fora das opções');
 if(i.tipo==='number')assert.ok(i.min!=null&&i.max!=null,i.key+': número sem limites');
}
assert.ok(!chaves.includes('ORBIS_ENGINE_URL')&&!chaves.includes('ORBIS_DATA_DIR'),'o start.py precisa deles antes da tela');

// ---------------------------------------------------------------------------
// Validação
// ---------------------------------------------------------------------------
assert.throws(()=>st.validar('NAO_EXISTE','x'),/desconhecida/);
assert.throws(()=>st.validar('NCBI_EMAIL','a@b.co\nHTTP_PROXY=http://mal'),/quebra de linha/,'injeção de linha no .env');
assert.throws(()=>st.validar('NCBI_API_KEY','abc\rdef'),/quebra de linha/);
assert.throws(()=>st.validar('NCBI_EMAIL','sem-arroba'),/e-mail/);
assert.equal(st.validar('NCBI_EMAIL','  a@b.co '),'a@b.co','espaços nas pontas saem');
assert.equal(st.validar('NCBI_EMAIL',''),'','vazio = apagar');
assert.throws(()=>st.validar('ORBIS_LOTE_SIMULTANEOS','9'),/1 a 4/);
assert.throws(()=>st.validar('ORBIS_LOTE_SIMULTANEOS','2.5'),/inteiro/);
assert.equal(st.validar('ORBIS_LOTE_SIMULTANEOS',' 3'),'3');
assert.throws(()=>st.validar('ORBIS_BASE_PADRAO','scopus'),/opção/);
assert.equal(st.validar('ORBIS_BASE_PADRAO','lilacs'),'lilacs');
assert.throws(()=>st.validar('PAPER_FETCH_PROXY','ftp://x'),/http/);
assert.equal(st.validar('PAPER_FETCH_PROXY','socks5://127.0.0.1:9050'),'socks5://127.0.0.1:9050');
assert.equal(st.validar('PAPER_FETCH_CLOAK',true),'1');
assert.equal(st.validar('PAPER_FETCH_CLOAK',false),'','desligar = apagar a chave');
assert.throws(()=>st.validar('ORBIS_IA_MODELO_GEMINI','x/../../y?key=1'),/formato/,'modelo vai na URL do Gemini');
assert.equal(st.validar('ORBIS_IA_MODELO_ANTHROPIC','claude-sonnet-5'),'claude-sonnet-5');

// ---------------------------------------------------------------------------
// Resolução: tela → ambiente (com alias) → padrão
// ---------------------------------------------------------------------------
const r=st.resolver({NCBI_API_KEY:'da-tela-123456789'},{NCBI_API_KEY:'do-ambiente',EMBASE_API_KEY:'alias-embase-12345',CORE_API_KEY:'x'});
assert.deepEqual(r.NCBI_API_KEY,{valor:'da-tela-123456789',origem:'tela'});
assert.deepEqual(r.ELSEVIER_API_KEY,{valor:'alias-embase-12345',origem:'ambiente'},'alias do ambiente vale');
assert.deepEqual(r.ORBIS_LOTE_SIMULTANEOS,{valor:'2',origem:'padrão'});
assert.deepEqual(r.NCBI_EMAIL,{valor:'',origem:''});
assert.equal(r.CORE_API_KEY,undefined,'item só do motor não é resolvido no ORBIS');
assert.equal(st.valores(r).NCBI_API_KEY,'da-tela-123456789');

// ---------------------------------------------------------------------------
// Máscara e visão pública
// ---------------------------------------------------------------------------
assert.equal(st.mascarar(''),'');
assert.equal(st.mascarar('curta-1234'),'••••','segredo curto não mostra nada');
assert.equal(st.mascarar('abcdefghijkl'),'••••ijkl');
const pub=st.visaoPublica(r);
assert.equal(pub.NCBI_API_KEY.valor,'••••6789');
assert.equal(pub.NCBI_API_KEY.preenchido,true);
assert.equal(pub.ORBIS_LOTE_SIMULTANEOS.valor,'2','não-segredo aparece inteiro');
assert.ok(!JSON.stringify(pub).includes('da-tela-123456789'));

// ---------------------------------------------------------------------------
// Destinos
// ---------------------------------------------------------------------------
const d=st.separarPorDestino({NCBI_API_KEY:'a',CORE_API_KEY:'b',UNPAYWALL_EMAIL:'c@d.ef',NCBI_EMAIL:''});
assert.deepEqual(d.orbis,{NCBI_API_KEY:'a',UNPAYWALL_EMAIL:'c@d.ef',NCBI_EMAIL:null});
assert.deepEqual(d.motor,{CORE_API_KEY:'b',UNPAYWALL_EMAIL:'c@d.ef'});

// ---------------------------------------------------------------------------
// Erros de teste de credencial
// ---------------------------------------------------------------------------
assert.equal(st.traduzirErro('Gemini HTTP 403: permission denied'),'Chave recusada pelo serviço.');
assert.equal(st.traduzirErro('HTTP 429'),'Limite de consultas atingido; tente de novo em instantes.');
assert.equal(st.traduzirErro('TypeError: fetch failed'),'Sem conexão com o serviço.');
assert.equal(st.semSegredos('falhou em ?key=segredo-gemini-999',{GEMINI_API_KEY:'segredo-gemini-999'}),'falhou em ?key=••••');

// ---------------------------------------------------------------------------
// Lote
// ---------------------------------------------------------------------------
assert.equal(st.simultaneos(null),2);
assert.equal(st.simultaneos({ORBIS_LOTE_SIMULTANEOS:{valor:'4'}}),4);
assert.equal(st.simultaneos({ORBIS_LOTE_SIMULTANEOS:{valor:'40'}}),2,'fora da faixa cai no padrão');

console.log('settings: ok');
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `node tests/settings.mjs`
Expected: FAIL — `ENOENT: no such file or directory, open 'lib/settings.ts'`

- [ ] **Step 3: Implementar**

`lib/settings.ts`:

```ts
// O que uma instalação do ORBIS configura pela tela, e as regras puras sobre isso.
//
// É a única lista: a tela desenha os campos a partir dela e o servidor valida
// por ela. Sem imports, porque os testes carregam este arquivo direto.
//
// A chave de cada item é o nome da variável de ambiente que ele substitui. Assim
// quem já configurava pelo ambiente não perde nada: a resolução é
// tela → ambiente → padrão.
export type Tipo='secret'|'text'|'email'|'url'|'number'|'select'|'boolean';
export type Destino='orbis'|'motor'|'ambos';
export type Grupo='bases'|'ia'|'lote'|'motor';
export type Item={key:string;grupo:Grupo;rotulo:string;tipo:Tipo;destino:Destino;ajuda?:string;padrao?:string;min?:number;max?:number;opcoes?:[string,string][];aliases?:string[];reiniciar?:boolean;formato?:RegExp};

export const ALVOS=['ncbi','lilacs','embase','unpaywall','anthropic','openai','gemini'] as const;
export type Alvo=typeof ALVOS[number];
export const ROTULO_ALVO:Record<Alvo,string>={ncbi:'NCBI (PubMed/Cochrane)',lilacs:'LILACS',embase:'Embase',unpaywall:'Unpaywall',anthropic:'Anthropic',openai:'OpenAI',gemini:'Gemini'};

export const GRUPOS:[Grupo,string,string,Alvo[]][]=[
 ['bases','Bases de busca','Credenciais e preferências da busca de artigos.',['ncbi','lilacs','embase']],
 ['ia','IA de triagem','Chaves dos provedores e qual deles a triagem automática usa.',['anthropic','openai','gemini']],
 ['lote','Lote de consultas','Ritmo das consultas e modo de download de projetos novos.',[]],
 ['motor','Motor de download','Chaves e opções do motor Python (gravadas em motor/.env).',['unpaywall']],
];

const MODELO=/^[\w.:-]{1,100}$/;

export const CATALOGO:Item[]=[
 {key:'NCBI_API_KEY',grupo:'bases',rotulo:'Chave da API do NCBI',tipo:'secret',destino:'orbis',ajuda:'Eleva o limite de consultas do PubMed e da Cochrane. Crie em ncbi.nlm.nih.gov/account.'},
 {key:'NCBI_EMAIL',grupo:'bases',rotulo:'E-mail para o NCBI',tipo:'email',destino:'orbis'},
 {key:'ELSEVIER_API_KEY',grupo:'bases',rotulo:'Chave da Elsevier',tipo:'secret',destino:'ambos',aliases:['EMBASE_API_KEY'],ajuda:'Busca no Embase e PDFs da Elsevier pelo motor. Crie em dev.elsevier.com.'},
 {key:'ELSEVIER_INST_TOKEN',grupo:'bases',rotulo:'Token institucional da Elsevier',tipo:'secret',destino:'ambos',aliases:['EMBASE_INST_TOKEN'],ajuda:'Fornecido pela biblioteca; é ele que libera o Embase.'},
 {key:'ORBIS_BASE_PADRAO',grupo:'bases',rotulo:'Base aberta ao entrar na busca',tipo:'select',destino:'orbis',padrao:'pubmed',opcoes:[['pubmed','PubMed'],['lilacs','LILACS'],['cochrane','Cochrane (revisões CDSR)'],['embase','Embase']]},
 {key:'ORBIS_BUSCA_LIMITE',grupo:'bases',rotulo:'Máximo de resultados por busca',tipo:'number',destino:'orbis',padrao:'500',min:1,max:500},

 {key:'ANTHROPIC_API_KEY',grupo:'ia',rotulo:'Chave da Anthropic',tipo:'secret',destino:'ambos'},
 {key:'OPENAI_API_KEY',grupo:'ia',rotulo:'Chave da OpenAI',tipo:'secret',destino:'ambos'},
 {key:'GEMINI_API_KEY',grupo:'ia',rotulo:'Chave do Gemini',tipo:'secret',destino:'ambos'},
 {key:'ORBIS_IA_PROVEDOR',grupo:'ia',rotulo:'Provedor da triagem automática',tipo:'select',destino:'orbis',padrao:'auto',opcoes:[['auto','Automático (primeira chave disponível)'],['anthropic','Anthropic'],['openai','OpenAI'],['gemini','Gemini']]},
 {key:'ORBIS_IA_MODELO_ANTHROPIC',grupo:'ia',rotulo:'Modelo da Anthropic',tipo:'text',destino:'orbis',padrao:'claude-sonnet-5',formato:MODELO},
 {key:'ORBIS_IA_MODELO_OPENAI',grupo:'ia',rotulo:'Modelo da OpenAI',tipo:'text',destino:'orbis',padrao:'gpt-4o-mini',formato:MODELO},
 {key:'ORBIS_IA_MODELO_GEMINI',grupo:'ia',rotulo:'Modelo do Gemini',tipo:'text',destino:'orbis',padrao:'gemini-2.5-flash',formato:MODELO},

 {key:'ORBIS_LOTE_SIMULTANEOS',grupo:'lote',rotulo:'Consultas simultâneas',tipo:'number',destino:'orbis',padrao:'2',min:1,max:4,ajuda:'Mais que 4 só gera bloqueio (HTTP 429) nas fontes.'},
 {key:'ORBIS_DOWNLOAD_PADRAO',grupo:'lote',rotulo:'Modo de download de projetos novos',tipo:'select',destino:'orbis',padrao:'baixar',opcoes:[['baixar','Analisar e baixar os PDFs'],['analisar','Só analisar no sistema']]},

 {key:'UNPAYWALL_EMAIL',grupo:'motor',rotulo:'E-mail do Unpaywall',tipo:'email',destino:'ambos',reiniciar:true,ajuda:'A fonte de maior rendimento. Obrigatório para consultá-la.'},
 {key:'CROSSREF_MAILTO',grupo:'motor',rotulo:'E-mail para a Crossref',tipo:'email',destino:'motor'},
 {key:'OPENALEX_MAILTO',grupo:'motor',rotulo:'E-mail para a OpenAlex',tipo:'email',destino:'motor'},
 {key:'OPENALEX_API_KEY',grupo:'motor',rotulo:'Chave da OpenAlex',tipo:'secret',destino:'motor'},
 {key:'SEMANTIC_SCHOLAR_API_KEY',grupo:'motor',rotulo:'Chave do Semantic Scholar',tipo:'secret',destino:'motor'},
 {key:'SPRINGER_API_KEY',grupo:'motor',rotulo:'Chave da Springer Nature',tipo:'secret',destino:'motor',reiniciar:true},
 {key:'CORE_API_KEY',grupo:'motor',rotulo:'Chave do CORE',tipo:'secret',destino:'motor',reiniciar:true},
 {key:'WILEY_TDM_TOKEN',grupo:'motor',rotulo:'Token TDM da Wiley',tipo:'secret',destino:'motor'},
 {key:'SCOPUS_API_KEY',grupo:'motor',rotulo:'Chave da Scopus',tipo:'secret',destino:'motor',reiniciar:true},
 {key:'IEEE_API_KEY',grupo:'motor',rotulo:'Chave do IEEE Xplore',tipo:'secret',destino:'motor',reiniciar:true},
 {key:'EZPROXY_BASE_URL',grupo:'motor',rotulo:'Endereço do EZproxy da biblioteca',tipo:'url',destino:'motor'},
 {key:'EZPROXY_USER',grupo:'motor',rotulo:'Usuário do EZproxy',tipo:'text',destino:'motor'},
 {key:'EZPROXY_PASSWORD',grupo:'motor',rotulo:'Senha do EZproxy',tipo:'secret',destino:'motor'},
 {key:'PAPER_FETCH_PROXY',grupo:'motor',rotulo:'Proxy institucional',tipo:'url',destino:'motor',ajuda:'http://, https:// ou socks5://'},
 {key:'PAPER_FETCH_INSTITUTIONAL',grupo:'motor',rotulo:'Acesso institucional pelos portais das editoras',tipo:'boolean',destino:'motor'},
 {key:'PAPER_FETCH_CLOAK',grupo:'motor',rotulo:'Navegador headless contra Cloudflare',tipo:'boolean',destino:'motor'},
 {key:'PAPER_FETCH_BROWSER',grupo:'motor',rotulo:'Navegador para repositórios institucionais',tipo:'boolean',destino:'motor'},
 {key:'PAPER_FETCH_NO_SCIHUB',grupo:'motor',rotulo:'Desligar Sci-Hub',tipo:'boolean',destino:'motor'},
 {key:'PAPER_FETCH_NO_WAYBACK',grupo:'motor',rotulo:'Desligar Wayback Machine',tipo:'boolean',destino:'motor'},
];

export const ITENS:Record<string,Item>=Object.fromEntries(CATALOGO.map(i=>[i.key,i]));

export type Origem='tela'|'ambiente'|'padrão'|'';
export type Resolvido=Record<string,{valor:string;origem:Origem}>;

export function validar(key:string,bruto:unknown):string{
 const item=ITENS[key];
 if(!item)throw new Error('Configuração desconhecida: '+key+'.');
 if(item.tipo==='boolean'){
  if(bruto===true||bruto==='1'||bruto==='true')return '1';
  // Desligar é apagar: o motor trata qualquer valor presente como ligado.
  if(bruto===false||bruto===''||bruto==='0'||bruto==='false'||bruto==null)return '';
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
  if(!['http:','https:','socks5:','socks5h:'].includes(u.protocol))throw new Error(item.rotulo+': use http, https ou socks5.');
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

export function traduzirErro(msg:string):string{
 const t=msg.toLowerCase();
 if(/\b(401|403)\b|invalid api key|api key not valid|unauthorized|permission denied|credenciais/.test(t))return 'Chave recusada pelo serviço.';
 if(/\b429\b|rate limit|limitou/.test(t))return 'Limite de consultas atingido; tente de novo em instantes.';
 if(/fetch failed|network|timeout|timed out|abort|enotfound|econnrefused/.test(t))return 'Sem conexão com o serviço.';
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
```

- [ ] **Step 4: Rodar e ver passar**

Run: `node tests/settings.mjs`
Expected: `settings: ok`

- [ ] **Step 5: Checkpoint**

Run: `for t in tests/settings.mjs tests/bases.mjs tests/pubmed.mjs; do node "$t" || echo FALHOU $t; done`
Expected: três `ok`, nenhum `FALHOU`.

---

### Task 2: Provedor de IA com preferência e modelo

**Files:**
- Modify: `lib/ai-provider.ts:60-120` (bloco `--- provedores ---` até o fim de `pickProvider`)
- Test: `tests/settings.mjs` (acrescentar ao fim, antes do `console.log`)

**Interfaces:**
- Consumes: nada de outras tasks.
- Produces: `type Preferencia={provedor?:string;modelos?:{anthropic?:string;openai?:string;gemini?:string}}`, `MODELOS_PADRAO`, `pickProvider(env:Env,pref?:Preferencia):Provider|null`. Sem `pref` (ou `provedor:'auto'`), comportamento idêntico ao de hoje. Com provedor explícito sem chave → `null`.

- [ ] **Step 1: Escrever o teste que falha** — acrescentar em `tests/settings.mjs`, antes de `console.log('settings: ok')`:

```js
// ---------------------------------------------------------------------------
// Provedor de IA
// ---------------------------------------------------------------------------
const ai=await load('lib/ai-provider.ts');
assert.equal(ai.pickProvider({OPENAI_API_KEY:'o',GEMINI_API_KEY:'g'}).name,'OpenAI','automático mantém a ordem de hoje');
assert.equal(ai.pickProvider({OPENAI_API_KEY:'o',GEMINI_API_KEY:'g'},{provedor:'auto'}).name,'OpenAI');
assert.equal(ai.pickProvider({OPENAI_API_KEY:'o',GEMINI_API_KEY:'g'},{provedor:'gemini'}).name,'Google','preferência explícita');
assert.equal(ai.pickProvider({OPENAI_API_KEY:'o'},{provedor:'anthropic'}),null,'preferido sem chave não cai em outro às escondidas');
assert.equal(ai.pickProvider({ANTHROPIC_API_KEY:'a'},{modelos:{anthropic:'claude-opus-5-5'}}).model,'claude-opus-5-5');
assert.equal(ai.pickProvider({ANTHROPIC_API_KEY:'a'},{modelos:{anthropic:''}}).model,'claude-sonnet-5','modelo vazio usa o padrão');
assert.equal(ai.pickProvider({}),null);
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `node tests/settings.mjs`
Expected: FAIL em `preferência explícita` (hoje devolve `OpenAI`).

- [ ] **Step 3: Implementar** — substituir em `lib/ai-provider.ts` desde a linha `type Env={ANTHROPIC_API_KEY?...` até o fim de `pickProvider` por:

```ts
type Env={ANTHROPIC_API_KEY?:string;OPENAI_API_KEY?:string;GEMINI_API_KEY?:string};
export type Preferencia={provedor?:string;modelos?:{anthropic?:string;openai?:string;gemini?:string}};
export const MODELOS_PADRAO={anthropic:'claude-sonnet-5',openai:'gpt-4o-mini',gemini:'gemini-2.5-flash'};

function anthropic(key:string,model:string):Provider{return {
 name:'Anthropic',model,
 complete:async(system,user)=>{
  const r=await fetch('https://api.anthropic.com/v1/messages',{
   method:'POST',
   headers:{'content-type':'application/json','x-api-key':key,'anthropic-version':'2023-06-01'},
   body:JSON.stringify({model,max_tokens:8192,system,messages:[{role:'user',content:user}]}),
   signal:AbortSignal.timeout(120000),
  });
  if(!r.ok)throw new Error('Anthropic HTTP '+r.status+': '+(await r.text()).slice(0,300));
  const d:any=await r.json();
  return String(d?.content?.[0]?.text||'');
 }};}

function openai(key:string,model:string):Provider{return {
 name:'OpenAI',model,
 complete:async(system,user)=>{
  const r=await fetch('https://api.openai.com/v1/chat/completions',{
   method:'POST',
   headers:{'content-type':'application/json',authorization:'Bearer '+key},
   body:JSON.stringify({model,temperature:0,response_format:{type:'json_object'},
    messages:[{role:'system',content:system},{role:'user',content:user}]}),
   signal:AbortSignal.timeout(120000),
  });
  if(!r.ok)throw new Error('OpenAI HTTP '+r.status+': '+(await r.text()).slice(0,300));
  const d:any=await r.json();
  return String(d?.choices?.[0]?.message?.content||'');
 }};}

function gemini(key:string,model:string):Provider{return {
 name:'Google',model,
 complete:async(system,user)=>{
  // O modelo vem da tela e entra no caminho da URL: codificado, sempre.
  const r=await fetch('https://generativelanguage.googleapis.com/v1beta/models/'+encodeURIComponent(model)+':generateContent?key='+encodeURIComponent(key),{
   method:'POST',headers:{'content-type':'application/json'},
   body:JSON.stringify({systemInstruction:{parts:[{text:system}]},contents:[{parts:[{text:user}]}],
    generationConfig:{temperature:0,responseMimeType:'application/json'}}),
   signal:AbortSignal.timeout(120000),
  });
  if(!r.ok)throw new Error('Gemini HTTP '+r.status+': '+(await r.text()).slice(0,300));
  const d:any=await r.json();
  return String(d?.candidates?.[0]?.content?.parts?.[0]?.text||'');
 }};}

// Sem preferência, vale a regra de sempre: a primeira chave que existir. Com
// preferência, só aquele provedor — escolher Anthropic e ser atendido pela
// OpenAI sem saber mudaria quem avaliou os artigos.
export function pickProvider(env:Env,pref:Preferencia={}):Provider|null{
 const m=pref.modelos||{};
 const fabricas:Record<string,()=>Provider|null>={
  anthropic:()=>env.ANTHROPIC_API_KEY?anthropic(env.ANTHROPIC_API_KEY,m.anthropic||MODELOS_PADRAO.anthropic):null,
  openai:()=>env.OPENAI_API_KEY?openai(env.OPENAI_API_KEY,m.openai||MODELOS_PADRAO.openai):null,
  gemini:()=>env.GEMINI_API_KEY?gemini(env.GEMINI_API_KEY,m.gemini||MODELOS_PADRAO.gemini):null,
 };
 const escolhido=pref.provedor&&pref.provedor!=='auto'?pref.provedor:'';
 if(escolhido)return fabricas[escolhido]?.()??null;
 return fabricas.anthropic()||fabricas.openai()||fabricas.gemini();
}
```

Atualize também o comentário acima (`// A chave vem do ambiente do Worker...`) para: `// A chave vem de Configurações (ou, na falta, do ambiente do Worker), nunca do repositório.`

- [ ] **Step 4: Rodar e ver passar**

Run: `node tests/settings.mjs && node tests/ai-runner.mjs`
Expected: `settings: ok` e a suíte `ai-runner` passando (garante que o formato de `Provider` não mudou).

- [ ] **Step 5: Checkpoint** — `pnpm exec tsc --noEmit 2>&1 | grep ai-provider || echo tipos-ok` → `tipos-ok`.

---

### Task 3: Motor lê e grava `motor/.env`, com token

**Files:**
- Create: `servico-python/config_env.py`
- Modify: `servico-python/main.py` (imports no topo; `import config_env` + carga antes de `import baixar`; rotas novas após `saude`)
- Modify: `start.py` (`import secrets`; token nas funções `subir_motor` e `subir_orbis`)
- Test: `servico-python/tests/test_config_env.py`

**Interfaces:**
- Produces (Python): `config_env.PERMITIDAS: dict[str, tuple[bool, bool]]` (chave → (segredo, exige reinício)), `ler(arquivo=None)->dict[str,str]`, `carregar_no_ambiente(arquivo=None)->None`, `visao()->dict[str,dict]`, `gravar(mudancas:dict[str,str|None],arquivo=None)->list[str]` (chaves que pedem reinício; lança `ValueError`).
- Produces (HTTP): `GET /config` → `{"itens":{KEY:{"preenchido":bool,"valor":str}}}`; `PUT /config` corpo `{"mudancas":{KEY:str|null}}` → `{"salvas":[...],"reiniciar":[...],"itens":{...}}`. Ambos exigem `X-Orbis-Token`; 403 sem ele; 400 em chave/valor inválido.
- Env: `ORBIS_ENGINE_TOKEN` (gerado pelo `start.py`, entregue ao motor e ao ORBIS).

- [ ] **Step 1: Escrever o teste que falha**

`servico-python/tests/test_config_env.py`:

```python
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config_env  # noqa: E402

MODELO = """# Comentário de topo
# E-mail do Unpaywall
UNPAYWALL_EMAIL=antigo@exemplo.org

# Chave CORE
# CORE_API_KEY=sua_chave

OUTRA_COISA=fica
"""


@pytest.fixture
def arquivo(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text(MODELO, encoding="utf-8")
    # setenv + delenv: o monkeypatch passa a restaurar o valor original no fim,
    # mesmo que `gravar`/`carregar` escrevam em os.environ durante o teste.
    for k in ("UNPAYWALL_EMAIL", "CORE_API_KEY", "PAPER_FETCH_CLOAK", "EZPROXY_PASSWORD", "OUTRA_COISA"):
        monkeypatch.setenv(k, "x")
        monkeypatch.delenv(k)
    monkeypatch.setattr(config_env, "ARQUIVO", f)
    return f


def test_ler_ignora_comentarios(arquivo):
    assert config_env.ler() == {"UNPAYWALL_EMAIL": "antigo@exemplo.org", "OUTRA_COISA": "fica"}


def test_carregar_nao_sobrescreve_o_terminal(arquivo, monkeypatch):
    monkeypatch.setenv("UNPAYWALL_EMAIL", "terminal@exemplo.org")
    config_env.carregar_no_ambiente()
    assert os.environ["UNPAYWALL_EMAIL"] == "terminal@exemplo.org"
    assert os.environ["OUTRA_COISA"] == "fica"


def test_gravar_substitui_descomenta_e_preserva(arquivo):
    reiniciar = config_env.gravar({"UNPAYWALL_EMAIL": "novo@exemplo.org", "CORE_API_KEY": "core-123"})
    texto = arquivo.read_text(encoding="utf-8")
    assert "UNPAYWALL_EMAIL=novo@exemplo.org" in texto
    assert "antigo@exemplo.org" not in texto
    assert "CORE_API_KEY=core-123" in texto
    assert "# CORE_API_KEY=sua_chave" not in texto, "a linha do modelo vira a linha ativa"
    assert "# Comentário de topo" in texto and "OUTRA_COISA=fica" in texto
    assert texto.index("CORE_API_KEY=") < texto.index("OUTRA_COISA="), "mantém a posição do modelo"
    assert os.environ["UNPAYWALL_EMAIL"] == "novo@exemplo.org", "vale na hora"
    assert sorted(reiniciar) == ["CORE_API_KEY", "UNPAYWALL_EMAIL"]


def test_gravar_acrescenta_e_apaga(arquivo):
    config_env.gravar({"PAPER_FETCH_CLOAK": "1"})
    assert arquivo.read_text(encoding="utf-8").rstrip().endswith("PAPER_FETCH_CLOAK=1")
    assert config_env.gravar({"PAPER_FETCH_CLOAK": None}) == []
    assert "PAPER_FETCH_CLOAK" not in arquivo.read_text(encoding="utf-8")
    assert "PAPER_FETCH_CLOAK" not in os.environ


def test_valor_com_espaco_ou_cerquilha_vai_entre_aspas(arquivo):
    config_env.gravar({"EZPROXY_PASSWORD": 'se#nha com "aspas"'})
    assert config_env.ler()["EZPROXY_PASSWORD"] == 'se#nha com "aspas"'


def test_recusa_quebra_de_linha_e_chave_desconhecida(arquivo):
    antes = arquivo.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="quebra de linha"):
        config_env.gravar({"UNPAYWALL_EMAIL": "a@b.co\nHTTP_PROXY=http://mal"})
    with pytest.raises(ValueError, match="não configurável"):
        config_env.gravar({"ORBIS_DATA_DIR": "/tmp"})
    assert arquivo.read_text(encoding="utf-8") == antes, "nada gravado"


def test_visao_mascara_segredo(arquivo, monkeypatch):
    monkeypatch.setenv("CORE_API_KEY", "abcdefghijklmnop")
    monkeypatch.setenv("UNPAYWALL_EMAIL", "a@b.co")
    v = config_env.visao()
    assert v["CORE_API_KEY"] == {"preenchido": True, "valor": "••••mnop"}
    assert v["UNPAYWALL_EMAIL"] == {"preenchido": True, "valor": "a@b.co"}
    assert v["WILEY_TDM_TOKEN"] == {"preenchido": False, "valor": ""}


# --- rotas --------------------------------------------------------------------
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402


@pytest.fixture
def cliente(arquivo, monkeypatch):
    monkeypatch.setattr(main, "TOKEN", "tok")
    return TestClient(main.app)


def test_rota_exige_token(cliente, monkeypatch):
    assert cliente.get("/config").status_code == 403
    assert cliente.get("/config", headers={"X-Orbis-Token": "errado"}).status_code == 403
    monkeypatch.setattr(main, "TOKEN", "")
    r = cliente.get("/config", headers={"X-Orbis-Token": ""})
    assert r.status_code == 403 and "start.py" in r.json()["detail"], "motor iniciado sem token"


def test_rota_grava(cliente, arquivo):
    h = {"X-Orbis-Token": "tok"}
    r = cliente.put("/config", json={"mudancas": {"CORE_API_KEY": "core-abcdefghijk"}}, headers=h)
    assert r.status_code == 200
    assert r.json()["reiniciar"] == ["CORE_API_KEY"]
    assert r.json()["itens"]["CORE_API_KEY"]["valor"] == "••••hijk"
    assert "core-abcdefghijk" not in r.text
    assert cliente.put("/config", json={"mudancas": {"X": "1"}}, headers=h).status_code == 400
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd servico-python && ../motor/.venv/bin/python -m pytest tests/test_config_env.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'config_env'`

- [ ] **Step 3: Implementar `config_env.py`**

`servico-python/config_env.py`:

```python
"""Leitura e escrita do `motor/.env` pela tela de Configurações do ORBIS.

Até aqui o serviço nunca lia esse arquivo: só `motor/src/search` chamava
`load_dotenv`. As chaves escritas nele não chegavam aos downloads. Agora o
serviço carrega o arquivo na partida (sem sobrescrever o que veio do terminal)
e a tela grava nele sem desmontar o modelo: comentários e ordem ficam onde
estavam.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

ARQUIVO = Path(__file__).resolve().parent.parent / "motor" / ".env"

# chave -> (é segredo, exige reiniciar o motor)
# "Exige reiniciar" = o código do motor lê a variável uma vez, na importação
# (ex.: EMAIL e CORE_API_KEY em fetch.py). As demais valem na próxima chamada.
PERMITIDAS: dict[str, tuple[bool, bool]] = {
    "UNPAYWALL_EMAIL": (False, True),
    "ELSEVIER_API_KEY": (True, False),
    "ELSEVIER_INST_TOKEN": (True, False),
    "ANTHROPIC_API_KEY": (True, False),
    "OPENAI_API_KEY": (True, False),
    "GEMINI_API_KEY": (True, False),
    "CROSSREF_MAILTO": (False, False),
    "OPENALEX_MAILTO": (False, False),
    "OPENALEX_API_KEY": (True, False),
    "SEMANTIC_SCHOLAR_API_KEY": (True, False),
    "SPRINGER_API_KEY": (True, True),
    "CORE_API_KEY": (True, True),
    "WILEY_TDM_TOKEN": (True, False),
    "SCOPUS_API_KEY": (True, True),
    "IEEE_API_KEY": (True, True),
    "EZPROXY_BASE_URL": (False, False),
    "EZPROXY_USER": (False, False),
    "EZPROXY_PASSWORD": (True, False),
    "PAPER_FETCH_PROXY": (False, False),
    "PAPER_FETCH_INSTITUTIONAL": (False, False),
    "PAPER_FETCH_CLOAK": (False, False),
    "PAPER_FETCH_BROWSER": (False, False),
    "PAPER_FETCH_NO_SCIHUB": (False, False),
    "PAPER_FETCH_NO_WAYBACK": (False, False),
}

_LINHA = re.compile(r"^\s*(#\s*)?([A-Z][A-Z0-9_]*)\s*=(.*)$")
_PRECISA_ASPAS = re.compile(r"[\s#\"'\\]")


def _arquivo(arquivo: Path | None) -> Path:
    return arquivo or ARQUIVO


def _decodificar(bruto: str) -> str:
    v = bruto.strip()
    if len(v) >= 2 and v[0] == v[-1] == '"':
        return v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1]
    return v


def _codificar(valor: str) -> str:
    if not _PRECISA_ASPAS.search(valor):
        return valor
    return '"' + valor.replace("\\", "\\\\").replace('"', '\\"') + '"'


def ler(arquivo: Path | None = None) -> dict[str, str]:
    f = _arquivo(arquivo)
    if not f.exists():
        return {}
    out: dict[str, str] = {}
    for linha in f.read_text(encoding="utf-8").splitlines():
        m = _LINHA.match(linha)
        if m and not m.group(1):
            out[m.group(2)] = _decodificar(m.group(3))
    return out


def carregar_no_ambiente(arquivo: Path | None = None) -> None:
    # setdefault: o que veio do terminal continua mandando.
    for k, v in ler(arquivo).items():
        os.environ.setdefault(k, v)


def mascarar(valor: str) -> str:
    if not valor:
        return ""
    return "••••" if len(valor) < 12 else "••••" + valor[-4:]


def visao() -> dict[str, dict]:
    out = {}
    for k, (segredo, _r) in PERMITIDAS.items():
        v = os.environ.get(k, "").strip()
        out[k] = {"preenchido": bool(v), "valor": mascarar(v) if segredo else v}
    return out


def gravar(mudancas: dict[str, str | None], arquivo: Path | None = None) -> list[str]:
    # Valida tudo antes de tocar no arquivo: ou grava tudo, ou nada.
    for k, v in mudancas.items():
        if k not in PERMITIDAS:
            raise ValueError(f"{k} não configurável pela tela.")
        if v is not None and re.search(r"[\r\n\0]", v):
            raise ValueError(f"{k}: não pode ter quebra de linha.")
    f = _arquivo(arquivo)
    linhas = f.read_text(encoding="utf-8").splitlines() if f.exists() else []
    pendentes = dict(mudancas)
    saida: list[str] = []
    # 1ª passada: linhas ativas (substituir ou apagar).
    for linha in linhas:
        m = _LINHA.match(linha)
        if m and not m.group(1) and m.group(2) in pendentes:
            v = pendentes.pop(m.group(2))
            if v is not None and v != "":
                saida.append(f"{m.group(2)}={_codificar(v)}")
            continue
        saida.append(linha)
    # 2ª passada: a linha comentada do modelo vira a ativa, no mesmo lugar.
    for i, linha in enumerate(saida):
        m = _LINHA.match(linha)
        if m and m.group(1) and m.group(2) in pendentes and pendentes[m.group(2)]:
            k = m.group(2)
            saida[i] = f"{k}={_codificar(pendentes.pop(k))}"
    # O que sobrou (e não é apagar) vai para o fim.
    for k, v in pendentes.items():
        if v:
            saida.append(f"{k}={_codificar(v)}")
    tmp = f.with_suffix(".tmp")
    tmp.write_text("\n".join(saida) + "\n", encoding="utf-8")
    tmp.replace(f)
    for k, v in mudancas.items():
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)
    return sorted(k for k in mudancas if PERMITIDAS[k][1])
```

- [ ] **Step 4: Ligar em `main.py`**

Em `servico-python/main.py`, trocar a linha `from fastapi import FastAPI, HTTPException` por:

```python
import hmac

from fastapi import FastAPI, Header, HTTPException
```

Logo **antes** de `import baixar as motor_baixar`, inserir:

```python
# Antes de qualquer import do pipeline: parte dele lê o ambiente na importação.
import config_env
config_env.carregar_no_ambiente()
```

Logo **depois** da função `saude()`, inserir:

```python
# Segredo que o start.py gera a cada execução e entrega também ao ORBIS. Sem
# ele, qualquer página aberta no navegador poderia gravar no motor local (o
# CORS aceita qualquer origem), por exemplo um HTTP_PROXY.
TOKEN = os.environ.get("ORBIS_ENGINE_TOKEN", "").strip()


def _autorizar(token: str | None) -> None:
    if not TOKEN:
        raise HTTPException(403, "O motor foi iniciado sem ORBIS_ENGINE_TOKEN. Suba o ORBIS pelo start.py para editar as configurações.")
    if not hmac.compare_digest(token or "", TOKEN):
        raise HTTPException(403, "Token do ORBIS ausente ou inválido.")


@app.get("/config")
def config_ler(x_orbis_token: str | None = Header(default=None)):
    _autorizar(x_orbis_token)
    return {"itens": config_env.visao()}


class PedidoConfig(BaseModel):
    mudancas: dict[str, str | None]


@app.put("/config")
def config_gravar(p: PedidoConfig, x_orbis_token: str | None = Header(default=None)):
    _autorizar(x_orbis_token)
    try:
        reiniciar = config_env.gravar(p.mudancas)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"salvas": sorted(p.mudancas), "reiniciar": reiniciar, "itens": config_env.visao()}
```

- [ ] **Step 5: Token no `start.py`**

Em `start.py`, acrescentar `import secrets` na lista de imports (em ordem alfabética, entre `import os` e `import shlex`). Logo após a definição de `SERVICO = RAIZ / "servico-python"`, acrescentar:

```python
# Um segredo por execução: o ORBIS o envia ao motor para gravar configurações.
TOKEN_MOTOR = secrets.token_urlsafe(32)
```

Em `subir_motor`, trocar `ambiente = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"}` por:

```python
    ambiente = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1", "ORBIS_ENGINE_TOKEN": TOKEN_MOTOR}
```

Em `subir_orbis`, trocar o bloco

```python
    if com_motor:
        ambiente["ORBIS_ENGINE_URL"] = f"http://127.0.0.1:{PORTA_MOTOR}"
```

por

```python
    if com_motor:
        ambiente["ORBIS_ENGINE_URL"] = f"http://127.0.0.1:{PORTA_MOTOR}"
        ambiente["ORBIS_ENGINE_TOKEN"] = TOKEN_MOTOR
```

- [ ] **Step 6: Paridade entre as duas listas** — acrescentar em `tests/settings.mjs`, antes de `console.log('settings: ok')`:

```js
// ---------------------------------------------------------------------------
// Paridade: o que o ORBIS manda ao motor = o que o motor aceita, com as mesmas
// marcas de segredo e de reinício.
// ---------------------------------------------------------------------------
const py=await readFile('servico-python/config_env.py','utf8');
const doMotor=Object.fromEntries([...py.matchAll(/^\s+"([A-Z0-9_]+)":\s*\((True|False),\s*(True|False)\),/gm)].map(m=>[m[1],{segredo:m[2]==='True',reiniciar:m[3]==='True'}]));
const doCatalogo=Object.fromEntries(st.CATALOGO.filter(i=>i.destino!=='orbis').map(i=>[i.key,{segredo:i.tipo==='secret',reiniciar:!!i.reiniciar}]));
assert.deepEqual(doMotor,doCatalogo,'catálogo e PERMITIDAS do motor divergem');
```

Run: `node tests/settings.mjs`
Expected: `settings: ok`

- [ ] **Step 7: Rodar e ver passar**

Run: `cd servico-python && ../motor/.venv/bin/python -m pytest -q`
Expected: todos passam, incluindo os 9 de `test_config_env.py` (os de rota podem ser pulados se `httpx` faltar — confirme que **não** foram pulados: `-rs` não deve listar `test_config_env`).

Run: `python3 -c "import ast,sys;ast.parse(open('start.py').read())" && echo start-ok`
Expected: `start-ok`

---

### Task 4: Persistência no ORBIS e ponte com o motor

**Files:**
- Modify: `db/schema.ts` (nova tabela)
- Create: `drizzle/0003_*.sql` (gerado)
- Create: `lib/settings-store.ts`
- Modify: `lib/python-bridge.ts` (funções `engineConfig`, `saveEngineConfig`)

**Interfaces:**
- Consumes: `resolver`, `valores`, `validar`, `CATALOGO`, `type Resolvido` (Task 1).
- Produces:
  - `loadSettings():Promise<Resolvido>` — nunca lança por falta de tabela/banco; cai no ambiente.
  - `settingsValues():Promise<Record<string,string>>`
  - `gravarSettings(m:Record<string,string|null>):Promise<void>` — `null` apaga; lança se o banco falhar.
  - `type EngineConfig={itens:Record<string,{preenchido:boolean;valor:string}>}`
  - `engineConfig(env:any):Promise<EngineConfig|null>` — `null` se fora do ar **ou** recusando.
  - `saveEngineConfig(env:any,m:Record<string,string|null>):Promise<{salvas:string[];reiniciar:string[]}>` — lança com mensagem em português.

Esta task não tem teste próprio: tudo nela é E/S (D1, rede) e é exercitado pela integração da Task 5. O checkpoint é a checagem de tipos.

- [ ] **Step 1: Tabela no schema** — acrescentar ao fim de `db/schema.ts`:

```ts
// Configurações da instalação (Configurações na interface). Chave = nome da
// variável de ambiente que o valor substitui; ver lib/settings.ts.
export const settings=sqliteTable('settings',{key:text('key').primaryKey(),value:text('value').notNull(),updated:text('updated').notNull()});
```

- [ ] **Step 2: Gerar a migração**

Run: `pnpm db:generate`
Expected: cria `drizzle/0003_<nome>.sql` contendo `CREATE TABLE \`settings\`` e atualiza `drizzle/meta/_journal.json`. Confira: `cat drizzle/0003_*.sql`. O `start.py` aplica migrações novas sozinho na próxima execução.

- [ ] **Step 3: `lib/settings-store.ts`**

```ts
// Onde as configurações da tela moram (D1) e como o servidor as lê.
//
// A leitura nunca derruba quem chama: sem banco ou sem a tabela (instalação
// que ainda não rodou a migração), valem o ambiente e os padrões — exatamente
// o comportamento de antes desta tela existir.
import {env as cfEnv} from 'cloudflare:workers';
import {database} from '@/lib/server';
import {CATALOGO,resolver,valores,type Resolvido} from '@/lib/settings';

function ambiente():Record<string,unknown>{
 const out:Record<string,unknown>={};
 for(const i of CATALOGO)for(const k of [i.key,...(i.aliases||[])]){
  const v=(cfEnv as any)?.[k]??(globalThis as any)[k]??(typeof process!=='undefined'?process.env?.[k]:undefined);
  if(v!=null&&v!=='')out[k]=v;
 }
 return out;
}

async function salvos():Promise<Record<string,string>>{
 try{
  const r=await database().prepare('SELECT key,value FROM settings').all<{key:string;value:string}>();
  return Object.fromEntries((r.results||[]).map(x=>[x.key,x.value]));
 }catch{return {};}
}

export async function loadSettings():Promise<Resolvido>{return resolver(await salvos(),ambiente());}
export async function settingsValues():Promise<Record<string,string>>{return valores(await loadSettings());}

export async function gravarSettings(m:Record<string,string|null>):Promise<void>{
 const db=database(),at=new Date().toISOString();
 const ops=Object.entries(m).map(([k,v])=>v==null
  ?db.prepare('DELETE FROM settings WHERE key=?').bind(k)
  :db.prepare('INSERT INTO settings(key,value,updated) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated=excluded.updated').bind(k,v,at));
 if(ops.length)await db.batch(ops);
}
```

- [ ] **Step 4: Ponte com o motor** — acrescentar ao fim de `lib/python-bridge.ts`:

```ts
// --- configurações do motor -------------------------------------------------
// Única rota do motor que exige token: ela grava no motor/.env. O token vem do
// start.py (ORBIS_ENGINE_TOKEN) e só trafega entre servidores.
export type EngineConfig={itens:Record<string,{preenchido:boolean;valor:string}>};

async function callConfig(env:any,method:'GET'|'PUT',body?:unknown){
 const url=engineUrl(env);
 if(!url)throw new Error('O motor não está configurado neste ORBIS.');
 let r:Response;
 try{
  r=await fetch(url+'/config',{method,
   headers:{Accept:'application/json','x-orbis-token':String(env?.ORBIS_ENGINE_TOKEN||''),...(body?{'content-type':'application/json'}:{})},
   body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(8000)});
 }catch{throw new Error('O motor não está no ar.');}
 const d:any=await r.json().catch(()=>({}));
 if(!r.ok)throw new Error(String(d?.detail||'O motor recusou (HTTP '+r.status+').'));
 return d;
}

// Leitura: fora do ar ou recusando, a tela mostra o bloco do motor desabilitado.
export async function engineConfig(env:any):Promise<EngineConfig|null>{
 try{const d=await callConfig(env,'GET');return {itens:d?.itens||{}};}catch{return null;}
}

// Escrita: o erro sobe, porque a tela precisa dizer o que ficou pendente.
export async function saveEngineConfig(env:any,mudancas:Record<string,string|null>):Promise<{salvas:string[];reiniciar:string[]}>{
 const d=await callConfig(env,'PUT',{mudancas});
 return {salvas:d?.salvas||[],reiniciar:d?.reiniciar||[]};
}
```

- [ ] **Step 5: Checkpoint**

Run: `pnpm exec tsc --noEmit 2>&1 | grep -E "settings|python-bridge|schema" || echo tipos-ok`
Expected: `tipos-ok`

---

### Task 5: Rota `/api/settings` e testes de credencial

**Files:**
- Create: `lib/settings-test.ts`
- Create: `app/api/settings/route.ts`
- Test: `tests/settings-integration.mjs`

**Interfaces:**
- Consumes: Task 1 (`validar`, `separarPorDestino`, `visaoPublica`, `ALVOS`, `traduzirErro`, `semSegredos`, `type Alvo`), Task 2 (`pickProvider`), Task 4 (`loadSettings`, `settingsValues`, `gravarSettings`, `engineConfig`, `saveEngineConfig`), fontes existentes (`searchLilacs`, `searchEmbase`, `searchUrl` do PubMed).
- Produces (HTTP):
  - `GET /api/settings` → `{orbis:{KEY:{valor,origem,preenchido}}, motor:{online:false}|{online:true,itens}}`
  - `PUT /api/settings` corpo `{mudancas:{KEY:valor|null}}` → `{orbis, salvasNoOrbis:string[], motor:{enviado:true,salvas,reiniciar}|{enviado:false,pendentes,erro}|{enviado:false}}`
  - `POST /api/settings` corpo `{acao:'testar',alvo}` → `{ok:boolean,detalhe:string}`
  - Todas exigem login (`identity`).

- [ ] **Step 1: Escrever o teste de integração que falha**

`tests/settings-integration.mjs`:

```js
import {createRequire} from 'node:module';import {realpathSync,readFileSync,readdirSync} from 'node:fs';import assert from 'node:assert/strict';
const require=createRequire(realpathSync('./node_modules/wrangler')+'/package.json');const {Miniflare}=require('miniflare');

// Motor falso: guarda o que recebe e exige o token, como o servico-python.
const recebido={};let motorFora=false,tokensVistos=[];
async function motor(req){
 const u=new URL(req.url);
 // Fora do ar = conexão recusada, não uma resposta HTTP.
 if(motorFora)throw new Error('connection refused');
 if(u.origin!=='http://motor.test')return new Response('rede bloqueada no teste',{status:599});
 if(u.pathname==='/saude')return Response.json({ok:true,recursos:[],faltando:[],pasta_pdfs:'/d'});
 tokensVistos.push(req.headers.get('x-orbis-token'));
 if(req.headers.get('x-orbis-token')!=='tok')return Response.json({detail:'Token do ORBIS ausente ou inválido.'},{status:403});
 if(u.pathname==='/config'&&req.method==='PUT'){const b=await req.json();Object.assign(recebido,b.mudancas);return Response.json({salvas:Object.keys(b.mudancas).sort(),reiniciar:Object.keys(b.mudancas).filter(k=>['CORE_API_KEY','UNPAYWALL_EMAIL'].includes(k)).sort(),itens:{}});}
 if(u.pathname==='/config')return Response.json({itens:{CORE_API_KEY:{preenchido:!!recebido.CORE_API_KEY,valor:recebido.CORE_API_KEY?'••••'+recebido.CORE_API_KEY.slice(-4):''}}});
 return new Response('?',{status:404});
}
const modulos=[{type:'ESModule',path:'dist/server/index.js'},...readdirSync('dist/server',{recursive:true}).filter(f=>/\.(js|mjs)$/.test(f)&&f!=='index.js').map(f=>({type:'ESModule',path:'dist/server/'+f}))];
const mf=new Miniflare({modules:modulos,compatibilityDate:'2026-05-15',compatibilityFlags:['nodejs_compat'],d1Databases:['DB'],r2Buckets:['BUCKET'],
 bindings:{ORBIS_ENGINE_URL:'http://motor.test',ORBIS_ENGINE_TOKEN:'tok',NCBI_API_KEY:'ambiente-ncbi-123456'},outboundService:motor});
try{
 const db=await mf.getD1Database('DB');
 for(const f of readdirSync('drizzle').filter(f=>f.endsWith('.sql')).sort())for(const sql of readFileSync('drizzle/'+f,'utf8').split('--> statement-breakpoint'))if(sql.trim())await db.prepare(sql).run();
 const auth={'oai-authenticated-user-id':'test-a','oai-authenticated-user-email':'test-a@example.test'};
 async function req(method='GET',data,headers=auth){const h={...headers};if(data!==undefined)h['content-type']='application/json';const r=await mf.dispatchFetch('https://test.example/api/settings',{method,headers:h,body:data!==undefined?JSON.stringify(data):undefined});const raw=await r.text();return {status:r.status,raw,data:JSON.parse(raw)};}

 // Login obrigatório.
 assert.equal((await req('GET',undefined,{})).status,401);

 // Ambiente aparece mascarado, com origem.
 let r=await req();assert.equal(r.status,200);
 assert.equal(r.data.orbis.NCBI_API_KEY.origem,'ambiente');
 assert.equal(r.data.orbis.NCBI_API_KEY.valor,'••••3456');
 assert.ok(!r.raw.includes('ambiente-ncbi-123456'),'segredo do ambiente não sai inteiro');
 assert.equal(r.data.motor.online,true);

 // Tela vence o ambiente.
 r=await req('PUT',{mudancas:{NCBI_API_KEY:'tela-ncbi-abcdefghijk',ORBIS_LOTE_SIMULTANEOS:'3'}});
 assert.equal(r.status,200,r.raw);assert.ok(!r.raw.includes('tela-ncbi-abcdefghijk'));
 r=await req();assert.equal(r.data.orbis.NCBI_API_KEY.origem,'tela');assert.equal(r.data.orbis.NCBI_API_KEY.valor,'••••hijk');assert.equal(r.data.orbis.ORBIS_LOTE_SIMULTANEOS.valor,'3');

 // Tudo ou nada: um item inválido derruba o pedido inteiro.
 r=await req('PUT',{mudancas:{ORBIS_LOTE_SIMULTANEOS:'4',NCBI_EMAIL:'a@b.co\nHTTP_PROXY=x'}});assert.equal(r.status,400);assert.match(r.data.message,/quebra de linha/);
 assert.equal((await req()).data.orbis.ORBIS_LOTE_SIMULTANEOS.valor,'3','nada gravado');
 assert.equal((await req('PUT',{mudancas:{NAO_EXISTE:'1'}})).status,400);
 assert.equal((await req('PUT',{mudancas:[]})).status,400);

 // Itens do motor seguem com token; `ambos` fica nos dois lados.
 r=await req('PUT',{mudancas:{CORE_API_KEY:'core-123456789012',UNPAYWALL_EMAIL:'a@b.co'}});
 assert.equal(r.status,200,r.raw);assert.equal(r.data.motor.enviado,true);assert.deepEqual(r.data.motor.reiniciar,['CORE_API_KEY','UNPAYWALL_EMAIL']);
 assert.deepEqual(recebido,{CORE_API_KEY:'core-123456789012',UNPAYWALL_EMAIL:'a@b.co'});
 assert.ok(tokensVistos.every(t=>t==='tok'));
 assert.deepEqual(r.data.salvasNoOrbis,['UNPAYWALL_EMAIL'],'CORE é só do motor');
 assert.equal(r.data.orbis.UNPAYWALL_EMAIL.origem,'tela');

 // Apagar volta ao ambiente.
 r=await req('PUT',{mudancas:{NCBI_API_KEY:null}});assert.equal(r.data.orbis.NCBI_API_KEY.origem,'ambiente');

 // Motor fora do ar: ORBIS salva, resposta diz o que ficou pendente.
 motorFora=true;
 r=await req('PUT',{mudancas:{UNPAYWALL_EMAIL:'c@d.co'}});
 assert.equal(r.status,200,r.raw);assert.equal(r.data.motor.enviado,false);assert.deepEqual(r.data.motor.pendentes,['UNPAYWALL_EMAIL']);assert.match(r.data.motor.erro,/não está no ar/);
 assert.equal(r.data.orbis.UNPAYWALL_EMAIL.valor,'c@d.co');
 assert.equal((await req()).data.motor.online,false);
 motorFora=false;

 // Teste de credencial sem chave não sai para a rede.
 r=await req('POST',{acao:'testar',alvo:'embase'});assert.equal(r.status,200);assert.equal(r.data.ok,false);assert.match(r.data.detalhe,/Sem chave/);
 assert.equal((await req('POST',{acao:'testar',alvo:'nada'})).status,400);

 // Banco sem a tabela (migração não aplicada): continua respondendo pelo ambiente.
 await db.prepare('DROP TABLE settings').run();
 r=await req();assert.equal(r.status,200);assert.equal(r.data.orbis.NCBI_API_KEY.origem,'ambiente');

 console.log('PASS: configurações — login, máscara, precedência, tudo-ou-nada, motor com token, motor fora do ar, banco sem tabela');
}finally{await mf.dispose()}
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pnpm build && node tests/settings-integration.mjs`
Expected: FAIL — `GET /api/settings` responde 404 (rota não existe).

- [ ] **Step 3: `lib/settings-test.ts`**

```ts
// Botão "Testar" da tela: uma chamada real mínima por credencial. A resposta
// nunca carrega o valor da chave, nem quando o serviço o devolve no erro.
import {searchEmbase} from '@/lib/sources/embase-search';
import {searchLilacs} from '@/lib/sources/lilacs-search';
import {searchUrl as pubmedUrl} from '@/lib/sources/pubmed-search';
import {pickProvider} from '@/lib/ai-provider';
import {traduzirErro,semSegredos,type Alvo} from '@/lib/settings';

type Resultado={ok:boolean;detalhe:string};

export async function testar(alvo:Alvo,v:Record<string,string>):Promise<Resultado>{
 try{return await executar(alvo,v);}
 catch(e:any){return {ok:false,detalhe:semSegredos(traduzirErro(String(e?.message||e)),v)};}
}

async function executar(alvo:Alvo,v:Record<string,string>):Promise<Resultado>{
 if(alvo==='ncbi'){
  const r=await fetch(pubmedUrl('cancer',{retmax:1,apiKey:v.NCBI_API_KEY||undefined,email:v.NCBI_EMAIL||undefined}),{signal:AbortSignal.timeout(15000)});
  await r.body?.cancel();
  if(!r.ok)throw new Error('HTTP '+r.status);
  return {ok:true,detalhe:v.NCBI_API_KEY?'PubMed respondeu com a sua chave.':'PubMed respondeu (sem chave: limite menor de consultas).'};
 }
 if(alvo==='lilacs'){
  const r=await searchLilacs('dengue',{retmax:1});
  return {ok:true,detalhe:'LILACS respondeu ('+r.total+' registros para "dengue").'};
 }
 if(alvo==='embase'){
  if(!v.ELSEVIER_API_KEY)return {ok:false,detalhe:'Sem chave da Elsevier cadastrada.'};
  const r=await searchEmbase('cancer',{retmax:1,apiKey:v.ELSEVIER_API_KEY,instToken:v.ELSEVIER_INST_TOKEN||undefined});
  return {ok:true,detalhe:'Embase respondeu ('+r.total+' registros).'};
 }
 if(alvo==='unpaywall'){
  if(!v.UNPAYWALL_EMAIL)return {ok:false,detalhe:'Sem e-mail do Unpaywall cadastrado.'};
  const r=await fetch('https://api.unpaywall.org/v2/10.1038/nature12373?email='+encodeURIComponent(v.UNPAYWALL_EMAIL),{signal:AbortSignal.timeout(15000)});
  await r.body?.cancel();
  if(!r.ok)throw new Error('HTTP '+r.status);
  return {ok:true,detalhe:'Unpaywall respondeu.'};
 }
 const chave={anthropic:v.ANTHROPIC_API_KEY,openai:v.OPENAI_API_KEY,gemini:v.GEMINI_API_KEY}[alvo];
 if(!chave)return {ok:false,detalhe:'Sem chave cadastrada.'};
 const p=pickProvider(v,{provedor:alvo,modelos:{anthropic:v.ORBIS_IA_MODELO_ANTHROPIC,openai:v.ORBIS_IA_MODELO_OPENAI,gemini:v.ORBIS_IA_MODELO_GEMINI}})!;
 // A OpenAI em modo JSON exige a palavra "JSON" no pedido.
 await p.complete('Responda somente com o JSON {"ok":true}.','teste');
 return {ok:true,detalhe:p.name+' ('+p.model+') respondeu.'};
}
```

- [ ] **Step 4: `app/api/settings/route.ts`**

```ts
import {env} from 'cloudflare:workers';
import {identity,body,ok,fail,ApiError} from '@/lib/server';
import {ALVOS,validar,visaoPublica,separarPorDestino,type Alvo} from '@/lib/settings';
import {loadSettings,settingsValues,gravarSettings} from '@/lib/settings-store';
import {engineConfig,saveEngineConfig} from '@/lib/python-bridge';
import {testar} from '@/lib/settings-test';

export async function GET(r:Request){try{
 await identity(r);
 const [orbis,motor]=await Promise.all([loadSettings(),engineConfig(env)]);
 return ok({orbis:visaoPublica(orbis),motor:motor?{online:true,itens:motor.itens}:{online:false}});
}catch(e){return fail(e)}}

export async function PUT(r:Request){try{
 await identity(r);
 const m=(await body(r))?.mudancas;
 if(!m||typeof m!=='object'||Array.isArray(m))throw new ApiError(400,'Envie as mudanças em "mudancas".');
 // Valida tudo antes de gravar qualquer coisa: ou o pedido inteiro vale, ou nada.
 const validas:Record<string,string|null>={};
 for(const [k,v] of Object.entries(m)){try{validas[k]=v===null?null:validar(k,v)}catch(e:any){throw new ApiError(400,e.message)}}
 const {orbis,motor}=separarPorDestino(validas);
 if(Object.keys(orbis).length)await gravarSettings(orbis);
 let motorRes:any={enviado:false};
 if(Object.keys(motor).length){
  try{motorRes={enviado:true,...await saveEngineConfig(env,motor)}}
  catch(e:any){motorRes={enviado:false,pendentes:Object.keys(motor),erro:e.message}}
 }
 return ok({orbis:visaoPublica(await loadSettings()),salvasNoOrbis:Object.keys(orbis),motor:motorRes});
}catch(e){return fail(e)}}

export async function POST(r:Request){try{
 await identity(r);
 const b=await body(r);
 if(b?.acao!=='testar'||!(ALVOS as readonly string[]).includes(b?.alvo))throw new ApiError(400,'Teste desconhecido.');
 return ok(await testar(b.alvo as Alvo,await settingsValues()));
}catch(e){return fail(e)}}
```

- [ ] **Step 5: Rodar e ver passar**

Run: `pnpm build && node tests/settings-integration.mjs`
Expected: `PASS: configurações — ...`

- [ ] **Step 6: Checkpoint**

Run: `pnpm exec tsc --noEmit && for t in tests/*.mjs; do node "$t" >/dev/null 2>&1 || echo FALHOU $t; done`
Expected: nenhuma linha `FALHOU` (inclui `worker-integration` e `motor-integration`, que usam o mesmo build).

---

### Task 6: Quem passa a ler as configurações

**Files:**
- Modify: `app/api/projects/[id]/search/route.ts` (busca por base e resolução por DOI)
- Modify: `app/api/projects/[id]/route.ts:40-41` (triagem por IA)
- Modify: `lib/article-resolver.ts:23` e `:34` (e-mail do Unpaywall)
- Modify: `lib/incorporate-pdf.ts:15`

**Interfaces:**
- Consumes: `settingsValues()` (Task 4), `pickProvider(env,pref)` (Task 2).
- Produces: `resolveArticle(input:string,refresh=false,opts:{unpaywallEmail?:string}={})` — `opts` opcional; sem ele, comportamento de hoje.

- [ ] **Step 1: Busca** — em `app/api/projects/[id]/search/route.ts`, acrescentar o import:

```ts
import {settingsValues} from '@/lib/settings-store';
```

Trocar as duas linhas

```ts
 const g=globalThis as any,env=(k:string)=>g[k]||process.env?.[k];
 let achado;
 try{achado=await searchBase(base,termo,Number(b.limit)||500,{NCBI_API_KEY:env('NCBI_API_KEY'),NCBI_EMAIL:env('NCBI_EMAIL'),EMBASE_API_KEY:env('EMBASE_API_KEY'),ELSEVIER_API_KEY:env('ELSEVIER_API_KEY'),EMBASE_INST_TOKEN:env('EMBASE_INST_TOKEN'),ELSEVIER_INST_TOKEN:env('ELSEVIER_INST_TOKEN')});}
```

por

```ts
 const v=await settingsValues();
 let achado;
 try{achado=await searchBase(base,termo,Number(b.limit)||Number(v.ORBIS_BUSCA_LIMITE)||500,v);}
```

E, na resolução por DOI, trocar `try{const found=await resolveArticle(doi);` por:

```ts
try{const found=await resolveArticle(doi,false,{unpaywallEmail:(await settingsValues()).UNPAYWALL_EMAIL});
```

- [ ] **Step 2: Resolver** — em `lib/article-resolver.ts`, trocar a assinatura

```ts
export async function resolveArticle(input:string,refresh=false):Promise<Article>{
```

por

```ts
// `opts.unpaywallEmail` vem de Configurações; sem ele, vale o ambiente.
export async function resolveArticle(input:string,refresh=false,opts:{unpaywallEmail?:string}={}):Promise<Article>{
```

e a linha

```ts
 const up=await unpaywall(doi,(globalThis as any).UNPAYWALL_EMAIL||process.env?.UNPAYWALL_EMAIL||'');
```

por

```ts
 const up=await unpaywall(doi,opts.unpaywallEmail||(globalThis as any).UNPAYWALL_EMAIL||process.env?.UNPAYWALL_EMAIL||'');
```

- [ ] **Step 3: Incorporação** — em `lib/incorporate-pdf.ts`, acrescentar `import {settingsValues} from '@/lib/settings-store';` aos imports e trocar `const resolved=await resolveArticle(doi,true),` por:

```ts
  const resolved=await resolveArticle(doi,true,{unpaywallEmail:(await settingsValues()).UNPAYWALL_EMAIL}),
```

- [ ] **Step 4: Triagem por IA** — em `app/api/projects/[id]/route.ts`, acrescentar `import {settingsValues} from '@/lib/settings-store';` e trocar

```ts
 const provider=pickProvider(globalThis as any);
 if(!provider)throw new ApiError(400,'Nenhuma chave de provedor de IA está configurada neste ambiente. Use a importação manual.');
```

por

```ts
 const v=await settingsValues();
 const provider=pickProvider(v,{provedor:v.ORBIS_IA_PROVEDOR,modelos:{anthropic:v.ORBIS_IA_MODELO_ANTHROPIC,openai:v.ORBIS_IA_MODELO_OPENAI,gemini:v.ORBIS_IA_MODELO_GEMINI}});
 if(!provider)throw new ApiError(400,v.ORBIS_IA_PROVEDOR&&v.ORBIS_IA_PROVEDOR!=='auto'?'O provedor escolhido em Configurações não tem chave cadastrada. Cadastre a chave ou use a importação manual.':'Nenhuma chave de provedor de IA está configurada. Cadastre uma em Configurações ou use a importação manual.');
```

- [ ] **Step 5: Verificar**

Run: `pnpm exec tsc --noEmit && pnpm build && node tests/worker-integration.mjs && node tests/motor-integration.mjs && node tests/settings-integration.mjs`
Expected: os três `PASS` — as rotas antigas continuam funcionando sem nenhuma configuração salva.

---

### Task 7: Tela de Configurações

**Files:**
- Create: `app/settings-panel.tsx`
- Modify: `app/workspace.tsx` (imports, estado, carga, barra lateral, área principal, lote, modo de download)

**Interfaces:**
- Consumes: `CATALOGO`, `GRUPOS`, `ROTULO_ALVO`, `simultaneos`, `type Item` (Task 1); rotas da Task 5; `isBase` de `@/lib/sources/bases`.
- Produces: `<SettingsPanel onChange={(orbis)=>void}/>` — chama `onChange` com a visão pública do ORBIS a cada carga, para o `workspace` aplicar base padrão, lote e modo de download.

- [ ] **Step 1: `app/settings-panel.tsx`**

```tsx
'use client';
import {useEffect,useState} from 'react';
import {Button} from '@/components/ui/button';import {Input} from '@/components/ui/input';import {Badge} from '@/components/ui/badge';
import {NativeSelect,NativeSelectOption} from '@/components/ui/native-select';
import {toast} from 'sonner';
import {CATALOGO,GRUPOS,ROTULO_ALVO,type Item} from '@/lib/settings';

async function request(url:string,method='GET',data?:any){const r=await fetch(url,{method,headers:data?{'content-type':'application/json'}:undefined,body:data?JSON.stringify(data):undefined});const x:any=await r.json();if(!r.ok)throw Error(x.message);return x}
const ORIGEM:Record<string,string>={tela:'salvo aqui',ambiente:'do ambiente','padrão':'padrão'};

function Campo({item,atual,rascunho,desabilitado,mudar}:{item:Item;atual:any;rascunho:any;desabilitado:boolean;mudar:(k:string,v:any)=>void}){
 const origem=atual?.origem?ORIGEM[atual.origem]:'';
 let controle;
 if(item.tipo==='boolean'){
  const ligado=rascunho!==undefined?!!rascunho:!!atual?.preenchido;
  controle=<label className="choices"><input type="checkbox" checked={ligado} disabled={desabilitado} onChange={e=>mudar(item.key,e.target.checked)}/>{ligado?'Ligado':'Desligado'}</label>;
 }else if(item.tipo==='select'){
  controle=<NativeSelect value={rascunho??atual?.valor??item.padrao??''} disabled={desabilitado} onChange={e=>mudar(item.key,e.target.value)}>{item.opcoes!.map(([v,r])=><NativeSelectOption key={v} value={v}>{r}</NativeSelectOption>)}</NativeSelect>;
 }else if(item.tipo==='secret'){
  // O campo nunca recebe o valor salvo: só a máscara, como dica.
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
  <div className="page-heading"><div><p className="eyebrow">Esta instalação</p><h1>Configurações</h1><p className="muted">Valem para todos os projetos. Chaves nunca aparecem inteiras.</p></div></div>
  {aviso&&<p className="notice">{aviso}</p>}
  {GRUPOS.map(([grupo,titulo,descricao,alvos])=>{
   const itens=CATALOGO.filter(i=>i.grupo===grupo);
   const motorFora=grupo==='motor'&&!dados.motor?.online;
   const pendente=itens.some(i=>i.key in rascunho);
   return <section className="panel" key={grupo}>
    <div className="section-top"><div><h2>{titulo}</h2><p className="muted">{descricao}</p></div><Button disabled={!pendente||!!salvando} onClick={()=>salvar(grupo)}>{salvando===grupo?'Salvando…':'Salvar'}</Button></div>
    {motorFora&&<p className="notice">O motor não está no ar, ou foi iniciado fora do start.py. As opções só do motor ficam bloqueadas até ele subir pelo start.py.</p>}
    {itens.map(i=><Campo key={i.key} item={i} atual={atual(i)} rascunho={rascunho[i.key]} desabilitado={motorFora&&i.destino==='motor'} mudar={mudar}/>)}
    {alvos.length>0&&<div className="actions">{alvos.map(a=><Button key={a} variant="outline" disabled={!!testes[a]?.carregando} onClick={()=>testar(a)}>{testes[a]?.carregando?'Testando…':'Testar '+ROTULO_ALVO[a]}</Button>)}</div>}
    {alvos.filter(a=>testes[a]&&!testes[a].carregando).map(a=><p key={a} className={testes[a].ok?'muted':'notice'}>{testes[a].ok?'✓':'✗'} {ROTULO_ALVO[a]}: {testes[a].detalhe}</p>)}
   </section>;
  })}
 </>;
}
```

Se `globals.css` não tiver `.field`, acrescente:

```css
.field{display:flex;flex-direction:column;gap:.35rem;margin-block:.75rem}
```

(confira antes com `grep -n "\.field" app/globals.css`).

- [ ] **Step 2: Ligar no `workspace.tsx`** — cada troca abaixo é uma substituição exata de texto:

1. Imports: trocar `import {BASES,type Base} from '@/lib/sources/bases';` por
   ```ts
   import {BASES,isBase,type Base} from '@/lib/sources/bases';
   import {simultaneos} from '@/lib/settings';
   import SettingsPanel from './settings-panel';
   ```
2. Estado: trocar `[base,setBase]=useState<Base>('pubmed'),` por `[base,setBase]=useState<Base>('pubmed'),[config,setConfig]=useState<any>(null),[settingsOpen,setSettingsOpen]=useState(false),`
3. Carga e saída automática: logo após a linha `useEffect(()=>{loadHome().catch(e=>setMessage(e.message)).finally(()=>setLoading(false));},[]);` inserir
   ```ts
    // Base padrão, lote e modo de download vêm de Configurações.
    useEffect(()=>{api('/api/settings').then((d:any)=>{setConfig(d.orbis);const b=d.orbis?.ORBIS_BASE_PADRAO?.valor;if(isBase(b))setBase(b);}).catch(()=>{});},[]);
    useEffect(()=>{if(project)setSettingsOpen(false);},[project?.id]);
   ```
4. Modo de download (2 lugares): trocar `downloadMode=state?.settings?.downloadMode||'baixar'` por `downloadMode=state?.settings?.downloadMode||config?.ORBIS_DOWNLOAD_PADRAO?.valor||'baixar'`, e `const modo=start.state.settings?.downloadMode||'baixar'` por `const modo=start.state.settings?.downloadMode||config?.ORBIS_DOWNLOAD_PADRAO?.valor||'baixar'`.
5. Lote: trocar `await Promise.all([work(),work()])` por `await Promise.all(Array.from({length:simultaneos(config)},work))`.
6. Barra lateral: trocar `onClick={()=>{setProject(null);api('/api/projects').then(setList).catch(e=>setMessage(e.message))}}><FolderOpen/>Meus projetos</SidebarMenuButton></SidebarMenuItem>` por
   ```tsx
   onClick={()=>{setProject(null);setSettingsOpen(false);api('/api/projects').then(setList).catch(e=>setMessage(e.message))}}><FolderOpen/>Meus projetos</SidebarMenuButton></SidebarMenuItem><SidebarMenuItem><SidebarMenuButton disabled={disabled} isActive={settingsOpen} onClick={()=>{setProject(null);setSettingsOpen(true)}}><Settings2/>Configurações</SidebarMenuButton></SidebarMenuItem>
   ```
7. Título: trocar `{project?'Meus projetos / '+project.name:'Meus projetos'}` por `{settingsOpen?'Configurações':project?'Meus projetos / '+project.name:'Meus projetos'}`.
8. Área principal: trocar `{!project&&invitations.length>0&&` por `{!project&&!settingsOpen&&invitations.length>0&&`, e a linha ` {!project?<>` por ` {settingsOpen?<SettingsPanel onChange={setConfig}/>:!project?<>`.

- [ ] **Step 3: Verificar tipos e build**

Run: `pnpm exec tsc --noEmit 2>&1 | grep -E "workspace|settings-panel" || echo tipos-ok; pnpm build >/dev/null && echo build-ok`
Expected: `tipos-ok` e `build-ok`.

- [ ] **Step 4: Verificar na tela** (skill `run` ou manual): subir com `python3 start.py`, abrir `http://localhost:5173`, clicar **Configurações**. Conferir:
  - os quatro blocos aparecem; chaves do ambiente com etiqueta "do ambiente" e mascaradas;
  - trocar "Consultas simultâneas" para 3, **Salvar**, recarregar a página: continua 3, etiqueta "salvo aqui";
  - **Testar LILACS** mostra `✓ LILACS: LILACS respondeu (...)`;
  - salvar um e-mail do Unpaywall mostra o aviso de reiniciar, e `grep UNPAYWALL_EMAIL motor/.env` mostra o valor novo;
  - voltar a "Meus projetos" e abrir um projeto: a busca abre na base padrão escolhida.

---

### Task 8: Documentação e verificação final

**Files:**
- Modify: `COMO_RODAR.md` (seção "Variáveis de ambiente" e "Testes")

- [ ] **Step 1: Documentar** — em `COMO_RODAR.md`, logo abaixo do título `## Variáveis de ambiente`, inserir:

```markdown
> A forma recomendada agora é a tela **Configurações** (barra lateral). Ela
> grava as chaves do ORBIS no banco local e as do motor em `motor/.env`, e
> mostra de onde vem cada valor. As variáveis abaixo continuam valendo como
> padrão: o que for salvo na tela tem prioridade.
>
> O `start.py` gera a cada execução um `ORBIS_ENGINE_TOKEN` e o entrega ao
> ORBIS e ao motor. Sem ele (motor iniciado à mão), a tela mostra as opções do
> motor bloqueadas.
```

Na seção de testes, trocar `# ORBIS — 9 suítes` por `# ORBIS — 11 suítes` e, na lista de "Antes de publicar", acrescentar `&& node tests/settings-integration.mjs` ao fim do comando.

- [ ] **Step 2: Suíte completa**

Run: `pnpm exec tsc --noEmit && pnpm build && for t in tests/*.mjs; do node "$t" >/dev/null 2>&1 && echo "ok $t" || echo "FALHOU $t"; done; (cd servico-python && ../motor/.venv/bin/python -m pytest -q)`
Expected: todas as linhas `ok`, pytest sem falhas.
