import io,json,os,sys,glob,re
sys.stdout.reconfigure(encoding='utf-8')
src=json.load(io.open('retrans/source.json',encoding='utf-8'))
out={}; missing=[]; done=set()
for path in sorted(glob.glob('retrans/ko_*.json')):
    n=str(int(os.path.basename(path)[3:6]))
    done.add(n)
    got=json.load(io.open(path,encoding='utf-8'))
    items=src[n]
    for i,jp in enumerate(items):
        k=str(i)
        if k in got and got[k]: out[jp]=got[k]
        else: missing.append((n,i,jp[:40]))
json.dump(out,io.open('retranslation_ko.json','w',encoding='utf-8'),ensure_ascii=False,indent=0)
total=sum(len(v) for v in src.values())
print('batches done %d/%d | unique strings %d/%d'%(len(done),len(src),len(out),total))
if missing: print('MISSING %d:'%len(missing), missing[:6])
