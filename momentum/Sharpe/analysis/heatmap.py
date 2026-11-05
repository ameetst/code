import pandas as pd, numpy as np, json, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
o=['HFCL','STLTECH','LAURUSLABS','DIVISLAB','SHILPAMED','SANSERA','SONACOMS','ATHERENERG','SYRMA','WELCORP','DIACABS','CPPLUS','CUPID','SKYGOLD']
df=pd.read_excel('NSEAll_updated.xlsx',sheet_name='DATA',index_col=0); df.columns=pd.to_datetime(df.columns)
px=df.loc[o].T.sort_index().astype(float)
r=np.log(px).diff().dropna()
def plot(r,title,fn):
    c=r.corr().loc[o,o].values; n=len(o)
    m=np.ma.masked_where(np.triu(np.ones((n,n),bool),k=1),c)
    fig,ax=plt.subplots(figsize=(11,8))
    im=ax.imshow(m,cmap='RdBu',vmin=-1,vmax=1)
    for i in range(n):
        for j in range(i+1):
            v=c[i,j]; ax.text(j,i,f'{v:.2f}',ha='center',va='center',fontsize=9,color='white' if abs(v)>0.5 else 'black')
    ax.set_xticks(np.arange(n+1)-.5,minor=True); ax.set_yticks(np.arange(n+1)-.5,minor=True)
    ax.grid(which='minor',color='white',lw=1); ax.tick_params(which='minor',length=0)
    ax.set_xticks(range(n)); ax.set_yticks(range(n)); ax.set_xticklabels(o); ax.set_yticklabels(o)
    for s in ax.spines.values(): s.set_visible(False)
    plt.setp(ax.get_xticklabels(),rotation=-40,ha='left',rotation_mode='anchor')
    cb=fig.colorbar(im,ax=ax,shrink=.8,ticks=np.arange(-1,1.01,.2)); cb.outline.set_visible(False)
    ax.set_title(title,fontsize=12,pad=12)
    plt.tight_layout(); fig.savefig(fn,dpi=150); plt.close(fig)
plot(r,f'Portfolio holdings – daily return correlation ({len(r)} days, to {r.index[-1]:%d %b %Y})','analysis/corr_heatmap_full.png')
plot(r.tail(60),'Portfolio holdings – daily return correlation (last 60 days)','analysis/corr_heatmap_60d.png')
