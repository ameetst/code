import pandas as pd, numpy as np, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
o=['HFCL','STLTECH','LAURUSLABS','DIVISLAB','SHILPAMED','SANSERA','SONACOMS','ATHERENERG','SYRMA','WELCORP','DIACABS','CPPLUS','CUPID','SKYGOLD']
n=len(o)
df=pd.read_excel('NSEAll_updated.xlsx',sheet_name='DATA',index_col=0); df.columns=pd.to_datetime(df.columns)
r=np.log(df.loc[o].T.sort_index().astype(float)).diff().dropna()
F=r.corr().loc[o,o].values; R=r.tail(60).corr().loc[o,o].values; D=R-F
cm=plt.get_cmap('RdBu'); norm=plt.Normalize(-1,1)
dc=plt.get_cmap('PuOr_r'); dn=plt.Normalize(-.4,.4)
HI=0.40
fig,ax=plt.subplots(figsize=(13,10))
ax.set_xlim(-.5,n-.5); ax.set_ylim(n-.5,-.5); ax.set_aspect('equal')
ax.set_xticks(range(n)); ax.set_yticks(range(n)); ax.set_xticklabels(o); ax.set_yticklabels(o)
ax.xaxis.tick_top(); plt.setp(ax.get_xticklabels(),rotation=40,ha='left',rotation_mode='anchor'); ax.tick_params(length=0)
for s in ax.spines.values(): s.set_visible(False)
for i in range(n):
    for j in range(n):
        if i>=j:
            v=F[i,j]; fc=cm(norm(v)); t='1.00' if i==j else f'{v:.2f}'; col='white' if abs(v)>.5 else 'black'; fw='normal'
        else:
            v=R[i,j]; fc=dc(dn(D[i,j])); t=f'{v:.2f}'; col='white' if abs(D[i,j])>.25 else 'black'; fw='bold'
        ax.add_patch(plt.Rectangle((j-.5,i-.5),1,1,fc=fc,ec='white',lw=1))
        ax.text(j,i,t,ha='center',va='center',fontsize=9,color=col,fontweight=fw)
for cmap,nm,lab,kw in [(cm,norm,'full-year correlation (lower-left)',dict(ticks=np.arange(-1,1.01,.2))),
                       (dc,dn,'60d colour = change vs full year (upper-right)\norange = more correlated lately, purple = less',{})]:
    cb=fig.colorbar(plt.cm.ScalarMappable(cmap=cmap,norm=nm),ax=ax,shrink=.7,pad=.03,**kw); cb.set_label(lab); cb.outline.set_visible(False)
ax.set_title('Holdings correlation - full year vs last 60 days\n'
             f'lower-left = full-year ({len(r)}d), colour = correlation   |   upper-right (bold) = 60d correlation, colour = change vs full year',pad=70,fontsize=11)
fig.tight_layout(); fig.savefig('analysis/corr_heatmap_final.png',dpi=140)
