import pandas as pd, numpy as np, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon
o=['HFCL','STLTECH','LAURUSLABS','DIVISLAB','SHILPAMED','SANSERA','SONACOMS','ATHERENERG','SYRMA','WELCORP','DIACABS','CPPLUS','CUPID','SKYGOLD']
n=len(o)
df=pd.read_excel('NSEAll_updated.xlsx',sheet_name='DATA',index_col=0); df.columns=pd.to_datetime(df.columns)
r=np.log(df.loc[o].T.sort_index().astype(float)).diff().dropna()
F=r.corr().loc[o,o].values; R=r.tail(60).corr().loc[o,o].values; D=R-F
cm=plt.get_cmap('RdBu'); norm=plt.Normalize(-1,1)
def txtc(v): return 'white' if abs(v)>0.5 else 'black'
def style(ax):
    ax.set_xlim(-.5,n-.5); ax.set_ylim(n-.5,-.5); ax.set_aspect('equal')
    ax.set_xticks(range(n)); ax.set_yticks(range(n)); ax.set_xticklabels(o); ax.set_yticklabels(o)
    ax.xaxis.tick_top(); plt.setp(ax.get_xticklabels(),rotation=40,ha='left',rotation_mode='anchor')
    ax.tick_params(length=0)
    for s in ax.spines.values(): s.set_visible(False)
def cbar(fig,ax,cmap,norm,label,**k):
    sm=plt.cm.ScalarMappable(cmap=cmap,norm=norm); cb=fig.colorbar(sm,ax=ax,shrink=.7,pad=.02,**k); cb.set_label(label); cb.outline.set_visible(False); return cb

# A: lower = full year, upper = 60d, same scale
fig,ax=plt.subplots(figsize=(12,10)); style(ax)
for i in range(n):
    for j in range(n):
        v=F[i,j] if i>=j else R[i,j]
        ax.add_patch(plt.Rectangle((j-.5,i-.5),1,1,fc=cm(norm(v)),ec='white',lw=1))
        ax.text(j,i,f'{v:.2f}' if i!=j else '',ha='center',va='center',fontsize=9,color=txtc(v),fontweight='bold' if i<j else 'normal')
        if i==j: ax.text(j,i,'1.00',ha='center',va='center',fontsize=9,color='white')
cbar(fig,ax,cm,norm,'correlation',ticks=np.arange(-1,1.01,.2))
ax.set_title('A. One matrix, two windows\nlower-left = full year (251d)   |   upper-right (bold) = last 60 days',pad=70,fontsize=12)
fig.tight_layout(); fig.savefig('analysis/corr_merged_A_two_triangles.png',dpi=140); plt.close(fig)

# B: lower = full-year level, upper = change in correlation (60d - full)
dn=plt.Normalize(-.4,.4); dc=plt.get_cmap('PuOr_r')
fig,ax=plt.subplots(figsize=(13,10)); style(ax)
for i in range(n):
    for j in range(n):
        if i>=j:
            v=F[i,j]; ax.add_patch(plt.Rectangle((j-.5,i-.5),1,1,fc=cm(norm(v)),ec='white',lw=1))
            ax.text(j,i,f'{v:.2f}',ha='center',va='center',fontsize=9,color=txtc(v))
        else:
            d=D[i,j]; ax.add_patch(plt.Rectangle((j-.5,i-.5),1,1,fc=dc(dn(d)),ec='white',lw=1))
            ax.text(j,i,f'{d:+.2f}',ha='center',va='center',fontsize=9,color='black')
cbar(fig,ax,cm,norm,'full-year correlation (lower-left)',ticks=np.arange(-1,1.01,.2))
cbar(fig,ax,dc,dn,'change: 60d minus full year (upper-right)  orange = more correlated lately')
ax.set_title('B. Level + trend\nlower-left = full-year correlation   |   upper-right = how much it has changed in the last 60 days',pad=70,fontsize=12)
fig.tight_layout(); fig.savefig('analysis/corr_merged_B_level_and_change.png',dpi=140); plt.close(fig)

# C: every cell split diagonally: upper-left triangle = full year, lower-right = 60d (full square matrix)
fig,ax=plt.subplots(figsize=(15,12.5)); style(ax)
for i in range(n):
    for j in range(n):
        x0,y0=j-.5,i-.5
        if i==j:
            ax.add_patch(plt.Rectangle((x0,y0),1,1,fc='#f2f2f2',ec='white',lw=1)); ax.text(j,i,'1.00',ha='center',va='center',fontsize=8,color='gray'); continue
        ul=Polygon([(x0,y0),(x0+1,y0),(x0,y0+1)],fc=cm(norm(F[i,j])),ec='none')
        lr=Polygon([(x0+1,y0),(x0+1,y0+1),(x0,y0+1)],fc=cm(norm(R[i,j])),ec='none')
        ax.add_patch(ul); ax.add_patch(lr)
        ax.add_patch(plt.Rectangle((x0,y0),1,1,fc='none',ec='white',lw=1.2))
        ax.text(x0+.07,y0+.13,f'{F[i,j]:.2f}',ha='left',va='center',fontsize=7.5,color=txtc(F[i,j]))
        ax.text(x0+.93,y0+.87,f'{R[i,j]:.2f}',ha='right',va='center',fontsize=7.5,color=txtc(R[i,j]),fontweight='bold')
cbar(fig,ax,cm,norm,'correlation',ticks=np.arange(-1,1.01,.2))
ax.set_title('C. Split cells\nupper-left triangle = full year   |   lower-right triangle (bold) = last 60 days',pad=70,fontsize=12)
fig.tight_layout(); fig.savefig('analysis/corr_merged_C_split_cells.png',dpi=140); plt.close(fig)
print('ok', np.round(np.nanmax(np.abs(D-np.diag(np.diag(D)))),2))
