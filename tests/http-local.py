import urllib.request,urllib.error,json
BASE='http://127.0.0.1:4173'
def req(path,method='GET',data=None,user='test-a',raw=False):
 h={'oai-authenticated-user-id':user,'oai-authenticated-user-email':user+'@example.test'} if user else {}
 if data is not None:h['content-type']='application/pdf' if raw else 'application/json'
 r=urllib.request.Request(BASE+path,data=data if raw else json.dumps(data).encode() if data is not None else None,headers=h,method=method)
 try:x=urllib.request.urlopen(r,timeout=45)
 except urllib.error.HTTPError as e:x=e
 b=x.read();return x.status,b if raw else json.loads(b)
assert req('/api/projects',user=None)[0]==401
status,p=req('/api/projects','POST',{'name':'Projeto sintético QA'});assert status==201,(status,p)
id=p['id'];path='/api/projects/'+id
assert req(path,user='test-b')[0]==404
_,p=req(path);assert p['state']['articles']==[]
protocol={'population':'P','concept':'C','context':'C','inclusion':'I','exclusion':'','questions':['A população atende?']}
status,result=req(path,'PATCH',{'revision':0,'action':'protocol','protocol':protocol,'approve':True});assert status==200,(status,result)
assert req(path,'PATCH',{'revision':0,'action':'notes','notes':'conflito'})[0]==409
assert req(path,'PATCH',{'revision':1,'action':'notes','notes':'nota persistida'})[0]==200
_,p=req(path);assert p['state']['notes']=='nota persistida'
# Restore only into a new project. Upload validates relation and bytes.
_,new=req('/api/projects','POST',{'name':'Restauração sintética QA'});q='/api/projects/'+new['id'];s=p['state'];s['articles']=[{'id':'testarticle','title':'Registro sintético','doi':'10.1234/test','filename':'teste.pdf','authors':'Teste','year':'2026','abstract':'Resumo'}]
assert req(q,'PATCH',{'revision':0,'action':'restore','state':s})[0]==200
assert req(q+'/pdf?article=testarticle','POST',b'not-a-pdf',raw=True)[0]==400
status,doc=req(q+'/pdf?article=testarticle','POST',b'%PDF-1.7\nsynthetic',raw=True);assert status==200,(status,doc)
doc=json.loads(doc);assert req(q+'/pdf?document='+doc['id'],user='test-b',raw=True)[0]==404
status,pdf=req(q+'/pdf?document='+doc['id'],raw=True);assert status==200 and pdf.startswith(b'%PDF-')
_,p=req(q);assert p['bytes']==len(pdf) and len(p['documents'])==1
assert req(q,'PATCH',{'revision':1,'action':'triage','article':'testarticle','answers':['Não'],'reason':''})[0]==400
assert req(q,'PATCH',{'revision':1,'action':'triage','article':'testarticle','answers':['Indeterminado'],'reason':''})[0]==200
print('PASS: autenticação, isolamento de projetos/PDFs, concorrência, persistência, restauração, upload validado, download, quota contábil e triagem.')
