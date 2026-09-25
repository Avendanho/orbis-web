import {readFile} from 'node:fs/promises';import {stripTypeScriptTypes} from 'node:module';import assert from 'node:assert/strict';
const url=s=>'data:text/javascript;base64,'+Buffer.from(s).toString('base64');
const load=async p=>import(url(stripTypeScriptTypes(await readFile(p,'utf8'))));

const pmc=await load('lib/sources/pmc.ts');
const unpaywall=await load('lib/sources/unpaywall.ts');

// ---------------------------------------------------------------------------
// PMC — bucket de acesso aberto na AWS
//
// O ORBIS usava `oa.fcgi`, desligado na migração de agosto/2026 (responde 404).
// O bucket é a via que continua atendendo cliente HTTP. Duas armadilhas na
// leitura da listagem: um artigo pode ter várias versões (.1, .2) e só a mais
// nova vale; e os suplementos também terminam em .pdf.
// ---------------------------------------------------------------------------
const listagem=(...keys)=>'<?xml version="1.0"?><ListBucketResult><IsTruncated>false</IsTruncated>'+
 keys.map(k=>'<Contents><Key>'+k+'</Key></Contents>').join('')+'</ListBucketResult>';

assert.deepEqual(
 pmc.parseBucketListing(listagem('PMC10350077.1/PMC10350077.1.pdf','PMC10350077.1/PMC10350077.1.xml','PMC10350077.1/PMC10350077.1.txt'),'PMC10350077'),
 {version:1,pdf:'https://pmc-oa-opendata.s3.amazonaws.com/PMC10350077.1/PMC10350077.1.pdf',xml:'https://pmc-oa-opendata.s3.amazonaws.com/PMC10350077.1/PMC10350077.1.xml'},
 'artigo simples: pdf e xml canônicos');

assert.equal(
 pmc.parseBucketListing(listagem('PMC1.1/PMC1.1.pdf','PMC1.2/PMC1.2.pdf'),'PMC1').version,2,
 'quando há duas versões, vale a mais nova');

assert.equal(
 pmc.parseBucketListing(listagem('PMC2.1/MGG3-11-e2191-s001.pdf','PMC2.1/41390_2023_MOESM1_ESM.pdf','PMC2.1/PMC2.1.xml'),'PMC2').pdf,null,
 'suplemento em .pdf não é o artigo');

assert.deepEqual(pmc.parseBucketListing(listagem(),'PMC99'),{version:null,pdf:null,xml:null},
 'artigo ausente do bucket devolve vazio, não erro');

assert.equal(pmc.normalizePmcid('10350077'),'PMC10350077','aceita id numérico');
assert.equal(pmc.normalizePmcid('pmc10350077'),'PMC10350077','normaliza caixa');
assert.equal(pmc.normalizePmcid(''),'','id vazio não vira PMC');
assert.ok(pmc.bucketListingUrl('PMC1000').includes('prefix=PMC1000.'),
 'o ponto final ancora o prefixo: PMC1000 não pode listar PMC10000338');

// ---------------------------------------------------------------------------
// PMC — conversor oficial de identificadores do NCBI
// ---------------------------------------------------------------------------
assert.deepEqual(
 pmc.parseIdConv({records:[{doi:'10.1/x',pmid:'123',pmcid:'PMC456'}]}),
 {pmid:'123',pmcid:'PMC456'},'extrai pmid e pmcid');
assert.deepEqual(pmc.parseIdConv({records:[{status:'error',errmsg:'invalid'}]}),{},
 'registro com erro não vira identificador');
assert.deepEqual(pmc.parseIdConv({}),{},'resposta vazia não quebra');
assert.deepEqual(pmc.parseIdConv(null),{},'resposta nula não quebra');

// ---------------------------------------------------------------------------
// Unpaywall — a fonte de maior rendimento medido, ausente do ORBIS
//
// Ela exige um e-mail de contato; sem ele a API recusa. Sem e-mail
// configurado, a fonte tem de se omitir em silêncio, nunca inventar consulta.
// ---------------------------------------------------------------------------
assert.equal(unpaywall.requestUrl('10.1/x',''),null,'sem e-mail, não há consulta');
assert.ok(unpaywall.requestUrl('10.1/x','a@b.c').includes('email=a%40b.c'),'e-mail vai na consulta');
assert.ok(unpaywall.requestUrl('10.1002/aur.1227','a@b.c').includes('10.1002%2Faur.1227'),'DOI é escapado');

const resposta={
 is_oa:true,
 best_oa_location:{url_for_pdf:'https://r.example/melhor.pdf',url_for_landing_page:'https://r.example/item'},
 oa_locations:[
  {url_for_pdf:'https://r.example/melhor.pdf',url_for_landing_page:'https://r.example/item'},
  {url_for_pdf:null,url_for_landing_page:'https://outro.example/item'},
  {url_for_pdf:'https://terceiro.example/f.pdf',url_for_landing_page:null},
 ],
};
const lido=unpaywall.parse(resposta);
assert.equal(lido.isOa,true,'marca acesso aberto');
assert.equal(lido.pdfUrls[0],'https://r.example/melhor.pdf','o melhor PDF vem primeiro');
assert.equal(new Set(lido.pdfUrls).size,lido.pdfUrls.length,'sem URL repetida');
assert.ok(lido.pageUrls.includes('https://outro.example/item'),
 'página sem PDF também é candidata: costuma trazer citation_pdf_url');

// Medido na API real: para muitos artigos `url_for_pdf` vem nulo e só `url`
// traz o endereço. Perder esses casos é perder recuperação barata.
assert.deepEqual(
 unpaywall.parse({is_oa:true,oa_locations:[{url_for_pdf:null,url_for_landing_page:null,url:'https://so-url.example/item'}]}).pageUrls,
 ['https://so-url.example/item'],'usa `url` quando a landing page vem nula');

assert.deepEqual(unpaywall.parse({is_oa:false,oa_locations:[]}),{isOa:false,pdfUrls:[],pageUrls:[]},
 'artigo fechado não devolve candidato');
assert.deepEqual(unpaywall.parse(null),{isOa:false,pdfUrls:[],pageUrls:[]},'resposta nula não quebra');

console.log('sources: ok');
