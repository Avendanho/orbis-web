// Bases bibliográficas oferecidas na etapa de busca. Só metadados: a interface
// importa isto sem arrastar o código de rede de cada fonte.
export const BASES={
 pubmed:{label:'PubMed',exemplo:'autism[tiab] AND genetics[tiab] AND 2024[dp]'},
 lilacs:{label:'LILACS',exemplo:'dengue AND tw:(tratamento)'},
 cochrane:{label:'Cochrane (revisões CDSR)',exemplo:'dengue[tiab] AND vaccine*[tiab]'},
 embase:{label:'Embase',exemplo:"'dengue'/exp AND 'vaccine'/exp"},
} as const;
export type Base=keyof typeof BASES;

export function isBase(v:any):v is Base{return typeof v==='string'&&Object.prototype.hasOwnProperty.call(BASES,v);}
