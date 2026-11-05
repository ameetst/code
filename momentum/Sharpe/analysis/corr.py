import pandas as pd, numpy as np, json
pd.set_option('display.width',250); pd.set_option('display.max_columns',30)
led=json.load(open('NSEAll_positions_ledger.json'))
tk=list(led)
df=pd.read_excel('NSEAll_updated.xlsx',sheet_name='DATA',index_col=0)
df.columns=pd.to_datetime(df.columns)
px=df.loc[tk].T.sort_index().astype(float)
print(px.index[-1], px.shape); print(px.tail(2).T)
print("NaN count:", px.isna().sum().to_dict())
ret=np.log(px).diff().dropna(how='all')
# weights by current value
last=px.ffill().iloc[-1]
val=pd.Series({t:led[t]['qty']*last[t] for t in tk}); w=val/val.sum()
print("\nWeights:\n",(w*100).round(1).sort_values(ascending=False))
print("Invested value", val.sum())
def rep(r,label):
    c=r.corr()
    print(f"\n=== {label}: {len(r)} obs ===")
    print(c.round(2))
    m=c.where(~np.eye(len(c),dtype=bool))
    print("avg pairwise corr:", round(np.nanmean(m.values),3))
    iu=np.triu_indices(len(c),1)
    pairs=pd.Series(c.values[iu],index=[(c.index[i],c.columns[j]) for i,j in zip(*iu)]).sort_values()
    print("Top 8 pairs:\n",pairs.tail(8).iloc[::-1].round(2)); print("Lowest 5:\n",pairs.head(5).round(2))
    print("avg corr per stock:\n",m.mean().sort_values(ascending=False).round(2))
    return c
r=ret.dropna()
c_all=rep(r,"full common window")
rep(r.tail(60),"last 60d")
# weekly returns (less asynchronous noise)
wr=np.log(px.ffill()).resample('W-FRI').last().diff().dropna()
rep(wr,"weekly")
# portfolio diversification
cov=r.cov()*252
wv=w.reindex(cov.index).values
pvol=np.sqrt(wv@cov.values@wv); wavg=(wv*np.sqrt(np.diag(cov))).sum()
print("\nPort vol %.1f%%, weighted avg stock vol %.1f%%, diversification ratio %.2f"%(pvol*100,wavg*100,wavg/pvol))
ev=np.linalg.eigvalsh(r.corr().values)[::-1]
print("Eigenvalue share:",(ev/ev.sum()).round(3)[:5], "eff N (PR):", round(ev.sum()**2/(ev**2).sum(),2))
# risk contributions
mrc=cov.values@wv; rc=wv*mrc/(wv@cov.values@wv)
print("Risk contrib %:\n",pd.Series(rc*100,index=cov.index).round(1).sort_values(ascending=False))
# clustering
from scipy.cluster.hierarchy import linkage,fcluster
from scipy.spatial.distance import squareform
d=np.sqrt(0.5*(1-c_all.values)); np.fill_diagonal(d,0)
Z=linkage(squareform(d,checks=False),'average')
for k in (3,4): 
    lab=fcluster(Z,k,'maxclust'); print(k,{i:list(c_all.index[lab==i]) for i in set(lab)})
# vs benchmark: Nifty? market proxy = equal-weight all stocks
allpx=df.T.sort_index().astype(float)
mk=np.log(allpx).diff().iloc[1:].clip(-0.3,0.3).median(axis=1).loc[r.index]
beta=r.apply(lambda s:np.cov(s,mk)[0,1]/mk.var()); cm=r.corrwith(mk)
print("\nvs market (median stock ret): beta / corr\n",pd.DataFrame({'beta':beta,'corr':cm}).round(2).sort_values('corr',ascending=False))
c_all.to_csv('analysis/corr_matrix.csv')
