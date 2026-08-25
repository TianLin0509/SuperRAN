"""Build the self-contained SuperRAN developer documentation site.

The output is a single ``docs/index.html`` with hash-routed logical pages.
Curated algorithm explanations live in this file; volatile inventories
(modules, public APIs, MCP tools, tests, presets and Skill references) are
derived from the current repository on every build so counts cannot drift.

Python 3.10 compatibility matters.  In particular, formula expressions with
backslashes stay in module-level constants instead of f-string expressions.
"""
from __future__ import annotations

import ast
import base64
import cmath
import hashlib
import html
import json
import math
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

try:  # direct script execution (scripts/ is sys.path[0])
    from developer_guide_details import (
        DETAIL_SPECS,
        FORMULA_SPECS,
        render_detail,
        render_formula,
    )
except ModuleNotFoundError:  # importing as scripts.make_developer_guide
    from scripts.developer_guide_details import (
        DETAIL_SPECS,
        FORMULA_SPECS,
        render_detail,
        render_formula,
    )

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "superran"
OUT = ROOT / "docs" / "index.html"
GITHUB = "https://github.com/TianLin0509/superran/blob/main/"
UI_ASSETS = ROOT / "docs" / "assets" / "ui"
sys.path.insert(0, str(ROOT / "src"))

# These files are deliberately carried by the generated API atlas rather than
# by standalone wireless chapters.  Every other top-level module must be named
# by at least one detailed chapter, so a new capability cannot disappear into
# the API reference without an explicit documentation decision.
DETAILED_MODULE_EXEMPTIONS = {"__init__", "katex", "mathml"}

from superran import bler_curves as bc  # noqa: E402
from superran import katex as kx  # noqa: E402
from superran import linkadapt as la  # noqa: E402
from superran import mathml as mm  # noqa: E402


def M(tex: str, *, block: bool = True) -> str:
    """KaTeX-upgradable formula with MathML fallback."""
    return kx.wrap(tex, mm.render(tex, block=block), display=block)


def real_ui_screenshot(name: str, alt: str, caption: str) -> str:
    """Embed a browser-captured product screenshot into the offline guide."""
    path = UI_ASSETS / name
    if not path.is_file():
        raise FileNotFoundError(
            f"缺少真实 UI 截图 {path}；先运行 run_spec_browser_qa.py / "
            "run_kpi_browser_qa.py")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    encoded = base64.b64encode(raw).decode("ascii")
    return (
        '<figure class="product-shot" data-real-ui-screenshot="true" '
        f'data-source="{html.escape(name, quote=True)}" data-sha256="{digest}">'
        f'<img src="data:image/png;base64,{encoded}" alt="{html.escape(alt, quote=True)}" '
        'loading="lazy" decoding="async">'
        f'<figcaption>{html.escape(caption)}</figcaption></figure>'
    )


# Keep LaTeX outside f-string expression parts for Python < 3.12.
F_CHANNEL = M(
    r"H_u(t,f)=\sum_{\ell=1}^{L}\sqrt{P_\ell}\,e^{-j2\pi f\tau_\ell}"
    r"e^{j2\pi\nu_\ell t}\,a_{\mathrm{UE},\ell}\,J_\ell\,"
    r"a_{\mathrm{BS},\ell}^{H}",
)
F_CHANNEL_SHAPE = M(
    r"H_{\mathrm{DL}}\in\mathbb C^{N_{\mathrm{UE,Rx}}\times N_{\mathrm{BS,Tx}}}"
    r"=\mathbb C^{4\times64},\qquad "
    r"H_{\mathrm{UL}}\in\mathbb C^{64\times4}",
)
F_DATASET = M(
    r"h_{\mathrm{true}},h_{\mathrm{est}}\in"
    r"\mathbb C^{N\times T\times RB\times N_{\mathrm{BS}}\times N_{\mathrm{UE}}}",
)
F_PDP_IFFT = M(
    r"g_{t,m,u}[\ell]=\sqrt{N_{\mathrm{RB}}}\,\operatorname{IFFT}_k"
    r"\{w[k]H_{t,k,m,u}\},\qquad "
    r"P[\ell]=\mathbb E_{t,m,u}|g_{t,m,u}[\ell]|^2",
)
F_PDP_AXIS = M(
    r"\Delta f_{\mathrm{obs}}=12\Delta f_{\mathrm{SCS}},\qquad "
    r"\Delta\tau=\frac{1}{N_{\mathrm{RB}}\Delta f_{\mathrm{obs}}},\qquad "
    r"T_{\mathrm{amb}}=\frac{1}{\Delta f_{\mathrm{obs}}}",
)
F_PDP_MOMENT = M(
    r"\delta_\ell=\operatorname{wrap}_{[-T_{\mathrm{amb}}/2,T_{\mathrm{amb}}/2)}"
    r"(\tau_\ell-\bar\tau),\qquad "
    r"\tau_{\mathrm{rms}}=\sqrt{\max\!\left("
    r"\frac{\sum_\ell P[\ell]\delta_\ell^2}{\sum_\ell P[\ell]}-\sigma_w^2,0\right)}",
)
F_PATTERN = M(
    r"A_H(\phi)=\min\!\left(12(\phi/\phi_{3\mathrm{dB}})^2,A_m\right),"
    r"\qquad A_V(\epsilon)=\min\!\left(12(\epsilon/\theta_{3\mathrm{dB}})^2,A_m\right)",
)
F_PATTERN_COMBINE = M(
    r"G_E(\phi,\epsilon)=G_{\max}-\min\!\left(A_H(\phi)+A_V(\epsilon),A_m\right),"
    r"\qquad g_E(\phi,\epsilon)=10^{G_E(\phi,\epsilon)/20}",
)
F_JONES = M(
    r"f_p(\phi,\epsilon)=g_E(\phi,\epsilon)"
    r"\begin{bmatrix}\cos\zeta_p\\\sin\zeta_p\end{bmatrix},"
    r"\qquad \zeta_0=+45^\circ,\quad\zeta_1=-45^\circ",
)
F_SUBARRAY_PATTERN = M(
    r"S_M^{\mathrm{RX}}(\epsilon,f)=\sum_{q=0}^{M-1}w_q^{*}"
    r"e^{-j2\pi(f/f_{\mathrm{ref}})z_q\sin\epsilon},"
    r"\qquad a_{\mathrm{port}}=F^Ha_{\mathrm{AE}}",
)
F_RAY_POLARIZATION = M(
    r"c_{\ell,p_t,p_r}=f_{\mathrm{RX},p_r}^{T}J_\ell f_{\mathrm{TX},p_t},"
    r"\qquad H_\ell\propto\sqrt{P_\ell}\,c_{\ell,p_t,p_r}\,"
    r"a_{\mathrm{RX},\ell}a_{\mathrm{TX},\ell}^{H}"
    r"e^{-j2\pi f\tau_\ell}e^{j2\pi\nu_\ell t}",
)
F_FEED = M(
    r"w_q=\frac{A_qe^{j\psi_q}e^{j2\pi z_q\sin\theta_{\mathrm{tilt}}}}"
    r"{\left\|[A_ke^{j\psi_k}e^{j2\pi z_k\sin\theta_{\mathrm{tilt}}}]_k\right\|_2}",
)
F_COUPLING = M(
    r"F_{e,r}=\begin{cases}w_q,&e=e(h,3v_{\mathrm{RF}}+q,p),\ "
    r"r=r(h,v_{\mathrm{RF}},p)\\0,&\text{otherwise}\end{cases},"
    r"\qquad F\in\mathbb C^{192\times64}",
)
F_COUPLING_256 = M(
    r"r_{256}(p,h,v)=p\cdot128+h\cdot8+v,\qquad "
    r"F_{e,r}=w_q\ \text{for}\ e=e(p,h,6v+q),\ q=0,\ldots,5,\qquad "
    r"F\in\mathbb C^{1536\times256}",
)
F_EFFECTIVE = M(
    r"a_{\mathrm{port}}=F^Ha_{\mathrm{AE}},\qquad "
    r"H_{\mathrm{port}}=H_{\mathrm{AE}}F",
)
F_SRS_RX = M(
    r"Y_{\mathrm{SRS}}[k]=H_{\mathrm{UL}}[k]X_{\mathrm{SRS}}[k]+I[k]+N[k],"
    r"\quad H_{\mathrm{UL}}[k]\in\mathbb C^{64\times4}",
)
F_LS = M(
    r"\widehat H_{\mathrm{LS}}[k]=Y_{\mathrm{SRS}}[k]X_{\mathrm{SRS}}[k]^{\dagger}",
)
F_LMMSE = M(
    r"\widehat h_{t,\mathrm{LMMSE}}=R_{tp}\left(R_{pp}+R_v\right)^{-1}"
    r"\widehat h_{p,\mathrm{LS}}",
)
F_SRS_LAG = M(
    r"\tau_b(t)=t-t_{\mathrm{last\ SRS},b}-D_{\mathrm{proc}},\qquad "
    r"\widehat H_b(s)=H_b\!\left(\max(0,s-\lceil\tau_b/\Delta t_{\mathrm{snap}}\rceil)\right)",
)
F_CSI_SWEEP = M(
    r"T_{\mathrm{sweep}}=H_{\mathrm{hop}}T_{\mathrm{SRS}},\qquad "
    r"\bar\tau_{\mathrm{CSI}}=\frac{H_{\mathrm{hop}}T_{\mathrm{SRS}}}{2}+D_{\mathrm{proc}}",
)
F_CSI_REPORT_HOLD = M(
    r"q(s)=\max\{q_i:t_{q_i}\le t_s\},\qquad "
    r"(\mathrm{PMI},\mathrm{CQI})_s=(\mathrm{PMI},\mathrm{CQI})_{q(s)}",
)
F_TYPE1_COLUMN = M(
    r"a_H(k)=\frac{1}{\sqrt{N_H}}"
    r"\left[e^{-j2\pi nk/(N_HO_H)}\right]_{n=0}^{N_H-1},\qquad "
    r"w(k_H,k_V,p)=\frac{1}{\sqrt2}"
    r"\begin{bmatrix}a_V(k_V)\otimes a_H(k_H)\\"
    r"\phi_p\,a_V(k_V)\otimes a_H(k_H)\end{bmatrix},\quad "
    r"\phi_p\in\{1,j,-1,-j\}",
)
F_PMI_COVARIANCE = M(
    r"R_{\mathrm{tx}}=\frac{1}{TN_{\mathrm{RB}}N_{\mathrm{UE}}}"
    r"\sum_{t,k}H_{t,k}H_{t,k}^{H}",
)
F_PMI_GREEDY = M(
    r"i_\ell^\star=\arg\max_{i\notin\mathcal I_\ell}"
    r"w_i^HR_\ell w_i,\qquad "
    r"R_{\ell+1}=(I-w_{i_\ell^\star}w_{i_\ell^\star}^{H})R_\ell"
    r"(I-w_{i_\ell^\star}w_{i_\ell^\star}^{H})^{H}",
)
F_PMI_REFERENCE = M(
    r"G_{\mathrm{BF}}(s,r)=\gamma(H_{\mathrm{prec},s},W_{\mathrm{tx},s,r})"
    r"-\gamma(H_{\mathrm{prec},s},W_{\mathrm{PMI},q(s),r}),\qquad "
    r"\gamma_{\mathrm{PMI,true}}(s,r)="
    r"\gamma(H_{\mathrm{true},s},W_{\mathrm{PMI},q(s),r})",
)
F_CSI_AGING_SINR = M(
    r"W_s=\operatorname{SVD}(\widehat H_{s-d_s}),\qquad "
    r"\gamma_s=\operatorname{SINR}_{\mathrm{MMSE}}(H_s,W_s,R_{uu},N_0)",
)
F_EBF = M(
    r"Q_{\mathrm{EBF}}=W\sqrt{P/L},\qquad "
    r"\operatorname{tr}(QQ^H)\le P",
)
F_PEBF = M(
    r"Q_{\mathrm{PEBF}}=\alpha Q_{\mathrm{EBF}},\qquad "
    r"\alpha=\min\!\left(1,\sqrt{\frac{P/M}{\max_m\|q_{m,:}\|_2^2}}\right)",
)
F_NEBF = M(
    r"q_{m,:}^{\mathrm{NEBF}}=\sqrt{P/M}\,\frac{q_{m,:}^{\mathrm{EBF}}}"
    r"{\|q_{m,:}^{\mathrm{EBF}}\|_2},\qquad m=1,\ldots,M",
)
F_CSI_ERROR_MODEL = M(
    r"H=\widehat H+E,\qquad "
    r"\mathbb E[EE^H]=N_{\mathrm{BS}}\sigma_e^2 I",
)
F_CSI_ERROR_VARIANCE = M(
    r"\widehat\sigma_e^2=\frac{\|H-\widehat H\|_F^2}{N_{\mathrm{coef}}}",
)
F_ROBUST_RZF = M(
    r"W_{\mathrm{rRZF}}=\widehat H^H"
    r"(\widehat H\widehat H^H+\lambda I)^{-1},\qquad "
    r"\lambda=\frac{N_s\sigma_n^2}{P}+N_{\mathrm{BS}}\sigma_e^2",
)
F_MMSE = M(
    r"G=(H_{\mathrm{eff}}^HH_{\mathrm{eff}}+R_{uu}+N_0I)^{-1}H_{\mathrm{eff}}^H",
)
F_STREAM_SINR = M(
    r"\gamma_\ell=\frac{|g_\ell^Hh_\ell|^2P_\ell}"
    r"{\sum_{j\ne\ell}|g_\ell^Hh_j|^2P_j+g_\ell^H(R_{uu}+N_0I)g_\ell}",
)
F_RB_LINK_BUDGET = M(
    r"P_{\mathrm{tx,RB}}[\mathrm{dBm}]=P_{\mathrm{tx,total}}-10\log_{10}N_{\mathrm{RB}},"
    r"\qquad N_{\mathrm{RB}}[\mathrm{dBm}]=-174+10\log_{10}(12\Delta f)+NF",
)
F_PREBEAM_ANCHOR = M(
    r"S_0=\mathbb E[|H|^2]P,\qquad I+N=\frac{S_0}{10^{\gamma_{\mathrm{geo,dB}}/10}},"
    r"\qquad \gamma_{\mathrm{rank1}}=\gamma_{\mathrm{geo}}"
    r"\frac{\mathbb E[\sigma_1^2]}{\mathbb E[|H|^2]}",
)
F_RANK = M(
    r"r^\star=\arg\max_{r\in\{1,2,3,4\}}\ r\cdot\eta\!\left("
    r"\gamma_{\mathrm{eff}}(r)\right)",
)
F_SVD_DIRECTION = M(
    r"R_{\mathrm{tx},f}=\frac{1}{T}\sum_{t=1}^{T}H^{\mathrm{code}}_{t,f}"
    r"\left(H^{\mathrm{code}}_{t,f}\right)^{H}=V_f\Lambda_fV_f^{H},\qquad "
    r"W_{\mathrm{SVD},f}=V_f[:,1{:}r]",
)
F_PMI_CODEBOOK = M(
    r"\begin{aligned}"
    r"a_h(i)&=\frac{1}{\sqrt{N_1}}\left[e^{-j2\pi ni/(N_1O_1)}\right]_{n=0}^{N_1-1},\quad "
    r"a_v(j)=\frac{1}{\sqrt{N_2}}\left[e^{-j2\pi mj/(N_2O_2)}\right]_{m=0}^{N_2-1}\\[4pt]"
    r"v_{i,j}&=a_v(j)\otimes a_h(i),\quad "
    r"c_{i,j,p}=\frac{1}{\sqrt2}\begin{bmatrix}v_{i,j}\\e^{jp\pi/2}v_{i,j}\end{bmatrix},\quad p\in\{0,1,2,3\}\\[4pt]"
    r"q_\ell&=\arg\max_q c_q^H R_{\mathrm{res},\ell}c_q,\quad "
    r"R_{\mathrm{res},\ell+1}=(I-c_{q_\ell}c_{q_\ell}^H)R_{\mathrm{res},\ell}(I-c_{q_\ell}c_{q_\ell}^H)"
    r"\end{aligned}",
)
F_SPATIAL_POWER = M(
    r"\begin{aligned}"
    r"Q_0&=W_{\mathrm{dir}}\sqrt{P/r},\quad p_m^{(0)}=\sum_{k=1}^{r}|Q_{0,mk}|^2,\quad P_m^{\max}=P/M\\[4pt]"
    r"Q_{\mathrm{EBF}}&=Q_0,\quad Q_{\mathrm{PEBF}}=\alpha Q_0,\quad "
    r"\alpha=\min\!\left(1,\sqrt{\frac{P/M}{\max_m p_m^{(0)}}}\right)\\[4pt]"
    r"Q_{\mathrm{NEBF}}&=DQ_0,\quad D_{mm}=\sqrt{\frac{P/M}{p_m^{(0)}}}"
    r"\end{aligned}",
)
F_BF_STREAM = M(
    r"\gamma_{f,k}^{(x)}=\frac{1}{\left[\left(I_r+"
    r"G_{x,f}^{H}R_{n,f}^{-1}G_{x,f}\right)^{-1}\right]_{kk}}-1,\qquad "
    r"G_{x,f}=\left(H^{\mathrm{code}}_{\mathrm{gNB},f}\right)^H Q_{x,f},\ "
    r"x\in\{\mathrm{SVD}{+}C,\mathrm{PMI}{+}C\}",
)
F_BF_RBG = M(
    r"\bar\gamma_{b,k}^{(x)}=\frac{1}{|\mathcal F_b|}\sum_{f\in\mathcal F_b}"
    r"\gamma_{f,k}^{(x)},\qquad "
    r"\Gamma_{b,k}^{(x)}=10\log_{10}\bar\gamma_{b,k}^{(x)}",
)
F_BF_GAIN = M(
    r"G_{\mathrm{BF},b}=\frac{1}{r}\sum_{k=1}^{r}"
    r"\left(\Gamma_{b,k}^{(\mathrm{TX})}-\Gamma_{b,k}^{(\mathrm{PMI})}\right),"
    r"\qquad G_{\mathrm{BF}}=\frac{1}{B}\sum_{b=1}^{B}G_{\mathrm{BF},b}",
)
F_AMC_PRED = M(
    r"\gamma_{\mathrm{AMC,pred}}=\Gamma(\mathrm{MCS}(\mathrm{CQI}))"
    r"+G_{\mathrm{BF}}\qquad[\mathrm{dB}]",
)
F_RX_BLER = M(
    r"\gamma_{\mathrm{RX}}=\mathcal A_{\mathrm{RBG,stream}}\!\left("
    r"\gamma(H_{\mathrm{true}},Q_{\mathrm{SVD}+C})\right),\qquad "
    r"P_{\mathrm{TB,error}}=\mathcal C_{m_{\mathrm{final}}}(\gamma_{\mathrm{RX}})",
)
F_MU_SINR = M(
    r"\gamma_{\mathrm{tx,MU}}=\Gamma(\mathrm{MCS}(\mathrm{CQI}))+G_{\mathrm{BF}}"
    r"+L_{\mathrm{corr}}+L_{\mathrm{power}}",
)
F_POWER_LOSS = M(r"L_{\mathrm{power}}=-10\log_{10}K_{\mathrm{MU}}\ \mathrm{dB}")
F_TBS = M(r"N_{\mathrm{info}}=N_{\mathrm{RE}}Q_mR\nu,\qquad TBS=Q_{38.214}(N_{\mathrm{info}})")
F_RBG_SEARCH = M(
    r"n_u^\star=\min\{n\in[1,17]:TBS(s,m_u,r_u,n)\ge B_u\}"
    r"=\operatorname{searchsorted}(\mathbf{TBS}_{s,m,r},B_u)+1",
)
F_PF = M(r"M_u(t)=\frac{TBS_u(17,t)}{\max(\bar R_u(t),\epsilon)}")
F_QOS_PF = M(
    r"M_u=w_u\frac{[R_u^{\mathrm{inst}}]^\beta}{[\bar R_u]^\alpha}"
    r"\left(1+\frac{D_u^{\mathrm{HoL}}}{D_u^{\mathrm{budget}}}\right)^\gamma",
)
F_RAVG = M(
    r"\bar R_u(t+1)=(1-a)\bar R_u(t)+aR_u^{\mathrm{credit}}(t),\qquad "
    r"a=1/T_{\mathrm{PF}}",
)
F_OLLA = M(
    r"\Delta(t+1)=\operatorname{clip}\!\left(\Delta(t)+"
    r"\mathbf1_{\mathrm{ACK}}s_{\uparrow}-\mathbf1_{\mathrm{NACK}}s_{\downarrow}\right)",
)
F_FINAL_MCS = M(
    r"\begin{aligned}"
    r"m_{\mathrm{base}}&=\mathcal S(\gamma_{\mathrm{base}},p_{\mathrm{target}}),\\"
    r"m_{\mathrm{tx,SU}}&=\operatorname{clip}\!\left(\left\lfloor m_{\mathrm{base,SU}}+"
    r"\Delta_{\mathrm{SU}}\right\rfloor\right),\\"
    r"m_{\mathrm{tx,MU}}&=\operatorname{clip}\!\left(\left\lfloor m_{\mathrm{base,MU}}+"
    r"\Delta_{\mathrm{SU}}+\Delta_{\mathrm{MU}}\right\rfloor\right)"
    r"\end{aligned}"
)
F_BUSY_RATE = M(
    r"R_{\mathrm{trim}}=\frac{\sum_{i=1}^{K-1}B_i}"
    r"{t_{\mathrm{ACK},K-1}-t_{\mathrm{first\ TX}}},\qquad "
    r"R_{\mathrm{head}}=\frac{\sum_{i=1}^{K-1}B_i}"
    r"{t_{\mathrm{ACK},K-1}-t_{\mathrm{arrival},1}}",
)
F_FIRST_PACKET = M(r"D_{\mathrm{first}}=t_{\mathrm{first\ scheduled}}-t_{\mathrm{arrival}}")
F_PRB_UTIL = M(
    r"U_{\mathrm{PRB}}=\frac{\sum_t n_{\mathrm{RBG,used}}(t)f_{\mathrm{slot}}(t)}"
    r"{17\sum_t f_{\mathrm{slot}}(t)},\qquad "
    r"U_{\mathrm{MU}}=\frac{\mathrm{MU\ PRB\ equivalent}}{\mathrm{used\ PRB\ equivalent}}",
)
F_POWER_COMPOSITION = M(
    r"Q_{c,r}^{\mathrm{phys}}=\sqrt{q_{c,r}}\,Q_{c,r}^{\mathrm{spatial}},\qquad "
    r"\|Q_{c,r}^{\mathrm{phys}}(m,:)\|_2^2"
    r"=q_{c,r}\|Q_{c,r}^{\mathrm{spatial}}(m,:)\|_2^2",
)
F_RB_POWER_CONSTRAINT = M(
    r"0.1\le q_{c,r}\le4.0,\qquad "
    r"\frac{1}{N_{\mathrm{RB}}}\sum_{r=0}^{N_{\mathrm{RB}}-1}q_{c,r}=1",
)
F_RB_AUTOBALANCE = M(
    r"q_{c,\mathrm{bal}}="
    r"\frac{N_{\mathrm{RB}}-\sum_{r\in\Omega_c}q_{c,r}}"
    r"{N_{\mathrm{RB}}-|\Omega_c|}",
)
F_RB_COUPLING = M(
    r"\gamma_{u,r}=\frac{q_{c(u),r}S_{u,c(u),r}}"
    r"{N_{u,r}+\eta_u\sum_{c\ne c(u)}q_{c,r}I_{u,c,r}}",
)
F_IOT = M(
    r"\mathrm{IoT}=10\log_{10}\frac{I+N}{N},\qquad "
    r"I=S10^{-\mathrm{SIR}/10},\quad N=S10^{-\mathrm{SINR}/10}-I",
)
F_STREAM_POWER = M(
    r"p_\ell=\max\!\left(0,\mu-\frac{\sigma_\ell^2}{g_\ell}\right),\qquad "
    r"\sum_{\ell=1}^{L}p_\ell=P",
)
F_CAL_COUPLING = M(
    r"CL=P_{\mathrm{tx}}-P_{\mathrm{rx}}"
    r"=PL-G_{\mathrm{tx}}-G_{\mathrm{rx}}\qquad[\mathrm{dB}]",
)
F_CAL_ANGLE = M(
    r"AS=\sqrt{-2\ln\!\left|"
    r"\frac{\sum_n P_ne^{j\phi_n}}{\sum_n P_n}\right|}",
)
F_CAL_SINGULAR = M(
    r"R_r=H_r^HH_r,\qquad \lambda_{r,1}\ge\lambda_{r,2},\qquad "
    r"\Delta\lambda_r=10\log_{10}\!\frac{\lambda_{r,1}}{\lambda_{r,2}}",
)
F_CRN = M(
    r"d_i=Y_i^{(A)}-Y_i^{(B)}\ \text{with identical }"
    r"(\text{drop},\text{traffic},\text{BLER},\text{scheduler})\text{ streams}",
)
F_PROFILE_SCORE = M(
    r"p^\star=\arg\max_{p\in\mathcal P}"
    r"\sum_{\kappa\in\mathcal K_p}\mathbf 1"
    r"[\kappa\subset\operatorname{lower}(\mathrm{intent})]",
)
F_CONFIG_PRECEDENCE = M(
    r"C_{\mathrm{resolved}}=C_{\mathrm{default}}\triangleright"
    r" C_{\mathrm{preset}}\triangleright C_{\mathrm{task}}"
    r"\triangleright C_{\mathrm{user}},\qquad"
    r" A\triangleright B\ \text{means keys in }B\text{ win}",
)
F_REQUIRED_SLOTS = M(
    r"\mathcal Q=\{s\in\mathcal S_{\mathrm{required}}:s\notin"
    r"\mathcal S_{\mathrm{answered}}\},\qquad"
    r"\mathcal S_{\mathrm{required}}="
    r"\{\mathrm{baseline,metric,effect,csi,scenario,scope}\}",
)
F_PREREG_DIGEST = M(
    r"\begin{aligned}"
    r"\Theta_{\mathrm{pr}}={}&(\mathrm{draft,metric,unit,baseline,CSI,effect},\\"
    r"&\mathrm{direction,secondary,note}),\\"
    r"d_{\mathrm{pr}}={}&\operatorname{SHA256}\!\left("
    r"\operatorname{JSON}_{\mathrm{canonical}}(\Theta_{\mathrm{pr}})\right)"
    r"\end{aligned}",
)
F_RESULT_CONTRACT = M(
    r"d_D^{(A)}=d_D^{(B)},\quad n_A=n_B,\quad"
    r" id_i^{(A)}=id_i^{(B)}\ \forall i,\quad"
    r" (m,u)_A=(m,u)_B",
)
F_PREREG_CLASS = M(
    r"\operatorname{class}(m)=\begin{cases}"
    r"\mathrm{primary},&m=m_{\mathrm{primary}}\\"
    r"\mathrm{secondary},&m\in\mathcal M_{\mathrm{secondary}}\\"
    r"\mathrm{exploratory},&\text{otherwise}"
    r"\end{cases}",
)
F_PROBE_SNR = M(
    r"\mathrm{SNR}_{\mathrm{full,dB}}="
    r"\mathrm{SNR}_{\mathrm{probe,dB}}-10\log_{10}"
    r"\frac{N_{\mathrm{RB,full}}}{N_{\mathrm{RB,probe}}}",
)
F_SINR_COMBINE = M(
    r"\gamma_{\mathrm{SINR}}^{-1}=\gamma_{\mathrm{SNR}}^{-1}"
    r"+\gamma_{\mathrm{SIR}}^{-1},\qquad"
    r"\gamma_{\mathrm{SINR,dB}}=-10\log_{10}\!\left("
    r"10^{-\mathrm{SNR}_{\mathrm{dB}}/10}+"
    r"10^{-\mathrm{SIR}_{\mathrm{dB}}/10}\right)",
)
F_MAX_DOPPLER = M(
    r"f_{D,\max}=\frac{\|\mathbf v\|}{\lambda},\qquad"
    r"\nu_\ell=f_{D,\max}\cos\psi_\ell",
)
F_SEQUENCE_CORR = M(
    r"R_{ab}[k]=\frac{1}{N}\left|\sum_{n=0}^{N-1}"
    r"a_n^{\ast}b_{(n+k)\bmod N}\right|",
)
F_BEAM_SELECT = M(
    r"i^\star=\arg\max_i\|H w_i\|_F^2,\qquad"
    r" w_i\in\mathcal W_{\mathrm{CSI\!\!-\!RS\ DFT}}",
)
F_TDD_FRACTION = M(
    r"\rho_{\mathrm{DL}}=\frac{N_D+f_S N_S}"
    r"{N_D+N_S+N_U},\qquad"
    r" f_S=\frac{N_{\mathrm{DL,sym}}}{N_{\mathrm{sym/slot}}}",
)
F_QAM_MI = M(
    r"\begin{aligned}"
    r"I_M(\gamma)&=\log_2M-\frac1M\sum_{m=1}^{M}\mathbb E_n[L_m(n)],\\"
    r"L_m(n)&=\log_2\sum_{m'=1}^{M}\exp\!\left(-\frac{\Delta_{mm'}(n)}{\sigma^2}\right),\\"
    r"\Delta_{mm'}(n)&=|x_m+n-x_{m'}|^2-|n|^2,\\"
    r"\sigma^2&=1/\gamma"
    r"\end{aligned}",
)
F_MIESM = M(
    r"\gamma_{\mathrm{eff}}=I_M^{-1}\!\left("
    r"\frac1N\sum_{n=1}^{N}I_M(\gamma_n)\right)",
)
F_EESM = M(
    r"\gamma_{\mathrm{eff}}=-\beta\ln\!\left("
    r"\frac1N\sum_{n=1}^{N}e^{-\gamma_n/\beta}\right)",
)
F_TB_BLER = M(
    r"P_{\mathrm{TB}}=1-(1-P_{\mathrm{CB}})^C",
)
F_MCS_PROFILE = M(
    r"R_m=\frac{r_m}{1024},\qquad \eta_m=Q_{m}R_m"
)
F_CODEWORD_SINR = M(
    r"\begin{aligned}"
    r"\gamma_{g,s}^{\mathrm{dB}}&=10\log_{10}\!\left("
    r"\frac{1}{|\mathcal B_g|}\sum_{b\in\mathcal B_g}\gamma_{b,s}\right),\\"
    r"\gamma_{\mathrm{cw}}^{\mathrm{dB}}&="
    r"\frac{1}{N_GN_s}\sum_{g=1}^{N_G}\sum_{s=1}^{N_s}"
    r"\gamma_{g,s}^{\mathrm{dB}}"
    r"\end{aligned}"
)
F_MCS_SELECT = M(
    r"m^\star=\max\left\{m\in\{0,\ldots,27\}:"
    r"f_m\!\left(\gamma_{\mathrm{cw}}^{\mathrm{dB}}\right)"
    r"\le p_{\mathrm{target}}\right\}"
)
F_PRESET_TTI_BLER = M(
    r"\begin{aligned}"
    r"p_t^{\mathrm{NewTx}}&=f_{m_t}(\gamma_t),\\"
    r"\mathrm{ACK}_t&=\mathbf 1\!\left\{U_t>p_t^{\mathrm{NewTx}}\right\}"
    r"\end{aligned}",
)
F_HARQ_CC = M(
    r"p_t^{\mathrm{CC}}=f_{m_t}\!\left(\gamma_t+10\log_{10}2\right)"
)
F_HARQ_IR = M(
    r"\begin{aligned}"
    r"\eta_{\mathrm{eq}}&=\eta_{m_t}/2,\qquad "
    r"m_{\mathrm{eq}}=\max\{j:\eta_j\leq\eta_{\mathrm{eq}}\},\\"
    r"p_t^{\mathrm{IR}}&=f_{m_{\mathrm{eq}}}(\gamma_t),\qquad "
    r"P_{\mathrm{res}}=p_t^{\mathrm{NewTx}}p_t^{\mathrm{IR}}"
    r"\end{aligned}"
)
F_LOG_BLER_INTERP = M(
    r"\log_{10}p(x)=(1-\alpha)\log_{10}p_i+"
    r"\alpha\log_{10}p_{i+1},\qquad"
    r"\alpha=\frac{x-x_i}{x_{i+1}-x_i}",
)
F_CONSERVE = M(
    r"B_{\mathrm{arrived}}=B_{\mathrm{ACK}}+B_{\mathrm{queued}}+"
    r"B_{\mathrm{inflight}}+B_{\mathrm{dropped}}",
)

# A mathematical expression is not self-documenting.  Wrap every curated
# formula in a detailed-edition card with a plain-language interpretation and
# an explicit symbol table.  Exact set equality turns future unannotated
# formulas (or stale explanations for deleted formulas) into a build failure.
_formula_constants = {
    name for name, value in tuple(globals().items())
    if name.startswith("F_") and isinstance(value, str)
}
if _formula_constants != set(FORMULA_SPECS):
    _missing = sorted(_formula_constants - set(FORMULA_SPECS))
    _stale = sorted(set(FORMULA_SPECS) - _formula_constants)
    raise RuntimeError(
        f"developer-guide formula documentation drift: missing={_missing}, stale={_stale}"
    )
for _formula_name in sorted(_formula_constants):
    globals()[_formula_name] = render_formula(
        _formula_name, globals()[_formula_name]
    )
del _formula_constants, _formula_name


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def slug(value: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", value).strip("-").lower()
    return s or "section"


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def source_line(rel: str, needle: str) -> int:
    if not needle:
        return 1
    for number, line in enumerate(read(rel).splitlines(), 1):
        if needle in line:
            return number
    raise ValueError(f"documentation source anchor drift: {rel!r} has no {needle!r}")


def source_ref(rel: str, needle: str, label: str | None = None) -> str:
    line = source_line(rel, needle)
    text = label or f"{rel}:{line}"
    href = GITHUB + rel.replace("\\", "/") + f"#L{line}"
    return f'<a class="src" href="{esc(href)}" target="_blank" rel="noreferrer">{esc(text)}</a>'


def code(text: str, language: str = "python") -> str:
    return (
        '<div class="codebox"><div class="codebar"><span>' + esc(language)
        + '</span><button class="copy" type="button">复制</button></div><pre><code>'
        + esc(text.strip("\n")) + "</code></pre></div>"
    )


def callout(kind: str, title: str, body: str) -> str:
    icons = {
        "note": "i", "good": "✓", "warn": "!", "danger": "×", "decision": "?",
    }
    return (
        f'<aside class="callout {esc(kind)}"><span class="callout-icon">'
        f'{esc(icons.get(kind, "i"))}</span><div><strong>{esc(title)}</strong>{body}</div></aside>'
    )


def steps(items: Iterable[tuple[str, str]]) -> str:
    rows = []
    for index, (title, body) in enumerate(items, 1):
        rows.append(
            f'<li><span class="step-no">{index}</span><div><strong>{esc(title)}</strong>{body}</div></li>'
        )
    return '<ol class="steps">' + "".join(rows) + "</ol>"


def metric_cards(items: Iterable[tuple[str, str, str]]) -> str:
    return '<div class="metrics">' + "".join(
        f'<div class="metric"><span>{esc(label)}</span><b>{esc(value)}</b><small>{esc(note)}</small></div>'
        for label, value, note in items
    ) + "</div>"


def table(headers: list[str], rows: Iterable[Iterable[str]], *, raw: set[int] | None = None) -> str:
    raw = raw or set()
    head = "".join(f"<th>{esc(x)}</th>" for x in headers)
    body = []
    for row in rows:
        cells = []
        for index, value in enumerate(row):
            cells.append(f"<td>{value if index in raw else esc(value)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return '<div class="table-wrap"><table><thead><tr>' + head + '</tr></thead><tbody>' + "".join(body) + "</tbody></table></div>"


@dataclass
class MemberDoc:
    name: str
    kind: str
    line: int
    signature: str
    doc: str


@dataclass
class SymbolDoc:
    module: str
    name: str
    kind: str
    line: int
    signature: str
    doc: str
    members: list[MemberDoc] = field(default_factory=list)


@dataclass
class ModuleDoc:
    name: str
    rel: str
    lines: int
    doc: str
    symbols: list[SymbolDoc]


@dataclass
class Page:
    key: str
    title: str
    group: str
    eyebrow: str
    summary: str
    body: str
    tags: tuple[str, ...] = ()
    detail: str = ""
    detail_extra: str = ""


def first_paragraph(doc: str | None, limit: int = 360) -> str:
    if not doc:
        return "—"
    part = re.split(r"\n\s*\n", doc.strip())[0]
    part = re.sub(r"\s+", " ", part)
    return part if len(part) <= limit else part[: limit - 1] + "…"


def _annotation(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return "…"


def _function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = ast.unparse(node.args)
    ret = _annotation(node.returns)
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({args})" + (f" -> {ret}" if ret else "")


def scan_modules() -> list[ModuleDoc]:
    modules: list[ModuleDoc] = []
    for path in sorted(SRC.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        symbols: list[SymbolDoc] = []
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if node.name.startswith("_"):
                continue
            if isinstance(node, ast.ClassDef):
                bases = ", ".join(_annotation(x) for x in node.bases)
                signature = f"class {node.name}" + (f"({bases})" if bases else "")
                members: list[MemberDoc] = []
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("_"):
                        members.append(MemberDoc(
                            item.name, "method", item.lineno, _function_signature(item),
                            first_paragraph(ast.get_docstring(item)),
                        ))
                    elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) \
                            and not item.target.id.startswith("_"):
                        default = ""
                        if item.value is not None:
                            try:
                                default = " = " + ast.unparse(item.value)
                            except Exception:
                                default = " = …"
                        members.append(MemberDoc(
                            item.target.id, "field", item.lineno,
                            f"{item.target.id}: {_annotation(item.annotation)}{default}", "数据字段",
                        ))
                symbols.append(SymbolDoc(
                    path.stem, node.name, "class", node.lineno, signature,
                    first_paragraph(ast.get_docstring(node)), members,
                ))
            else:
                symbols.append(SymbolDoc(
                    path.stem, node.name, "function", node.lineno,
                    _function_signature(node), first_paragraph(ast.get_docstring(node)),
                ))
        modules.append(ModuleDoc(
            path.stem, str(path.relative_to(ROOT)).replace("\\", "/"),
            text.count("\n") + 1, first_paragraph(ast.get_docstring(tree)), symbols,
        ))
    return modules


def detailed_module_coverage(modules: list[ModuleDoc]) -> tuple[set[str], set[str], set[str]]:
    """Return covered, explicitly exempt, and missing top-level module names."""

    names = {module.name for module in modules}
    documented = {
        Path(path).stem
        for spec in DETAIL_SPECS.values()
        for path in spec.source_paths
        if path.startswith("src/superran/") and path.endswith(".py")
    }
    exempt = names & DETAILED_MODULE_EXEMPTIONS
    covered = names & documented
    missing = names - covered - exempt
    return covered, exempt, missing


def scan_tools(modules: list[ModuleDoc]) -> list[SymbolDoc]:
    server = next(m for m in modules if m.name == "server")
    return [s for s in server.symbols if s.name.startswith("sr_")]


def scan_tests() -> list[dict[str, Any]]:
    out = []
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        check_sites = len(re.findall(r"(?<!def )\bcheck\s*\(", text))
        assert_sites = sum(isinstance(n, ast.Assert) for n in ast.walk(tree))
        sections = re.findall(r"(?:sect|section)\(\s*[\"']([^\"']+)", text)
        out.append({
            "name": path.name,
            "rel": str(path.relative_to(ROOT)).replace("\\", "/"),
            "lines": text.count("\n") + 1,
            "check_sites": check_sites,
            "assert_sites": assert_sites,
            "sections": sections,
        })
    return out


def scan_skills() -> list[dict[str, Any]]:
    base = ROOT / "skills" / "channel-sim"
    paths = [base / "SKILL.md"] + sorted((base / "references").glob("*.md"))
    out = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        headings = [m.group(2).strip() for m in re.finditer(r"^(#{1,3})\s+(.+)$", text, re.M)]
        out.append({
            "name": path.name,
            "rel": str(path.relative_to(ROOT)).replace("\\", "/"),
            "lines": text.count("\n") + 1,
            "headings": headings,
        })
    return out


def scan_presets() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for rel in ("presets/presets.yaml", "presets/system_presets.yaml"):
        with (ROOT / rel).open("r", encoding="utf-8") as handle:
            result[rel] = yaml.safe_load(handle) or {}
    return result


def svg_box(x: int, y: int, w: int, h: int, title: str, sub: str, cls: str = "b") -> str:
    return (
        f'<g class="{esc(cls)}"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12"/>'
        f'<text class="dt" x="{x + 14}" y="{y + 24}">{esc(title)}</text>'
        f'<text class="ds" x="{x + 14}" y="{y + 44}">{esc(sub)}</text></g>'
    )


def arrow(x1: int, y1: int, x2: int, y2: int, label: str = "") -> str:
    midx = (x1 + x2) // 2
    midy = (y1 + y2) // 2
    text = f'<text class="al" x="{midx}" y="{midy - 7}">{esc(label)}</text>' if label else ""
    return f'<path class="arr" marker-end="url(#arrow)" d="M{x1},{y1} L{x2},{y2}"/>' + text


def svg_wrap(
    body: str,
    width: int,
    height: int,
    label: str,
    *,
    css_class: str = "",
) -> str:
    defs = (
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z"/></marker></defs>'
    )
    figure_class = "diagram" + (f" {css_class}" if css_class else "")
    return (
        f'<figure class="{esc(figure_class)}"><svg viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{esc(label)}">{defs}{body}</svg><figcaption>{esc(label)}</figcaption></figure>'
    )


def architecture_svg() -> str:
    boxes = [
        (20, 34, 128, 64, "Agent / CLI", "自然语言目标"),
        (178, 34, 128, 64, "MCP server", "35 个 sr_* 工具"),
        (336, 34, 128, 64, "Plan / Spec", "冻结配置与说明书"),
        (494, 34, 128, 64, "Generate", "ChannelHub / Sionna"),
        (652, 34, 128, 64, "Dataset", "h_true / h_est"),
        (810, 34, 128, 64, "Algorithms", "链路 / 系统仿真"),
        (968, 34, 128, 64, "Gates / KPI", "证据与结论"),
    ]
    body = "".join(svg_box(*b) for b in boxes)
    for a, b in zip(boxes, boxes[1:], strict=False):
        body += arrow(a[0] + a[2], 66, b[0], 66)
    body += svg_box(494, 140, 286, 64, "物理内核边界", "复用算法，不复用 ChannelHub 产品壳", "accent")
    body += arrow(636, 140, 636, 104, "标准化")
    body += svg_box(810, 140, 286, 64, "两条评估模式", "capacity / experience，不是精度档位", "good")
    body += arrow(952, 140, 952, 104, "显式 profile")
    return svg_wrap(body, 1120, 230, "SuperRAN 从 Agent 请求到可信结论的完整数据流")


def topology_svg() -> str:
    centers = [(320, 150), (500, 150), (590, 300), (500, 450), (320, 450), (230, 300), (410, 300)]
    body = '<g class="site-lines">'
    for i, (x, y) in enumerate(centers):
        body += f'<circle cx="{x}" cy="{y}" r="42" class="site"/><text class="site-t" x="{x}" y="{y + 5}">站{i}</text>'
        for angle in (-90, 30, 150):
            import math
            x2 = x + 66 * math.cos(math.radians(angle))
            y2 = y + 66 * math.sin(math.radians(angle))
            body += f'<line class="sector" x1="{x}" y1="{y}" x2="{x2:.1f}" y2="{y2:.1f}"/>'
    body += '</g>'
    body += svg_box(720, 80, 330, 88, "同站三个扇区", "共享 site-level LSP / cluster birth-death", "good")
    body += svg_box(720, 214, 330, 88, "不同站", "独立传播状态；不能复制同一 realization", "danger")
    body += svg_box(720, 348, 330, 88, "仍可相关", "同一 UE 几何、场景统计、遮挡规则可相关", "accent")
    body += arrow(590, 190, 720, 124, "同站共享")
    body += arrow(590, 300, 720, 258, "跨站独立")
    return svg_wrap(body, 1080, 520, "7 站 21 小区传播状态拓扑：共享的是同站环境状态，不是复制信道矩阵")


def array_svg() -> str:
    body = ""
    x0, y0 = 70, 50
    for h in range(8):
        for v in range(12):
            for p, color in enumerate(("polp", "polm")):
                x = x0 + h * 56 + p * 16
                y = y0 + v * 31
                body += f'<circle class="ae {color}" cx="{x}" cy="{y}" r="5"/>'
    for h in range(8):
        for vrf in range(4):
            y = y0 + (3 * vrf + 1) * 31
            x = x0 + h * 56 + 8
            body += f'<rect class="feed" x="{x - 18}" y="{y - 40}" width="52" height="80" rx="8"/>'
    body += '<text class="dt" x="55" y="445">8H × 12V × 2pol = 192 physical AEs</text>'
    body += '<circle class="ae polp" cx="570" cy="86" r="7"/><text class="ds" x="586" y="91">+45°</text>'
    body += '<circle class="ae polm" cx="570" cy="118" r="7"/><text class="ds" x="586" y="123">−45°</text>'
    body += svg_box(550, 160, 390, 74, "一个 RF 端口", "固定驱动同列相邻 3 个 AE（1 驱 3）", "accent")
    body += svg_box(550, 258, 390, 74, "64 个 RF 端口", "8H × 4V × 2pol；端口间垂直 2.01λ", "good")
    body += svg_box(550, 356, 390, 74, "F: 192 × 64", "每列仅 3 个非零复馈电权，列范数 = 1", "b")
    body += arrow(510, 205, 550, 197)
    body += arrow(510, 285, 550, 295)
    body += arrow(510, 365, 550, 393)
    return svg_wrap(body, 980, 480, "公司 AAU 阵列拓扑：双极化、1 驱 3 与 192×64 耦合矩阵一一对应")


def array_256_svg() -> str:
    """Product-drawing port order plus the vertical 1-to-6 physical feed."""
    body = ""
    x0, y0 = 42, 50
    for h in range(16):
        for v in range(8):
            x = x0 + h * 34
            y = y0 + v * 35
            port = h * 8 + v + 1
            body += f'<circle class="ae polp" cx="{x}" cy="{y}" r="5"/>'
            if h in (0, 1, 15) or v in (0, 7):
                body += f'<text class="tiny" x="{x + 8}" y="{y + 3}">{port}</text>'
    body += '<text class="dt" x="42" y="360">极化块 1：端口 1…128；行从上到下，列从左到右</text>'
    body += '<text class="ds" x="42" y="385">r = p·128 + h·8 + v + 1（图中 1-based）</text>'
    body += svg_box(660, 48, 380, 76, "第二极化块", "同一 16H×8V 位置；端口 129…256", "accent")
    body += svg_box(660, 154, 380, 76, "每个 T 后的物理阵子", "垂直 1 驱 6；AE 间距 0.67λ", "good")
    body += svg_box(660, 260, 380, 76, "F: 1536 × 256", "每列 6 个非零；FᴴF=I₂₅₆", "b")
    body += arrow(585, 112, 660, 86, "pol block")
    body += arrow(585, 205, 660, 192, "1→6")
    body += arrow(585, 288, 660, 298, "coupling")
    return svg_wrap(body, 1080, 420, "公司 256T 图纸顺序：16H×8V×2pol 端口与 1 驱 6 的 1536×256 耦合")


def port_contract_svg() -> str:
    """Show that layout migration is a physical permutation, not a reshape."""
    body = svg_box(24, 22, 250, 64, "物理位置不变", "同一 (h, physical-v, p)", "good")
    body += svg_box(365, 22, 310, 64, "canonical · 新 64T/256T", "pol_h_v + top_to_bottom", "accent")
    body += svg_box(766, 22, 310, 64, "legacy · 仅旧 64T", "h_v_pol + bottom_to_top", "warn")
    y_rows = (132, 186, 240, 294)
    canonical = (1, 2, 3, 4)
    legacy = (7, 5, 3, 1)
    for row, y in enumerate(y_rows):
        body += f'<circle class="physical-dot" cx="146" cy="{y}" r="9"/>'
        body += f'<text class="tiny" x="146" y="{y + 28}">top+{row}</text>'
        body += f'<rect class="index-cell canonical" x="452" y="{y - 18}" width="136" height="36" rx="8"/>'
        body += f'<text class="index-text" x="520" y="{y + 5}">port {canonical[row]}</text>'
        body += f'<rect class="index-cell legacy" x="853" y="{y - 18}" width="136" height="36" rx="8"/>'
        body += f'<text class="index-text" x="921" y="{y + 5}">port {legacy[row]}</text>'
        body += arrow(158, y, 452, y, "same AE")
        body += arrow(588, y, 853, y, "P")
    body += '<text class="ds" x="146" y="350">示例：64T、第一水平列、第一极化；编号为 1-based</text>'
    body += '<text class="ds" x="720" y="386">H_new = P H_old，W_new = P W_old ⇒ W_newᴴH_new = W_oldᴴH_old</text>'
    return svg_wrap(
        body,
        1100,
        420,
        "64T 新旧端口合同的物理置换：必须同步重排 H、W、F，不能只改 shape 或元数据",
    )


def element_pattern_svg() -> str:
    """Formula-generated pattern cuts; explicitly illustrative, not measured data."""

    def point(cx: float, cy: float, radius: float, angle_deg: float) -> tuple[float, float]:
        angle = math.radians(angle_deg)
        return cx + radius * math.cos(angle), cy - radius * math.sin(angle)

    def radial_path(
        cx: float,
        cy: float,
        values: list[tuple[float, float]],
        *,
        close_to_center: bool = False,
    ) -> str:
        coords = []
        for angle_deg, gain_db in values:
            radius = 18.0 + 102.0 * max(0.0, min(1.0, (gain_db + 30.0) / 30.0))
            coords.append(point(cx, cy, radius, angle_deg))
        start = f"M{cx:.1f},{cy:.1f} " if close_to_center else "M"
        return start + " ".join(
            f"{'L' if i or close_to_center else ''}{x:.1f},{y:.1f}"
            for i, (x, y) in enumerate(coords)
        ) + (" Z" if close_to_center else "")

    def horizontal_gain(phi_deg: float) -> float:
        return -min(12.0 * (phi_deg / 110.0) ** 2, 30.0)

    z = (-0.67, 0.0, 0.67)
    tilt = math.radians(6.0)
    weights = tuple(cmath.exp(1j * 2.0 * math.pi * zi * math.sin(tilt)) / math.sqrt(3.0) for zi in z)

    vertical_raw: list[tuple[float, float, float]] = []
    for elevation_deg in range(-90, 91):
        elevation = math.radians(elevation_deg)
        element_db = -min(12.0 * (elevation_deg / 65.0) ** 2, 30.0)
        response = sum(
            w.conjugate() * cmath.exp(-1j * 2.0 * math.pi * zi * math.sin(elevation))
            for w, zi in zip(weights, z, strict=True)
        )
        combined_db = element_db + 10.0 * math.log10(max(abs(response) ** 2, 1e-12))
        vertical_raw.append((float(elevation_deg), element_db, combined_db))
    peak_db = max(value[2] for value in vertical_raw)
    vertical_element = [(angle, element_db) for angle, element_db, _ in vertical_raw]
    vertical_port = [(angle, max(-30.0, combined_db - peak_db)) for angle, _, combined_db in vertical_raw]

    body = '<rect class="plot-panel" x="18" y="20" width="340" height="350" rx="14"/>'
    body += '<rect class="plot-panel" x="382" y="20" width="340" height="350" rx="14"/>'
    body += '<rect class="plot-panel" x="746" y="20" width="340" height="350" rx="14"/>'
    for cx in (188, 552):
        for radius, label in ((120, "0 dB"), (86, "−10"), (52, "−20"), (18, "−30")):
            body += f'<circle class="pattern-grid" cx="{cx}" cy="202" r="{radius}"/>'
            body += f'<text class="pattern-tick" x="{cx + radius - 4}" y="198">{label}</text>'
        body += f'<line class="pattern-axis" x1="{cx - 132}" y1="202" x2="{cx + 132}" y2="202"/>'
        body += f'<line class="pattern-axis" x1="{cx}" y1="70" x2="{cx}" y2="334"/>'

    horizontal = [(float(phi), horizontal_gain(float(phi))) for phi in range(-180, 181, 2)]
    body += f'<path class="pattern-lobe horizontal" d="{radial_path(188, 202, horizontal)}"/>'
    for angle, label in ((55.0, "+55°"), (-55.0, "−55°")):
        x, y = point(188, 202, 114, angle)
        body += f'<line class="hpbw" x1="188" y1="202" x2="{x:.1f}" y2="{y:.1f}"/>'
        body += f'<text class="pattern-note" x="{x:.1f}" y="{y - 5:.1f}">{label}</text>'
    body += '<text class="dt" x="188" y="48" text-anchor="middle">水平元素切面 · 110° HPBW</text>'
    body += '<text class="ds" x="188" y="354">±55° 处为 −3 dB；后向受 30 dB floor 截断</text>'

    body += f'<path class="pattern-lobe element" d="{radial_path(552, 202, vertical_element, close_to_center=True)}"/>'
    body += f'<path class="pattern-lobe port" d="{radial_path(552, 202, vertical_port, close_to_center=True)}"/>'
    down_x, down_y = point(552, 202, 130, -6.0)
    body += f'<line class="tilt-ray" x1="552" y1="202" x2="{down_x:.1f}" y2="{down_y:.1f}"/>'
    body += f'<text class="pattern-note" x="{down_x - 28:.1f}" y="{down_y + 18:.1f}">约 −6°</text>'
    body += '<text class="dt" x="552" y="48" text-anchor="middle">垂直切面 · 元素 × 1驱3</text>'
    body += '<line class="legend element" x1="430" y1="349" x2="465" y2="349"/><text class="pattern-note" x="472" y="353">65° 元素</text>'
    body += '<line class="legend port" x1="565" y1="349" x2="600" y2="349"/><text class="pattern-note" x="607" y="353">有效端口</text>'

    body += svg_box(776, 58, 280, 60, "① dBi → 场幅", "gE = 10^(GE/20)，不是 /10", "accent")
    body += svg_box(776, 140, 280, 60, "② ±45° Jones", "标量包络 × 极化方向向量", "b")
    body += svg_box(776, 222, 280, 60, "③ 固定子阵因子", "wq 相干叠加；产生下倾/栅瓣", "good")
    body += svg_box(776, 304, 280, 60, "④ 射线与数字 BF", "进入 Jℓ、H；W 在端口域另算", "warn")
    body += arrow(916, 118, 916, 140)
    body += arrow(916, 200, 916, 222)
    body += arrow(916, 282, 916, 304)
    return svg_wrap(
        body,
        1105,
        395,
        "阵元方向图示意：曲线由当前参数公式生成并归一化，不是公司实测方向图",
    )


def ray_construction_svg() -> str:
    items = (
        (20, "元素方向图", "gE(φ,ε)"),
        (202, "极化基", "f±45"),
        (384, "路径耦合", "2×2 Jℓ / XPR"),
        (566, "子阵/steering", "FᴴaAE"),
        (748, "时延与 Doppler", "τℓ / νℓ"),
        (930, "MIMO H(t,f)", "逐 ray/cluster 求和"),
    )
    body = ""
    for i, (x, title, sub) in enumerate(items):
        body += svg_box(x, 52, 158, 72, title, sub, "accent" if i in (0, 5) else "b")
        if i:
            body += arrow(items[i - 1][0] + 158, 88, x, 88)
    body += '<text class="ds" x="550" y="174">方向图改的是每条 ray 的复场系数；随后才由数字预编码 W 决定多端口合成与用户间干扰</text>'
    return svg_wrap(body, 1110, 205, "从阵元方向图到每个 RB 的 MIMO 信道系数")


def srs_matrix_svg() -> str:
    body = svg_box(25, 38, 185, 80, "UE 4Tx", "4 路正交 SRS 端口", "accent")
    body += svg_box(280, 38, 210, 80, "空口", "ZC/短序列 + comb + hopping", "b")
    body += svg_box(560, 38, 210, 80, "gNB 64Rx", "每端口观测 4 个 UE 端口", "good")
    body += svg_box(840, 38, 230, 80, "ĤSRS[64,4]", "LS → 频时插值 / LMMSE", "accent")
    body += arrow(210, 78, 280, 78, "X[4]")
    body += arrow(490, 78, 560, 78, "Y[64]")
    body += arrow(770, 78, 840, 78, "估计")
    body += '<text class="ds" x="550" y="142">目标物理链路；当前 serving pilot 仍固定 N_ap=1，观测按 BS×UE 系数广播</text>'
    colors = ["#2563eb", "#0f766e", "#7c3aed", "#c2410c", "#64748b"]
    order = [0, 8, 16, 7, 15, 6, 14, 5, 13, 4, 12, 3, 11, 2, 10, 1, 9]
    for i, value in enumerate(order):
        x = 30 + i * 61
        body += f'<rect x="{x}" y="190" width="52" height="48" rx="8" fill="{colors[i % len(colors)]}" opacity=".9"/>'
        body += f'<text class="slot" x="{x + 26}" y="220">{value}</text>'
        body += f'<text class="tiny" x="{x + 26}" y="256">{i * 10} ms</text>'
    body += '<text class="dt" x="30" y="166">17-hop order · C_SRS=63 / B_SRS=1 · 16 RB per hop</text>'
    body += '<path class="brace" d="M30,278 L30,292 L1058,292 L1058,278"/>'
    body += '<text class="ds" x="544" y="318">10 ms SRS 周期时，扫完整带约 170 ms；再加处理时延</text>'
    return svg_wrap(body, 1100, 350, "目标 64×4 SRS 物理链路与当前 17 RBG 跳频时间线")


def pdp_pipeline_svg() -> str:
    body = svg_box(24, 42, 184, 76, "频域 H[k]", "RB 中心频点 · 复数", "accent")
    body += svg_box(256, 42, 184, 76, "能量归一 Hann", "压低周期绕回旁瓣", "b")
    body += svg_box(488, 42, 184, 76, "√N · IFFT", "得到周期时延响应 g[ℓ]", "b")
    body += svg_box(720, 42, 184, 76, "逐 realization 复能", "恢复原频域总功率", "good")
    body += svg_box(952, 42, 184, 76, "PDP 与矩", "圆周解绕 · 核去嵌", "accent")
    for x1, x2 in ((208, 256), (440, 488), (672, 720), (904, 952)):
        body += arrow(x1, 80, x2, 80)
    body += svg_box(92, 176, 278, 72, "分辨率 Δτ", "272 RB / 30 kHz → 10.21 ns", "good")
    body += svg_box(414, 176, 278, 72, "无模糊周期 Tamb", "1/(12·30 kHz) → 2.778 μs", "warn")
    body += svg_box(736, 176, 310, 72, "可靠矩边界", "剖面支持宜落在半窗 1.389 μs 内", "danger")
    body += arrow(580, 118, 231, 176, "N 与 SCS")
    body += arrow(580, 118, 553, 176, "RB-center spacing")
    body += arrow(580, 118, 891, 176, "periodic axis")
    return svg_wrap(body, 1160, 286, "PDP 不是直接对 H 取 IFFT：窗、功率恢复、周期矩与可分辨边界缺一不可")


def csi_lifecycle_svg() -> str:
    body = svg_box(22, 36, 182, 72, "SRS 发送", "周期 TSRS；可逐 RBG 跳频", "accent")
    body += svg_box(244, 36, 182, 72, "估计与处理", "LS/LMMSE + Dproc", "b")
    body += svg_box(466, 36, 182, 72, "报告到达", "PMI/CQI report instant", "good")
    body += svg_box(688, 36, 182, 72, "保持旧报告", "直到下一次可用报告", "warn")
    body += svg_box(910, 36, 216, 72, "当前 PDSCH", "Hstale 设计 · Htrue 评价", "accent")
    for x1, x2 in ((204, 244), (426, 466), (648, 688), (870, 910)):
        body += arrow(x1, 72, x2, 72)
    body += '<line class="cap" x1="70" y1="176" x2="1080" y2="176"/>'
    for x, label, sub in (
        (110, "0 ms", "SRS"), (330, "10 ms", "下一跳"),
        (550, "20 ms", "report"), (770, "30 ms", "hold"), (990, "40 ms", "report"),
    ):
        body += f'<circle class="physical-dot" cx="{x}" cy="176" r="8"/>'
        body += f'<text class="tiny" x="{x}" y="202">{esc(label)}</text>'
        body += f'<text class="ds" x="{x}" y="226">{esc(sub)}</text>'
    body += '<text class="ds" x="575" y="266">snapshot 只负责离散 H(t)；它不是 SRS 周期，也不是 PMI/CQI 报告周期</text>'
    return svg_wrap(body, 1150, 300, "CSI 生命周期：采样、估计、报告、保持与实际发送是五个不同时间点")


def pmi_pipeline_svg() -> str:
    body = svg_box(18, 34, 182, 72, "可见 CSI", "Hprec · 估计/陈旧", "accent")
    body += svg_box(238, 34, 182, 72, "宽带协方差", "Rtx=E[HHᴴ]", "b")
    body += svg_box(458, 34, 202, 72, "Type-I-style 列", "DFT×双极化×端口置换", "b")
    body += svg_box(698, 34, 182, 72, "逐层贪心", "索引 iℓ + WPMI", "good")
    body += svg_box(918, 34, 210, 72, "报告状态", "periodic update + hold", "warn")
    for x1, x2 in ((200, 238), (420, 458), (660, 698), (880, 918)):
        body += arrow(x1, 70, x2, 70)
    body += svg_box(120, 174, 288, 76, "参照链", "Hprec 上 Wtx − WPMI → BF Gain", "accent")
    body += svg_box(470, 174, 288, 76, "反馈链", "Htrue 上 WPMI → PMI-SINR → CQI", "good")
    body += svg_box(820, 174, 288, 76, "发送链", "CQI → SINR+BF → MCS → OLLA", "b")
    body += arrow(1023, 106, 614, 174, "q(s)")
    body += arrow(408, 212, 470, 212, "同一 PMI")
    body += arrow(758, 212, 820, 212, "因果输入")
    body += '<text class="ds" x="575" y="292">离线 Dataset.pmi() 给候选码字；系统可用 PMI 还必须经过报告周期。PMIResult.rank 不是标准 RI 决策。</text>'
    return svg_wrap(body, 1150, 330, "PMI 从可见 CSI 到 Type-I-style 码字、报告状态、CQI 与 BF Gain 的完整调用链")


def robust_weight_svg() -> str:
    body = svg_box(28, 38, 230, 72, "轴 A · 发射功率几何", "EBF / PEBF / NEBF", "accent")
    body += svg_box(28, 158, 230, 72, "约束对象", "Q 的总功率或每天线功率", "b")
    body += arrow(143, 110, 143, 158, "normalize Q")
    body += svg_box(345, 38, 230, 72, "轴 B · CSI 不确定性", "ZF / RZF / robust RZF", "accent")
    body += svg_box(345, 158, 230, 72, "约束对象", "Ĥ Gram 逆的对角加载", "b")
    body += arrow(460, 110, 460, 158, "load Gram")
    body += svg_box(662, 38, 230, 72, "先设计方向", "Ĥᴴ(ĤĤᴴ+λI)⁻¹", "good")
    body += svg_box(662, 158, 230, 72, "再施加功率约束", "Q → EBF / PEBF / NEBF", "good")
    body += arrow(575, 74, 662, 74)
    body += arrow(575, 194, 662, 194)
    body += svg_box(979, 82, 150, 104, "真实评价", "Htrue + LMMSE\n检测残留干扰", "warn")
    body += arrow(892, 74, 979, 116, "W")
    body += arrow(892, 194, 979, 154, "Q")
    return svg_wrap(body, 1150, 270, "鲁棒 RZF 与每天线功率约束是两个独立设计轴，必须按顺序组合并分别诊断")


def calibration_stack_svg() -> str:
    body = svg_box(24, 38, 220, 78, "数据与路径真值", "H、功率、角度、时延、几何", "accent")
    body += svg_box(294, 38, 220, 78, "38.901 §7.8 口径", "CL / geometry / DS-AS / singular", "b")
    body += svg_box(564, 38, 220, 78, "参考分布", "R1 文稿或独立引擎", "warn")
    body += svg_box(834, 38, 286, 78, "校准报告", "分位点、适用性、KS 与差值", "good")
    body += arrow(244, 77, 294, 77)
    body += arrow(514, 77, 564, 77)
    body += arrow(784, 77, 834, 77)
    body += svg_box(158, 178, 250, 70, "calibration", "按标准口径出数；不自造阈值", "accent")
    body += svg_box(450, 178, 250, 70, "validate / Gate 1", "项目不变量与物理合理性", "b")
    body += svg_box(742, 178, 250, 70, "Gate 2 / Gate 3", "算法比较与可发布结论", "good")
    body += arrow(408, 213, 450, 213)
    body += arrow(700, 213, 742, 213)
    return svg_wrap(body, 1145, 286, "校准、验证与统计门的职责边界：先按标准出数，再用独立证据决定能否下结论")


def power_constraints_svg() -> str:
    vals = {
        "EBF": [0.18, 0.07, 0.12, 0.03, 0.22, 0.11, 0.16, 0.11],
        "PEBF": [0.10, 0.04, 0.07, 0.02, 0.125, 0.06, 0.09, 0.06],
        "NEBF": [0.125] * 8,
    }
    body = '<line class="cap" x1="70" y1="106" x2="1000" y2="106"/><text class="tiny left" x="1005" y="110">P/M</text>'
    for group, (name, arr) in enumerate(vals.items()):
        gx = 75 + group * 330
        body += f'<text class="dt" x="{gx + 120}" y="36">{name}</text>'
        for i, value in enumerate(arr):
            h = value / 0.24 * 150
            x = gx + i * 34
            y = 260 - h
            cls = "bar bad" if value > 0.1250001 else "bar"
            body += f'<rect class="{cls}" x="{x}" y="{y:.1f}" width="23" height="{h:.1f}" rx="3"/>'
        note = {"EBF": "总功率满；个别天线可超 P/M", "PEBF": "整体缩放；正交性保留但功率未用满", "NEBF": "逐天线拉满；可能破坏 MU 零陷"}[name]
        body += f'<text class="ds" x="{gx + 120}" y="292">{esc(note)}</text>'
    return svg_wrap(body, 1080, 325, "EBF、PEBF、NEBF 的每天线功率分布 toy example（8 天线示意）")


def power_dof_svg() -> str:
    body = svg_box(20, 30, 230, 72, "① 空间功率约束", "EBF / PEBF / NEBF", "accent")
    body += svg_box(300, 30, 230, 72, "② 流间功率", "equal / waterfilling", "b")
    body += svg_box(580, 30, 230, 72, "③ 频域功率", "q[cell,RB] · 0.1…4x", "good")
    body += svg_box(860, 30, 230, 72, "④ 邻区活动", "η · PRB utilization", "warn")
    body += svg_box(165, 166, 310, 80, "最终物理发射矩阵", "Qphys=√q · Qspatial", "accent")
    body += svg_box(635, 166, 310, 80, "逐小区 S/N/I 耦合", "qserv·S / (N+ηΣqkIk)", "good")
    body += arrow(135, 102, 270, 166, "天线轴")
    body += arrow(415, 102, 365, 166, "流轴")
    body += arrow(695, 102, 460, 166, "RB 轴")
    body += arrow(975, 102, 790, 166, "活动概率")
    body += arrow(475, 206, 635, 206, "同一 q")
    body += '<text class="ds" x="555" y="292">方向、每天线限制、流功率、RB profile 与负载是不同自由度；报告必须逐项写明，不能都叫“功控”。</text>'
    return svg_wrap(body, 1120, 330, "SuperRAN 的四类功率自由度及其在逐 RB 信号/干扰预算中的汇合")


def agent_loop_svg() -> str:
    body = svg_box(20, 34, 180, 72, "自然语言意图", "用户目标，不是配置字典", "accent")
    body += svg_box(238, 34, 190, 72, "TaskProfile", "关键词路由 + 有限知识表", "b")
    body += svg_box(466, 34, 190, 72, "两轮对齐", "设计槽位 + 高影响参数", "b")
    body += svg_box(694, 34, 190, 72, "Draft / Plan", "差分修改 + history", "good")
    body += svg_box(922, 34, 190, 72, "Resolved config", "右侧覆盖，唯一执行真值", "accent")
    for x1, x2 in ((200, 238), (428, 466), (656, 694), (884, 922)):
        body += arrow(x1, 70, x2, 70)
    body += svg_box(146, 178, 250, 76, "算法目录", "当前 choice / alternatives / caveat", "b")
    body += svg_box(446, 178, 250, 76, "说明书 HTML", "拓扑、公式、算法与可编辑项", "good")
    body += svg_box(746, 178, 250, 76, "本地回传桥", "loopback + token + whitelist", "warn")
    body += arrow(790, 106, 271, 178, "resolved")
    body += arrow(396, 216, 446, 216)
    body += arrow(696, 216, 746, 216, "POST")
    body += arrow(871, 178, 807, 106, "sanitized delta")
    body += '<text class="ds" x="560" y="304">回传只产生新的显式修改；页面展示、Agent 解释和真正执行都必须指向同一份 resolved config。</text>'
    return svg_wrap(body, 1140, 340, "从意图识别、分轮决策到说明书回传的 Agent 仿真闭环")


def external_contract_svg() -> str:
    body = svg_box(22, 34, 190, 72, "预注册", "metric / baseline / CSI / digest", "accent")
    body += svg_box(250, 34, 190, 72, "生成数据集", "绑定 prereg + dataset digest", "b")
    body += svg_box(478, 34, 190, 72, "用户进程", "h_est 设计 · h_true 评价", "good")
    body += svg_box(706, 34, 190, 72, "ResultArtifact", "values.npz + IDs + code hash", "b")
    body += svg_box(934, 34, 190, 72, "Gate 2 / 3", "pairable + prereg identity", "accent")
    for x1, x2 in ((212, 250), (440, 478), (668, 706), (896, 934)):
        body += arrow(x1, 70, x2, 70)
    body += svg_box(138, 174, 270, 74, "MCP 不执行用户代码", "只发模板/句柄；代码在用户进程", "warn")
    body += svg_box(462, 174, 270, 74, "四项硬相等", "dataset / n / ordered IDs / metric+unit", "danger")
    body += svg_box(786, 174, 270, 74, "结论身份", "primary / secondary / exploratory", "good")
    body += arrow(573, 106, 273, 174, "安全边界")
    body += arrow(801, 106, 597, 174, "pair check")
    body += arrow(1029, 106, 921, 174, "classify")
    return svg_wrap(body, 1145, 292, "外部算法从预注册到可发布结论的证据链")


def raytracing_probe_svg() -> str:
    body = svg_box(24, 30, 230, 72, "场景入口", "builtin / 城市 OSM / preset", "accent")
    body += svg_box(318, 30, 230, 72, "资产准备", "复制缓存 · 清理 PLY obj_info", "b")
    body += svg_box(612, 30, 230, 72, "Sionna RT", "材料/几何 → Paths → CFR", "good")
    body += svg_box(906, 30, 210, 72, "正式数据集", "generation_mode + 路径元数据", "accent")
    body += arrow(254, 66, 318, 66)
    body += arrow(548, 66, 612, 66)
    body += arrow(842, 66, 906, 66)
    body += svg_box(86, 176, 250, 76, "InternalSim probe", "24 RB · 4 symbol · SSB off", "b")
    body += svg_box(420, 176, 250, 76, "可还原几何量", "SIR/PL/位置；SNR 修正", "good")
    body += svg_box(754, 176, 300, 76, "不可拿来算", "谱效/吞吐/PDP/NMSE/宽带预编码", "danger")
    body += arrow(336, 214, 420, 214)
    body += arrow(670, 214, 754, 214)
    body += '<text class="ds" x="570" y="304">RT 没有便宜的等价 probe：光线数与场景几何主导耗时，只能用小样本正式生成；InternalSim probe 也只回答几何工作点。</text>'
    return svg_wrap(body, 1140, 340, "Sionna RT 场景资产链与 InternalSim 快速探测的能力边界")


def reference_signal_svg() -> str:
    body = svg_box(24, 32, 190, 72, "TDD pattern", "D / S / U + symbol split", "accent")
    body += svg_box(252, 32, 190, 72, "SSB", "PSS + SSS + PBCH-DMRS", "b")
    body += svg_box(480, 32, 190, 72, "CSI-RS / DMRS", "Gold sequence + resource map", "b")
    body += svg_box(708, 32, 190, 72, "SRS", "低 PAPR序列 + comb/hopping", "good")
    body += svg_box(936, 32, 190, 72, "估计与测量", "LS/LMMSE · RSRP · CQI", "accent")
    for x1, x2 in ((214, 252), (442, 480), (670, 708), (898, 936)):
        body += arrow(x1, 68, x2, 68)
    body += svg_box(120, 174, 250, 76, "CSI-RS DFT 扫描", "beam×port · 最大接收功率", "good")
    body += svg_box(445, 174, 250, 76, "PMI Type-I-style", "port×column · 宽带/多层选择", "warn")
    body += svg_box(770, 174, 250, 76, "干扰投影", "邻区按自己的服务权进入子空间", "b")
    body += arrow(575, 104, 245, 174, "beam reference")
    body += arrow(575, 104, 570, 174, "feedback reference")
    body += arrow(575, 104, 895, 174, "interference")
    return svg_wrap(body, 1150, 292, "NR 帧结构、参考序列、波束扫描与信道估计基线的关系")


def bler_pipeline_svg() -> str:
    body = svg_box(20, 34, 180, 72, "逐 RE/RB SINR", "频选、逐流真值", "accent")
    body += svg_box(238, 34, 190, 72, "有效 SINR", "当前 dB 平均 / 可选 MIESM", "b")
    body += svg_box(466, 34, 190, 72, "CQI / MCS / TBS", "38.214 表与量化", "good")
    body += svg_box(694, 34, 190, 72, "BLER backend", "分析模型 / 预置曲线", "b")
    body += svg_box(922, 34, 190, 72, "ACK / NACK", "独立随机流 + OLLA", "accent")
    for x1, x2 in ((200, 238), (428, 466), (656, 694), (884, 922)):
        body += arrow(x1, 70, x2, 70)
    body += svg_box(108, 176, 270, 76, "分析 BLER", "QAM MI + finite length + CB→TB", "warn")
    body += svg_box(435, 176, 270, 76, "preset_20b_256qam", "单码字 TTI/TB · MCS+SINR", "good")
    body += svg_box(762, 176, 270, 76, "一次 HARQ", "IR 半谱效（默认）/ CC +3 dB", "warn")
    body += arrow(789, 106, 243, 176, "table 1/2")
    body += arrow(789, 106, 570, 176, "table 3")
    body += arrow(1017, 106, 897, 176, "HARQ semantics")
    return svg_wrap(body, 1140, 294, "从频率选择性 SINR 到 BLER、ACK/NACK 与 HARQ 近似的链路抽象")


def _svg_polyline(points: list[tuple[float, float]]) -> str:
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in points)


def bler_threshold_svg() -> str:
    """All-MCS 10% BLER crossings for NewTx and ReTx."""

    width, height = 1140, 390
    left, right, top, bottom = 76.0, 1108.0, 52.0, 318.0
    y_min, y_max = -5.0, 27.0

    def sx(index: int) -> float:
        return left + index / 27.0 * (right - left)

    def sy(value: float) -> float:
        return bottom - (value - y_min) / (y_max - y_min) * (bottom - top)

    body = '<rect class="plot-panel" x="20" y="20" width="1100" height="344" rx="14"/>'
    modulation_spans = ((0, 4, "QPSK"), (5, 10, "16QAM"),
                        (11, 19, "64QAM"), (20, 27, "256QAM"))
    for span_index, (start, end, label) in enumerate(modulation_spans):
        x0 = left if start == 0 else 0.5 * (sx(start - 1) + sx(start))
        x1 = right if end == 27 else 0.5 * (sx(end) + sx(end + 1))
        body += (
            f'<rect class="chart-band band-{span_index % 2}" x="{x0:.2f}" y="{top:.2f}" '
            f'width="{x1 - x0:.2f}" height="{bottom - top:.2f}"/>'
            f'<text class="chart-band-label" x="{(x0 + x1) / 2:.2f}" y="42">{label}</text>'
        )
    for tick in range(-5, 30, 5):
        y = sy(float(tick))
        body += (
            f'<line class="chart-grid" x1="{left}" y1="{y:.2f}" x2="{right}" y2="{y:.2f}"/>'
            f'<text class="chart-tick y" x="{left - 10}" y="{y + 4:.2f}">{tick}</text>'
        )
    for index in range(28):
        x = sx(index)
        body += f'<text class="chart-tick x" x="{x:.2f}" y="339">{index}</text>'
    body += (
        f'<line class="chart-axis" x1="{left}" y1="{top}" x2="{left}" y2="{bottom}"/>'
        f'<line class="chart-axis" x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}"/>'
        '<text class="chart-axis-label" x="592" y="359">MCS index</text>'
        '<text class="chart-axis-label" transform="translate(20 220) rotate(-90)">10% BLER 门限 / dB</text>'
    )
    new_values = [bc.get_curve(index, "newtx").required_sinr_db(0.1)
                  for index in range(28)]
    retx_values = [bc.get_curve(index, "retx").required_sinr_db(0.1)
                   for index in range(28)]
    for css_class, values in (("chart-newtx", new_values), ("chart-retx", retx_values)):
        points = [(sx(index), sy(value)) for index, value in enumerate(values)]
        body += f'<polyline class="{css_class}" points="{_svg_polyline(points)}"/>'
        body += "".join(
            f'<circle class="{css_class} point" cx="{x:.2f}" cy="{y:.2f}" r="2.7"/>'
            for x, y in points
        )
    body += (
        '<line class="chart-newtx legend-line" x1="820" y1="378" x2="856" y2="378"/>'
        '<text class="chart-legend" x="864" y="382">NewTx</text>'
        '<line class="chart-retx legend-line" x1="946" y1="378" x2="982" y2="378"/>'
        '<text class="chart-legend" x="990" y="382">ReTx</text>'
    )
    return svg_wrap(
        body, width, height,
        "预置曲线 28 档 MCS 的 NewTx/ReTx 10% BLER 门限；门限差是曲线横向间距，不等同于标准 HARQ 合并增益",
        css_class="bler-threshold-chart",
    )


def bler_curve_atlas_svg() -> str:
    """Render all 56 source curves as four modulation-group small multiples."""

    width, height = 1140, 650
    groups = ((2, "QPSK", 0, 4), (4, "16QAM", 5, 10),
              (6, "64QAM", 11, 19), (8, "256QAM", 20, 27))
    panels = ((20, 26), (580, 26), (20, 330), (580, 330))
    body = (
        '<line class="chart-newtx legend-line" x1="390" y1="638" x2="426" y2="638"/>'
        '<text class="chart-legend" x="434" y="642">NewTx 实线</text>'
        '<line class="chart-retx legend-line" x1="588" y1="638" x2="624" y2="638"/>'
        '<text class="chart-legend" x="632" y="642">ReTx 虚线</text>'
    )
    palette = (190, 18, 36, 52, 74, 105, 138, 225, 265, 315)
    for panel_index, ((_q_m, label, first, last), (panel_x, panel_y)) in enumerate(
            zip(groups, panels, strict=True)):
        panel_w, panel_h = 540.0, 292.0
        plot_left, plot_right = panel_x + 58.0, panel_x + 516.0
        plot_top, plot_bottom = panel_y + 38.0, panel_y + 210.0
        curves = [bc.get_curve(index, mode)
                  for index in range(first, last + 1)
                  for mode in ("newtx", "retx")]
        x_min = min(float(curve.start_db) for curve in curves) - 0.25
        x_max = max(float(curve.end_db) for curve in curves) + 0.25

        def sx(
            value: float,
            left: float = plot_left,
            right: float = plot_right,
            min_value: float = x_min,
            max_value: float = x_max,
        ) -> float:
            return left + (value - min_value) / (max_value - min_value) * (right - left)

        def sy(
            probability: float,
            top_value: float = plot_top,
            bottom_value: float = plot_bottom,
        ) -> float:
            exponent = min(max(-math.log10(max(probability, 1e-4)), 0.0), 4.0)
            return top_value + exponent / 4.0 * (bottom_value - top_value)

        body += (
            f'<rect class="plot-panel" x="{panel_x}" y="{panel_y}" '
            f'width="{panel_w}" height="{panel_h}" rx="14"/>'
            f'<text class="chart-panel-title" x="{panel_x + 18}" y="{panel_y + 25}">'
            f'{label} · MCS {first}–{last}</text>'
        )
        for exponent in range(5):
            y = sy(10.0 ** (-exponent))
            tick = "1" if exponent == 0 else f"10⁻{exponent}"
            body += (
                f'<line class="chart-grid" x1="{plot_left}" y1="{y:.2f}" '
                f'x2="{plot_right}" y2="{y:.2f}"/>'
                f'<text class="chart-tick y" x="{plot_left - 8}" y="{y + 4:.2f}">{tick}</text>'
            )
        for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
            value = x_min + fraction * (x_max - x_min)
            x = sx(value)
            body += (
                f'<line class="chart-grid vertical" x1="{x:.2f}" y1="{plot_top}" '
                f'x2="{x:.2f}" y2="{plot_bottom}"/>'
                f'<text class="chart-tick x" x="{x:.2f}" y="{plot_bottom + 18}">{value:.1f}</text>'
            )
        body += (
            f'<line class="chart-axis" x1="{plot_left}" y1="{plot_top}" '
            f'x2="{plot_left}" y2="{plot_bottom}"/>'
            f'<line class="chart-axis" x1="{plot_left}" y1="{plot_bottom}" '
            f'x2="{plot_right}" y2="{plot_bottom}"/>'
            f'<text class="chart-axis-label" x="{(plot_left + plot_right) / 2:.2f}" '
            f'y="{plot_bottom + 35}">post-MMSE SINR / dB</text>'
        )
        for offset, mcs in enumerate(range(first, last + 1)):
            hue = palette[offset % len(palette)]
            colour = f"hsl({hue} 70% 45%)"
            for mode in ("newtx", "retx"):
                curve = bc.get_curve(mcs, mode)
                points = [
                    (sx(float(x)), sy(float(y)))
                    for x, y in zip(curve.sinr_db, curve.bler_points, strict=True)
                ]
                dash = ' stroke-dasharray="5 3"' if mode == "retx" else ""
                opacity = "0.78" if mode == "retx" else "0.96"
                body += (
                    f'<polyline class="chart-curve" style="stroke:{colour}" '
                    f'opacity="{opacity}"{dash} points="{_svg_polyline(points)}"/>'
                )
            legend_x = panel_x + 22 + offset * 48
            body += (
                f'<line class="chart-curve legend-line" style="stroke:{colour}" '
                f'x1="{legend_x}" y1="{panel_y + 273}" '
                f'x2="{legend_x + 16}" y2="{panel_y + 273}"/>'
                f'<text class="chart-legend compact" x="{legend_x + 20}" '
                f'y="{panel_y + 277}">{mcs}</text>'
            )
        body += (
            f'<text class="chart-axis-label" transform="translate({panel_x + 15} '
            f'{(plot_top + plot_bottom) / 2:.2f}) rotate(-90)">BLER</text>'
        )
        if panel_index == 0:
            body += (
                f'<text class="chart-legend" x="{panel_x + 365}" y="{panel_y + 25}">'
                '颜色=MCS</text>'
            )
    return svg_wrap(
        body, width, height,
        "preset_20b_256qam 全部 56 条原始 NewTx/ReTx BLER 瀑布曲线，按调制阶数分面",
        css_class="bler-curve-atlas",
    )


def bler_curve_summary_table() -> str:
    rows = []
    for mcs in range(28):
        new = bc.get_curve(mcs, "newtx")
        retx = bc.get_curve(mcs, "retx")
        new_thr = new.required_sinr_db(0.1)
        retx_thr = retx.required_sinr_db(0.1)
        rows.append((
            str(mcs), new.modulation, str(new.q_m),
            f"{new.code_rate:.3f} / {retx.code_rate:.3f}",
            f"{new_thr:.3f} / {retx_thr:.3f}",
            f"{new_thr - retx_thr:.3f}",
            f"{new.start_db:.2f}…{new.end_db:.2f} / "
            f"{retx.start_db:.2f}…{retx.end_db:.2f}",
            f"{len(new.bler_points)} / {len(retx.bler_points)}",
        ))
    return table(
        ["MCS", "调制", "Qm", "R New/Re", "10%门限 New/Re dB",
         "横向间距 dB", "源 SINR 范围 New/Re", "点数 New/Re"],
        rows,
    ).replace("<table>", '<table data-bler-curve-summary="true">', 1)


def bler_mcs_profile_table() -> str:
    """Render the exact 28-entry preset MCS profile plus IR lookup mapping."""
    rows = []
    for mcs in la.MCS_TABLE_3:
        new = bc.get_curve(mcs.index, "newtx")
        ir_lookup = la.mcs_for_spectral_efficiency(mcs.se / 2.0, table=3)
        r1024 = f"{float(mcs.r_1024):.3f}".rstrip("0").rstrip(".")
        rows.append((
            str(mcs.index), new.modulation, str(mcs.q_m),
            f"{mcs.rate:.3f}", r1024, f"{mcs.se:.4f}",
            f"{new.required_sinr_db(0.1):.3f}",
            f"{mcs.se / 2.0:.4f}", str(ir_lookup.index),
        ))
    return table(
        ["MCS", "调制", "Qm", "R", "1024R", "η=QmR bit/RE",
         "NewTx 10%门限 dB", "IR 半谱效", "IR lookup MCS"],
        rows,
    )


def bler_reproduction_payload() -> str:
    """Machine-copyable profile whose raw-row digest equals DATA_SHA256."""
    payload = {
        "schema": "superran.preset_bler_profile.v1",
        "source_id": bc.data.SOURCE_ID,
        "data_sha256": bc.data.DATA_SHA256,
        "hash_recipe": (
            "sha256(json.dumps(raw_mcs_curve_rows,separators=(',',':')).encode())"
        ),
        "axis": {
            "name": bc.data.SOURCE_AXIS_NAME,
            "original_label": bc.data.SOURCE_AXIS_ORIGINAL_LABEL,
            "interpretation": bc.data.SOURCE_AXIS_USAGE,
            "unit": "dB",
        },
        "lookup_inputs": list(bc.data.PRESET_LOOKUP_INPUTS),
        "error_event": bc.data.ERROR_EVENT,
        "system_curve_use": (
            "NewTx rows only; raw ReTx rows are retained for source audit"
        ),
        "mcs_profile": [
            {
                "mcs": int(m.index), "q_m": int(m.q_m),
                "code_rate": float(m.rate),
                "spectral_efficiency_bit_per_re": float(m.se),
            }
            for m in la.MCS_TABLE_3
        ],
        # JSON serializes tuples as arrays. Re-hashing this parsed field with the
        # recipe above reproduces DATA_SHA256 exactly.
        "raw_mcs_curve_rows": bc.data.MCS_CURVE_ROWS,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


BLER_REFERENCE_IMPLEMENTATION = r'''
"""Independent NumPy reference for preset_20b_256qam.

Save the manual's JSON block as preset_20b_256qam.json, then run this file.
It intentionally does not import SuperRAN.
"""
from __future__ import annotations

import hashlib
import json
import math

import numpy as np

with open("preset_20b_256qam.json", encoding="utf-8") as f:
    PROFILE = json.load(f)

ROWS = PROFILE["raw_mcs_curve_rows"]
digest = hashlib.sha256(
    json.dumps(ROWS, separators=(",", ":")).encode()
).hexdigest()
assert digest == PROFILE["data_sha256"]
assert len(ROWS) == 28


def curve(mcs: int, mode: str = "newtx"):
    """Return (q_m, rate, x_dB, bler) for one raw source curve."""
    if (isinstance(mcs, (bool, np.bool_))
            or not isinstance(mcs, (int, np.integer)) or not 0 <= int(mcs) < 28):
        raise ValueError("mcs must be integer 0..27")
    mcs = int(mcs)
    if mode not in ("newtx", "retx"):
        raise ValueError("mode must be newtx/retx")
    row = ROWS[mcs]
    assert row[0] == mcs
    q_m = int(row[1])
    rate, start_db, step_db, points = row[2 if mode == "newtx" else 3]
    y = np.asarray(points, dtype=float)
    x = float(start_db) + float(step_db) * np.arange(y.size)
    assert step_db > 0 and y.size >= 2
    assert np.all((0 < y) & (y <= 1)) and np.all(np.diff(y) <= 0)
    return q_m, float(rate), x, y


def lookup_bler(mcs: int, codeword_sinr_db: float) -> float:
    """System lookup: NewTx curve, log10 interpolation, conservative clamp."""
    s = float(codeword_sinr_db)
    if math.isnan(s) or s == -math.inf:
        return 1.0
    _, _, x, y = curve(mcs, "newtx")
    log_p = np.interp(
        s, x, np.log10(y), left=0.0, right=float(np.log10(y[-1]))
    )
    return float(np.clip(10.0 ** log_p, 0.0, 1.0))


def required_sinr_db(mcs: int, target_bler: float = 0.1) -> float:
    """Invert one NewTx curve in log10(BLER) between the bracketing points."""
    if not 0 < target_bler <= 1:
        raise ValueError("target_bler must be in (0,1]")
    _, _, x, y = curve(mcs, "newtx")
    if target_bler > y[0] or target_bler < y[-1]:
        raise ValueError("target is outside measured BLER range")
    for i in range(y.size - 1):
        if y[i] >= target_bler >= y[i + 1]:
            if y[i] == y[i + 1]:
                return float(x[i])
            frac = ((math.log10(target_bler) - math.log10(y[i])) /
                    (math.log10(y[i + 1]) - math.log10(y[i])))
            return float(x[i] + frac * (x[i + 1] - x[i]))
    return float(x[-1])


def codeword_sinr_db(sinr_lin_rb_stream, rb_per_rbg: int = 16) -> float:
    """[RB,stream] linear SINR -> one codeword SINR in dB."""
    s = np.asarray(sinr_lin_rb_stream, dtype=float)
    if s.ndim == 1:
        s = s[:, None]
    if s.ndim != 2 or min(s.shape) < 1 or rb_per_rbg < 1:
        raise ValueError("need non-empty [RB,stream] and positive rb_per_rbg")
    rbg_stream_db = []
    for start in range(0, s.shape[0], rb_per_rbg):
        # RBs inside an RBG: linear mean per stream; streams stay separate here.
        rbg_lin = s[start:start + rb_per_rbg].mean(axis=0)
        rbg_stream_db.extend(10.0 * np.log10(np.maximum(rbg_lin, 1e-12)))
    # Equal dB weight across granted RBGs and selected-rank streams.
    return float(np.mean(rbg_stream_db))


def spectral_efficiency(mcs: int) -> float:
    q_m, rate, _, _ = curve(mcs, "newtx")
    return q_m * rate


def select_mcs(codeword_sinr_db: float, target_bler: float = 0.1) -> int:
    """Highest MCS whose NewTx BLER is at or below target; fallback is MCS0."""
    if not 0 < target_bler < 1:
        raise ValueError("target_bler must be in (0,1)")
    best = 0
    for mcs in range(28):
        if lookup_bler(mcs, codeword_sinr_db) <= target_bler:
            best = mcs
    return best


def retransmission_bler(transmitted_mcs: int, codeword_sinr_db: float,
                        combining: str = "ir"):
    """Return lookup-only MCS/SINR/BLER; transmitted MCS never changes."""
    if (isinstance(transmitted_mcs, (bool, np.bool_))
            or not isinstance(transmitted_mcs, (int, np.integer))
            or not 0 <= int(transmitted_mcs) < 28):
        raise ValueError("transmitted_mcs must be integer 0..27")
    mcs = int(transmitted_mcs)
    if combining == "cc":
        lookup_mcs = mcs
        lookup_sinr_db = float(codeword_sinr_db) + 10.0 * math.log10(2.0)
    elif combining == "ir":
        target_se = spectral_efficiency(mcs) / 2.0
        eligible = [j for j in range(28)
                    if spectral_efficiency(j) <= target_se + 1e-12]
        lookup_mcs = max(eligible) if eligible else 0
        lookup_sinr_db = float(codeword_sinr_db)
    else:
        raise ValueError("combining must be ir/cc")
    return {
        "transmitted_mcs": mcs,
        "lookup_mcs": lookup_mcs,
        "lookup_sinr_db": lookup_sinr_db,
        "bler": lookup_bler(lookup_mcs, lookup_sinr_db),
    }


def draw_ack(bler: float, rng: np.random.Generator) -> bool:
    return bool(rng.random() > bler)


# Frozen anchors: if one fails, the copied data or implementation drifted.
assert abs(lookup_bler(15, 14.00) - 0.1320) < 1e-12
assert abs(lookup_bler(15, 14.05) - 0.0949) < 1e-12
assert abs(required_sinr_db(15, 0.1) - 14.042068) < 1e-6
assert retransmission_bler(20, 16.0, "ir")["lookup_mcs"] == 10
assert abs(retransmission_bler(20, 16.0, "cc")["lookup_sinr_db"]
           - 19.0102999566) < 1e-9
print("PASS: preset_20b_256qam reproduced")
'''


def bler_detail_atlas() -> str:
    verify = bc.verify_curves()
    m15 = bc.get_curve(15, "newtx")
    cc20 = la.harq_retransmission_bler(20, 16.0, combining="cc")
    ir20 = la.harq_retransmission_bler(20, 16.0, combining="ir")
    raw_payload = bler_reproduction_payload()
    return (
        '<section class="detail-data-atlas" data-bler-atlas '
        'data-bler-reimplementation="complete" data-bler-sha256="'
        + esc(str(verify["data_sha256"])) + '">'
        '<h2>复现资产总览：先确认拿到的是同一套数据</h2>'
        '<p>这一节的表、图、JSON 和代码都在构建手册时从当前 Python 常量生成；没有手抄第二份。'
        '复现者首先核对 profile、横轴、错误事件和 SHA，再实现插值与状态机。只对着一张截图拟合'
        'S 曲线，无法复现曲线平台、边界钳位和 MCS 间非均匀间距。</p>'
        + metric_cards((
            ("MCS 档位", str(verify["n_mcs"]), "0..27"),
            ("原始曲线", str(verify["n_curves"]), "28 NewTx + 28 ReTx audit"),
            ("原始点", f'{int(verify["n_points"]):,}', "每条 21..50 点"),
            ("SINR 网格", "0.05 dB", "逐曲线等间隔"),
        ))
        + table(
            ["合同项", "必须复现的值", "实现含义"],
            [
                ("source_id", str(verify["source_id"]), "不能标成 3GPP BLER 曲线"),
                ("lookup 输入", "codeword_effective_sinr_db + MCS", "不含 TBS/RE/rank/场景轴"),
                ("错误事件", "一个用户 grant/TTI 的单码字 TB", "每个 TB 只抽一次 ACK/NACK"),
                ("系统曲线", "仅 NewTx 28 条", "ReTx 原始行只做资产审计"),
                ("数据 SHA-256", str(verify["data_sha256"]), "按 raw rows 的紧凑 JSON 计算"),
            ],
        )
        + '<h2>完整预置 MCS Table 3：28 档逐行可复算</h2>'
        '<p><code>R</code> 是 NewTx 曲线随附码率，<code>1024R</code> 只是展示换算；'
        '<code>η=QmR</code> 是单层名义 bit/RE，不含 rank、TBS 量化或导频开销。MCS0 与 MCS1 '
        '虽然 Qm/R/η 相同，仍是两条不同 BLER 曲线，不能按谱效去重。最后两列把默认 IR 的'
        '“半谱效→等效 lookup MCS”也预先摊开。<strong>窄屏请在表内横向滑动查看全部 9 列。</strong></p>'
        + bler_mcs_profile_table()
        + '<h2>曲线数据结构：从一行常量重建横轴</h2>'
        '<p>每个 raw row 的精确结构如下。第 3、4 项分别是 NewTx 与原始 ReTx 审计分支；'
        '每个分支只保存起点、步长和 BLER 序列，横轴必须用 '
        '<code>x[i]=start_db+i×step_db</code> 重建。</p>'
        + code(
            "row = [\n"
            "  mcs, q_m,\n"
            "  [newtx_code_rate, newtx_start_db, newtx_step_db, newtx_bler_points],\n"
            "  [retx_code_rate,  retx_start_db,  retx_step_db,  retx_bler_points],\n"
            "]",
            "python",
        )
        + table(
            ["曲线操作", "精确规则", "禁止的替代写法"],
            [
                ("区间内查询", "在 log10(BLER) 域按 SINR 线性插值", "不能直接在线性 BLER 域插值"),
                ("低于横轴", "BLER=1", "不能外推到大于 1 或沿首段斜率延伸"),
                ("高于横轴", "钳到最后一个实测 BLER", "不能外推虚构 10^-6 尾部"),
                ("目标门限", "在包围 target 的两点间做同一 log 插值反解", "不能取最近网格点"),
                ("MCS 选择", "逐档检查 NewTx BLER≤target，取最高 index", "不能只按 η 或固定 SINR gap 估计"),
            ],
        )
        + '<h2>56 条原始曲线：先看门限，再看完整瀑布</h2>'
        '<p>第一张图比较 BLER=10% crossing；第二张图绘制全部 28×NewTx/ReTx。'
        '实线/虚线间距只描述原始资产，不能解释成当前系统的标准 HARQ 增益。当前系统初传、CC、IR '
        '全部只消费 NewTx 实线。</p>'
        + bler_threshold_svg()
        + bler_curve_atlas_svg()
        + '<h2>28 档曲线审计表</h2>'
        '<p><code>R New/Re</code> 是源曲线随附码率；表中没有 TB size/rank/场景列，是因为当前'
        '预置 profile 明确把 MCS 曲线视为通用曲线，不是导入遗漏。</p>'
        + bler_curve_summary_table()
        + '<h2>从 [RB,stream] 到 ACK/NACK：七步实现顺序</h2>'
        + table(
            ["步骤", "输入→输出", "必须锁住的不变量"],
            [
                ("1 数据体检", "raw rows→可信 profile", "28 顺序 MCS、56 曲线、SHA、有限/单调/target crossing"),
                ("2 RBG 内聚合", "逐 RB/stream 线性 SINR→逐 RBG/stream dB", "每 16 RB 先在线性域逐流平均"),
                ("3 单码字压缩", "逐 RBG/stream dB→γcw", "只平均本 grant RBG 与选定 rank streams"),
                ("4 选择 MCS", "γcw+target→m*", "只查 NewTx；最高满足档；MCS0 fallback 仍保留真实高 BLER"),
                ("5 TB 判错", "m*+γcw→p→ACK/NACK", "一个 UE grant/TTI 只抽一次；不做 CB 二次合成"),
                ("6 唯一重传", "NACK→CC/IR lookup", "空口 MCS/RBG 数/rank/TBS 冻结；lookup MCS 不能写回 DCI"),
                ("7 结果记账", "ACK/NACK→队列/BLER/KPI", "重传失败结束 HARQ；窗口末 pending 单列右删失"),
            ],
        )
        + '<h2>不依赖 SuperRAN 的 NumPy 参考实现</h2>'
        '<p>下面代码只依赖 NumPy 和本页完整 JSON。它实现 SHA 核对、横轴重建、对数域插值、'
        '门限反查、单码字 SINR 聚合、MCS 选择、CC/IR 和 ACK 抽样；末尾五个断言是冻结锚点。'
        '复制后若输出 PASS，就已经重建了本章当前系统 BLER 核心。</p>'
        + code(BLER_REFERENCE_IMPLEMENTATION, "python")
        + '<h2>必须得到的复现锚点</h2>'
        + table(
            ["锚点", "期望值", "抓住的错误"],
            [
                ("MCS15 @ 14.00 dB", f"BLER={float(m15.evaluate(14.0)[0]):.4f}", "线性概率插值/横轴偏移"),
                ("MCS15 @ 14.05 dB", f"BLER={float(m15.evaluate(14.05)[0]):.4f}", "0.05 dB 网格被粗化"),
                ("MCS15 的 10%门限", f"{m15.required_sinr_db(0.1):.6f} dB", "最近点代替 log 反插值"),
                ("MCS20/16 dB CC", f"lookup MCS20, SINR={float(cc20['lookup_sinr_db']):.6f} dB, BLER={float(cc20['bler']):.6f}", "3 dB 写成近似常数或误用 ReTx 行"),
                ("MCS20/16 dB IR", f"lookup MCS{int(ir20['lookup_mcs'])}, BLER={float(ir20['bler']):.6f}", "半谱效映射写死成 MCS7/8"),
            ],
        )
        + '<h2>完整 1,824 点机器可复制 JSON</h2>'
        '<p>把下面内容原样保存为 <code>preset_20b_256qam.json</code>。字段 '
        '<code>raw_mcs_curve_rows</code> 重新按 <code>hash_recipe</code> 序列化后，摘要必须等于'
        '<code>data_sha256</code>；否则不要继续画曲线或跑系统仿真。</p>'
        '<details class="raw-data"><summary>展开/复制完整 JSON（28 MCS，56 曲线，1,824 点）</summary>'
        + code(raw_payload, "json")
        + '</details>'
        + '<h2>复现完成的验收清单</h2>'
        + table(
            ["层次", "通过条件", "失败时先查"],
            [
                ("数据", "SHA 一致；28/56/1824；每条 x 递增、BLER 非增且在 (0,1]", "JSON 编码、float 精度、分支索引"),
                ("曲线", "五个冻结锚点逐值一致；边界钳位一致", "log10 插值、left/right 参数"),
                ("MCS", "所有测试 SINR 都选到相同最高满足档", "target 比较符、MCS0 fallback、重复 SE 档"),
                ("SINR", "全平输入原值返回；+20/−20 dB 两 RBG 得 0 dB", "是否误做全局线性平均"),
                ("HARQ", "MCS20 IR lookup=10；CC +3.0102999566 dB；空口身份不变", "lookup MCS 与 transmitted MCS 混写"),
                ("系统", "逐 TB 一次 draw；字节守恒；最多一次重传；pending 右删失", "CB 二次合成、第二次重传、窗口边界"),
            ],
        )
        + '<p><strong>明确边界：</strong>复现上述内容等于复现 SuperRAN 当前预置 Table 3 的工程'
        'BLER 抽象，不等于复现真实 LDPC decoder、RV/LLR 软缓冲或一套 3GPP 标准 BLER 曲线。</p>'
        + '</section>'
    )


def bf_gain_svg() -> str:
    body = svg_box(22, 92, 180, 76, "gNB 可见 CSI", "h_prec / 可能陈旧", "accent")
    body += svg_box(258, 24, 190, 72, "PMI 参照方向", "Type-I-style 宽带", "b")
    body += svg_box(258, 166, 190, 72, "TX 发送方向", "默认 SVD", "b")
    body += svg_box(500, 24, 190, 72, "同功率约束 C", "默认 NEBF", "warn")
    body += svg_box(500, 166, 190, 72, "同功率约束 C", "默认 NEBF", "warn")
    body += svg_box(744, 24, 176, 72, "post-MMSE", "逐 RB × 逐流", "good")
    body += svg_box(744, 166, 176, 72, "post-MMSE", "逐 RB × 逐流", "good")
    body += svg_box(970, 92, 150, 76, "物理 TX − PMI", "RBG/流 dB 聚合", "accent")
    body += arrow(202, 116, 258, 60)
    body += arrow(202, 144, 258, 202)
    body += arrow(448, 60, 500, 60)
    body += arrow(448, 202, 500, 202)
    body += arrow(690, 60, 744, 60)
    body += arrow(690, 202, 744, 202)
    body += arrow(920, 60, 970, 112, "PMI")
    body += arrow(920, 202, 970, 148, "TX")
    return svg_wrap(
        body, 1140, 270,
        "同一 gNB CSI、rank、功率约束与接收机；唯一改变预编码方向",
    )


def link_flow_svg() -> str:
    items = [
        (25, "CQI", "长期宽带"), (175, "Γ(MCS(CQI))", "CQI 门限反映射"),
        (365, "BF Gain", "SVD − PMI"), (525, "基准 MCS", "SINR 反折"),
        (680, "SU OLLA", "MCS 域闭环"), (820, "发送 MCS", "floor + clip"),
        (975, "BLER", "真实 SINR 查表"),
    ]
    body = ""
    boxes = []
    for x, t, s in items:
        w = 130 if x != 175 else 160
        boxes.append((x, 52, w, 68, t, s))
        body += svg_box(x, 52, w, 68, t, s, "accent" if t in ("CQI", "MCS") else "b")
    for a, b in zip(boxes[:5], boxes[1:5], strict=False):
        body += arrow(a[0] + a[2], 86, b[0], 86, "+")
    body += arrow(810, 86, 820, 86)
    body += arrow(950, 86, 975, 86, "curve")
    body += svg_box(365, 185, 180, 70, "CorrLoss", "MU 残留相关干扰", "danger")
    body += svg_box(575, 185, 180, 70, "PowerLoss", "−10log10(KMU)", "danger")
    body += svg_box(785, 185, 180, 70, "MU OLLA", "用户级、非 pair 级", "warn")
    body += arrow(455, 185, 610, 120, "+")
    body += arrow(665, 185, 660, 120, "+")
    body += arrow(875, 185, 710, 120, "+")
    return svg_wrap(body, 1140, 285, "CQI 查表、SINR+BF 反折基准 MCS，再叠加 MCS-domain OLLA")


def mu_decision_svg() -> str:
    body = svg_box(25, 25, 220, 72, "PF 排序一次", "得到 anchor → candidates", "accent")
    body += svg_box(315, 25, 220, 72, "构造 SU plan", "按序给最小够用 RBG", "b")
    body += svg_box(605, 25, 220, 72, "构造 MU plan", "真实 pair 表；2UE×rank2", "b")
    body += svg_box(895, 25, 220, 72, "比较 useful bytes", "超出队列的 padding 不计", "good")
    body += arrow(245, 61, 315, 61)
    body += arrow(535, 61, 605, 61)
    body += arrow(825, 61, 895, 61)
    body += svg_box(260, 165, 250, 78, "SU 清空全部队列？", "是 → 强制 SU，剩余 RBG 留空", "good")
    body += svg_box(605, 165, 250, 78, "否则 MU ≥ SU？", "是 → MU；否 → SU", "accent")
    body += arrow(1005, 97, 385, 165, "先判断")
    body += arrow(510, 204, 605, 204, "否")
    body += '<text class="yes" x="386" y="267">是 → SU</text><text class="yes" x="730" y="267">是 → MU；否 → SU</text>'
    return svg_wrap(body, 1140, 295, "experience_v2 每个 DL TTI 的 SU/MU 自适应决策")


def phases_svg() -> str:
    body = svg_box(35, 36, 485, 82, "Phase A · 链路预计算", "H → CSI → rank/MCS/SINR；MU pair tables", "accent")
    body += svg_box(620, 36, 485, 82, "Phase B · TTI 主循环", "traffic → PF → plan → grant → BLER → KPI", "good")
    body += arrow(520, 77, 620, 77, "纯查表")
    for i, (title, sub) in enumerate((
        ("UE/Snapshot/Rank", "SVD/PMI/BF gain"), ("SU table", "best_rank / SINR / MCS"),
        ("MU pair table", "CorrLoss / true SINR"), ("RB-PC table", "逐 RBG/RB 可选"),
    )):
        body += svg_box(35 + i * 270, 180, 235, 66, title, sub, "b")
        if i:
            body += arrow(35 + (i - 1) * 270 + 235, 213, 35 + i * 270, 213)
    body += '<text class="ds" x="570" y="286">主循环禁止重复矩阵分解；legacy_v1 标量 MU 与 experience_v2 pair 表必须分开解释</text>'
    return svg_wrap(body, 1140, 315, "系统仿真的两相架构")


def traffic_kpi_svg() -> str:
    heights = [150, 112, 54, 34, 28, 22, 18, 16, 18, 20, 22, 27, 33, 45, 62, 94, 142, 172]
    body = '<line class="axis" x1="60" y1="260" x2="680" y2="260"/><line class="axis" x1="60" y1="40" x2="60" y2="260"/>'
    for i, h in enumerate(heights):
        x = 70 + i * 33
        body += f'<rect class="hist" x="{x}" y="{260-h}" width="24" height="{h}" rx="3"/>'
        if i in (0, 1, 5, 9, 13, 17):
            body += f'<text class="tiny" x="{x + 12}" y="280">{i}</text>'
    body += '<text class="dt" x="280" y="310">每个 TTI 的占用 RBG 数（0..17）</text>'
    body += svg_box(760, 48, 310, 64, "首包时延", "arrival → first scheduled", "accent")
    body += svg_box(760, 142, 310, 64, "含头速率", "分母额外包含首包等待", "good")
    body += svg_box(760, 236, 310, 64, "MU 配对比例", "MU PRB / 已用 PRB", "warn")
    body += arrow(680, 130, 760, 80)
    body += arrow(680, 180, 760, 174)
    body += arrow(680, 230, 760, 268)
    return svg_wrap(body, 1120, 340, "mixed 话务常见的两头高 RBG 占用分布及三个关键体验 KPI")


def rb_power_svg() -> str:
    body = svg_box(35, 45, 240, 72, "Cell A · RBG0 ↑", "qA,0 增大；总功率守恒", "accent")
    body += svg_box(35, 175, 240, 72, "Cell A · 其他 RBG ↓", "qA,r 被迫降低", "warn")
    body += svg_box(440, 25, 255, 72, "UE A on RBG0", "服务信号增强", "good")
    body += svg_box(440, 125, 255, 72, "邻区 UE on RBG0", "来自 Cell A 的干扰增强", "danger")
    body += svg_box(440, 225, 255, 72, "UE A on other RBG", "服务信号减弱", "danger")
    body += svg_box(850, 125, 250, 72, "系统结果", "取决于调度/干扰/频选联合", "accent")
    body += arrow(275, 81, 440, 61, "+S")
    body += arrow(275, 81, 440, 161, "+I")
    body += arrow(275, 211, 440, 261, "−S")
    body += arrow(695, 61, 850, 150)
    body += arrow(695, 161, 850, 161)
    body += arrow(695, 261, 850, 172)
    return svg_wrap(body, 1140, 330, "为什么抬升 RBG0、压低其他 RBG 可能让整体性能下降")


def gates_svg() -> str:
    body = svg_box(35, 55, 255, 84, "门 1 · 数据体检", "18 checks；物理/合同/样本", "accent")
    body += svg_box(355, 55, 255, 84, "门 2 · 结果可信", "paired/clustered CI；CRN", "good")
    body += svg_box(675, 55, 255, 84, "门 3 · 可发布", "预注册、效应量、边界", "warn")
    body += arrow(290, 97, 355, 97, "PASS")
    body += arrow(610, 97, 675, 97, "PASS")
    body += svg_box(355, 205, 255, 70, "BLOCK", "不写提升百分比；补数据/修配置", "danger")
    body += arrow(482, 139, 482, 205, "FAIL")
    body += svg_box(675, 205, 255, 70, "LIMITED", "只写观察值与适用边界", "b")
    body += arrow(802, 139, 802, 205, "证据不足")
    return svg_wrap(body, 970, 305, "三道门把“能运行”与“能下结论”分开")


def skill_flow_svg() -> str:
    labels = [
        (25, "1 头脑风暴", "问题/基线/主指标"), (300, "2 计划书", "四项可见计划"),
        (575, "3 生成 + 门1", "数据先体检"), (850, "4 实验 + 门2/3", "证据后结论"),
    ]
    body = ""
    for i, (x, title, sub) in enumerate(labels):
        body += svg_box(x, 55, 230, 78, title, sub, "accent" if i == 0 else "b")
        if i:
            body += arrow(labels[i - 1][0] + 230, 94, x, 94)
    body += '<text class="ds" x="550" y="190">HARD-GATE：未通过时不能用“趋势上/总体来看”绕过，也不能手算救结论</text>'
    return svg_wrap(body, 1110, 225, "channel-sim Skill 的强制收敛与证据工作流")


def product_surfaces_showcase() -> str:
    """Real browser captures of the two flagship user-facing surfaces."""

    spec_shot = real_ui_screenshot(
        "spec-workbench-overview.png",
        "SuperRAN 运行前交互配置工作台，包含多站拓扑、配置来源和导出分享操作栏",
        "真实 Edge/Chromium 截图 · 1440×900 · 7 站 21 小区 64T 运行前说明书",
    )
    kpi_shot = real_ui_screenshot(
        "kpi-workbench-comparison.png",
        "SuperRAN 多算法 KPI 对比工作台，包含固定基线、候选算法系列、问题型 Tab 与置信区间柱形图",
        "真实 Edge/Chromium 截图 · 1440×900 · 三算法 CRN 对比证据首屏",
    )
    return """
<section class="product-showcase" aria-labelledby="product-surfaces-title">
  <div class="product-showcase-head">
    <span>FLAGSHIP PRODUCT SURFACES</span>
    <h2 id="product-surfaces-title">运行前看清配置，运行后读懂证据</h2>
    <p>SuperRAN 不要求用户盯着 YAML 和长 JSON。Agent 把同一份 resolved config 画成可回传的
    交互配置工作台；体验仿真结束后，再把 Result contract 变成会按问题调整首屏重点的 KPI 工作台。
    两页都自包含、可离线打开，并提供 JSON/CSV 下载、摘要复制、页面截图、系统分享与打印/PDF。
    下方不是概念效果图，而是本仓 QA 脚本刚刚驱动真实 Chromium 得到的产品截图。</p>
  </div>
  <div class="surface-grid">
    <div class="surface-card" data-product-surface="spec">
      <div class="surface-copy">
        <span class="surface-stage">RUN · BEFORE</span>
        <h3>交互配置 Mock · 仿真说明书</h3>
        <p>拓扑、阵列、频域、TDD、PDP、SRS/PMI 与算法链集中核对；用户改动白名单控件后点击
        “应用到仿真”，delta 经 loopback bridge 回到原 Draft。页面同时显示改动会重算信道、
        链路表、TTI 主循环还是仅重绘 KPI。</p>
        <a href="#/agentloop">查看配置闭环与安全边界 →</a>
      </div>
      """ + spec_shot + """
    </div>
    <div class="surface-card" data-product-surface="kpi">
      <div class="surface-copy">
        <span class="surface-stage">RUN · AFTER</span>
        <h3>多算法 KPI 对比与单 TTI 复盘</h3>
        <p>2~5 个算法同屏固定颜色，基线不可隐藏；总览、KPI 矩阵、用户 CDF、TTI 趋势、同 TTI grant 详情与
        统计门禁形成一条钻取链。单臂工作台仍保留 26 项小区 KPI、24 项用户 KPI 和 Agent 自适应首屏。</p>
        <a href="#/kpi">查看 KPI 口径与工作台合同 →</a>
      </div>
      """ + kpi_shot + """
    </div>
  </div>
</section>
"""


def overview_page(modules: list[ModuleDoc], tools: list[SymbolDoc], tests: list[dict[str, Any]],
                  skills: list[dict[str, Any]]) -> Page:
    source_lines = sum(m.lines for m in modules)
    top_symbols = sum(len(m.symbols) for m in modules)
    nested_members = sum(len(s.members) for m in modules for s in m.symbols)
    test_lines = sum(t["lines"] for t in tests)
    body = """
<section class="hello-world overview-hello" data-hello-world="overview-entry">
  <span>START HERE · RUN ONE TRUSTED EXPERIMENT</span>
  <h2>Hello World：SRS 权相较于 PMI 权，性能真的更好吗？</h2>
  <p>第一条学习路径不是浏览 API，而是运行一个会被证据门约束的完整实验：先在同一 UL-SRS 估计上隔离
  SVD 与 Type-I-style 码本的构造差异，再按真实信息链比较 UL-SRS/SVD 与 DL-CSI-RS/PMI。
  当前固定样例的点估计是 +0.7%，但 95% CI 跨零，因此平台给出的第一条可信结论恰好是
  <strong>“不能宣称有收益”</strong>。</p>
  <div class="paths-grid hello-actions">
    <a href="#/quickstart"><b>打开可运行 Hello World</b><span>一条命令 → 配置 Mock → Gate 1/2/3 → 完整 JSON 证据</span></a>
    <a href="#/agentloop"><b>先看交互配置 Mock</b><span>运行前核对拓扑、阵列、PDP、SRS/PMI 与用户 delta</span></a>
    <a href="#/kpi"><b>再看 KPI 工作台</b><span>运行后查看小区/用户双 Tab、CDF、PRB 与 MU 资源画像</span></a>
  </div>
</section>
"""
    body += metric_cards((
        ("源码模块", str(len(modules)), f"{source_lines:,} 行 Python"),
        ("公开顶层 API", str(top_symbols), f"另含 {nested_members} 个公开成员/字段"),
        ("MCP 工具", str(len(tools)), "由 server.py AST 实时提取"),
        ("测试文件", str(len(tests)), f"{test_lines:,} 行可执行检查"),
        ("Skill 文档", str(len(skills)), "1 主文件 + references"),
    ))
    body += product_surfaces_showcase()
    body += architecture_svg()
    body += """
<h2>一句话定位</h2>
<p><strong>SuperRAN 是给 Agent 使用的无线仿真实验编排与证据平台。</strong>
它把 ChannelHub/Sionna 等物理内核包装成稳定的数据合同、MCP 工具、系统仿真和三道证据门。
它的目标不是“能画一条曲线”，而是让配置、真值、估计、随机数、统计和结论都能回溯；
交互配置 Mock 与 KPI 工作台分别承载运行前确认和运行后解释。</p>
"""
    body += callout(
        "warn", "最重要的边界",
        "<p><code>capacity / legacy_v1</code> 与 <code>experience / experience_v2</code> "
        "是两种评估模式，不是同一算法的精度开关。前者复现全带调度历史行为；后者实现 FIFO、"
        "按需 RBG、真实 MU pair、PF 实际 TBS 记账和体验 KPI。跨模式比较必须把语义差异写出来。</p>",
    )
    body += """
<h2>推荐阅读路径</h2>
<div class="paths-grid">
  <a href="#/quickstart"><b>Hello World：SRS 权 vs PMI 权</b><span>同 SRS 机制诊断 + SRS/CSI-RS 主实验 → Gate 2/3</span></a>
  <a href="#/agentloop"><b>使用交互配置 Mock</b><span>Draft → 说明书 → 页面回传 → 唯一 resolved config</span></a>
  <a href="#/kpi"><b>进入 KPI 工作台</b><span>小区/用户双 Tab → CDF → RBG/MU 资源画像 → 自适应首屏</span></a>
  <a href="#/antenna"><b>复核 64T 物理</b><span>双极化 → 1 驱 3 → F(192×64) → 端口信道</span></a>
  <a href="#/experience"><b>实现体验速率</b><span>Phase A/B → traffic → PF → RBG → KPI</span></a>
  <a href="#/extension"><b>扩展算法</b><span>接口边界 → 不变量 → 测试 → MCP/Skill 文档同步</span></a>
  <a href="#/pdp"><b>读懂 PDP</b><span>频域 H → Hann/IFFT → 圆周矩 → 分辨率边界</span></a>
  <a href="#/csi"><b>追踪 CSI 生命周期</b><span>SRS → 报告 → PMI/CQI hold → 老化复评</span></a>
  <a href="#/pmi"><b>审计 PMI</b><span>Type-I 列 → 端口置换 → RI/CQI/BF Gain → report hold</span></a>
  <a href="#/powercontrol"><b>拆开功控自由度</b><span>每天线 → 逐流 → 逐 RB → 邻区活动与 S/I/N</span></a>
  <a href="#/robust"><b>实现鲁棒权</b><span>CSI-error loading × EBF/PEBF/NEBF 两个设计轴</span></a>
  <a href="#/calibration"><b>校准物理模型</b><span>38.901 §7.8 → CDF/KS → Gate 职责边界</span></a>
  <a href="#/raytracing"><b>核对射线追踪身份</b><span>场景资产 → Paths/CFR → RT/fallback → probe 边界</span></a>
  <a href="#/referencesignals"><b>复核参考信号</b><span>RB 表 → TDD → Gold/SRS → CSI-RS 扫描</span></a>
  <a href="#/bler"><b>分清 BLER 后端</b><span>当前 dB 聚合 → 预置曲线；QAM/MIESM 是可选分析链</span></a>
  <a href="#/externalresults"><b>接入外部算法</b><span>预注册 → ResultArtifact → 有序配对 → 发布门</span></a>
</div>
<h2>本页如何保持可信</h2>
<p>页面中的易漂移数字由生成器直接扫描当前 AST 和文件系统。算法解释旁的
<span class="src">源码</span>链接定位到实现；“当前边界”与“推荐演进”分开写。
历史 HTML 仍保留，但它们是某次审计快照，不再承担当前 API 真相源。</p>
"""
    return Page(
        "overview", "项目总览", "开始", "SUPERRAN DEVELOPER GUIDE",
        "从物理信道到系统 KPI，再到可信结论的一张全景地图。", body,
        ("MCP", "ChannelHub", "系统仿真", "开发者"),
    )


def quickstart_page() -> Page:
    install = r"""
cd C:\Vibe\Wireless\SuperRAN
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .

# 可选：射线追踪能力（约 300 MB 依赖）
python -m pip install -e ".[rt]"

# stdio MCP 服务；也可用安装后的 superran-mcp
python -m superran.server
"""
    mcp = r'''{
  "mcpServers": {
    "superran": {
      "command": "C:\\Vibe\\Wireless\\SuperRAN\\.venv\\Scripts\\python.exe",
      "args": ["-m", "superran.server"],
      "env": {
        "SUPERRAN_CHANNELHUB": "C:\\Vibe\\Wireless\\MSG-Platform"
      }
    }
  }
}'''
    first = r'''# Hello World：先隔离码本机制，再比较真实 SRS/SVD 与 CSI-RS/PMI 方案
caps = sr_capabilities()
draft = sr_plan(
    intent="在单小区 64T4R 全带场景，用同一批 SRS 估计信道比较 SVD 权与 PMI 权的谱效",
    preset="company_64t4r",  # 同一公司阵列/100 MHz；入门实验先隔离多小区干扰
    overrides={"link": "BOTH"},  # 必须生成 DL 真值 + UL SRS 估计的 paired contract
)

# 运行前主界面：把 resolved config 画出来，并允许用户回传白名单参数 delta
sheet = sr_spec_sheet(
    draft_id=draft["draft_id"],
    title="Hello World · SRS/SVD 权 vs PMI Type-I-style 权",
    highlight=["channel_est_mode", "antenna_preset", "port_order"],
)
# 把 sheet["url"] 发给用户。若他点击“应用到仿真”，再用
# sr_await_config(spec_id=sheet["spec_id"]) + sr_revise(...) 接收修改。

data = sr_generate(draft_id=draft["draft_id"], num_samples=80)
gate1 = sr_gate(
    dataset_id=data["dataset_id"],
    expected_precoding_csi_source="ul_srs_estimate",
)
if not gate1["passed"]:
    raise RuntimeError(gate1["verdict"])

codebook_diagnostic = sr_compare_arms(
    dataset_id=data["dataset_id"],
    method_a="svd",   name_a="SRS估计 + 逐RB协方差/SVD权", csi_a="srs",
    method_b="type1", name_b="SRS估计 + Type-I-style码本权", csi_b="srs",
    receiver="mmse",
)

# 主实验：真实来源不同是方案定义的一部分，必须显式声明，不能伪装成同 CSI。
comparison = sr_compare_arms(
    dataset_id=data["dataset_id"],
    method_a="svd",   name_a="UL-SRS估计 + 协方差/SVD权", csi_a="srs",
    method_b="type1", name_b="DL-CSI-RS估计 + PMI Type-I-style权", csi_b="csirs",
    receiver="mmse", varies=["csi", "method"],
)
print(comparison["statement"])  # 只有 passed=True 才能写成已验证收益
'''
    followup = r'''# 可选：同一数据集继续跑 5 s 体验仿真，1 s 后开始统计
experience = sr_system_sim(
    dataset_id=data["dataset_id"],
    evaluation_mode="experience",
    traffic_model="mixed",
    duration_s=5.0,
    warmup_s=1.0,
    target_prb_utilization=0.30,
    num_replications=8,
    kpi_intent="关注边缘体验、首包时延、PRB 利用率与用户差异",
)
workbench = experience["kpi_view"]
print(workbench["url"] or workbench["html_path"])
'''
    body = """
<section class="hello-world" data-hello-world="srs-vs-pmi">
  <span>HELLO WORLD · FIRST TRUSTED EXPERIMENT</span>
  <h2>SRS 估计权相较于 PMI 权，究竟有没有收益？</h2>
  <p>这不是手写一个矩阵再比均值，而是走完 SuperRAN 的最小产品闭环：能力发现 → Draft →
  交互配置 Mock → 同一数据集 → Gate 1 → 配对比较 → Gate 2/3。复制下面的 MCP 调用顺序即可运行；
  页面与结论都会返回可打开地址或结构化证据。</p>
</section>
"""
    body += code(
        r"python -u scripts\run_srs_pmi_hello_world.py",
        "PowerShell · runnable repository example",
    )
    body += metric_cards((
        ("数据体检", "Gate 1 PASS", "80 snapshots · 10 个独立 UE 位置"),
        ("主实验点估计", "+0.7%", "34.005 vs 33.765 bit/s/Hz"),
        ("95% CI", "−5.622 ~ +6.103", "跨零；Wilcoxon p=0.846"),
        ("发布判决", "BLOCK", "证据已写出，但不能宣称收益"),
    ))
    body += callout(
        "warn", "脚本退出码 3 是一次成功的可信度演示",
        "<p>当前固定样例 <code>ds_312bd664</code> 的数据与公平性门均通过，但 80 条原始快照按位置聚类后只有 "
        "10 个独立配对，主实验置信区间跨零。脚本仍会把完整证据写入 "
        "<code>artifacts/SRS_PMI_HELLO_WORLD_RESULT.json</code>，再用退出码 3 阻止流水线把点估计包装成收益。"
        "同 SRS 的机制诊断另报 +15.9%，但其 CI [−0.542,+9.881] 同样跨零，因此也只保留为 exploratory。</p>",
    )
    body += code(first, "MCP tool sequence · equivalent explicit flow")
    body += callout(
        "good", "两张比较表回答两个不同问题",
        "<p>页面同时给两张配对表。机制诊断的两臂都用同一份上行 SRS <code>h_est</code>，只把"
        "连续 SVD 权换成 Type-I-style 码本，回答纯码本损失；主实验则按真实链路让 SRS/SVD 读取"
        "互易映射后的 UL SRS 估计，让 PMI 读取 UE 的 DL CSI-RS 估计，并显式声明"
        "<code>varies=[csi,method]</code>。两张表共享同一 <code>h_true</code>、MMSE 接收机和逐样本工作点，"
        "但结论含义不同。当前 PMI 是 Type-I-style 单面板列码本近似，不应写成完整 38.214 Type-I。</p>",
    )
    body += callout(
        "note", "为什么 Hello World 用单小区，而不是 21 小区",
        "<p><code>company_64t4r</code> 与正式多小区预设使用同一 64T4R 公司阵列、100 MHz、"
        "272 RB、CDL-C 与 <code>ls_mmse</code>，但先隔离邻区干扰，让第一次实验只回答"
        "“连续 SRS/SVD 方向与有限 PMI 码本的差异”。<code>80=10 UE×8 snapshots</code> 也满足"
        "后续体验模式的最低快照数。21 小区全带场景属于进阶压力验证；它不能被包装成一分钟入门，"
        "也不能与单小区结果混写成同一适用范围。</p>",
    )
    body += steps((
        ("发现能力并冻结问题", "<p><code>sr_capabilities</code> 不静默降级；<code>sr_plan</code> 把场景、估计模式、基线和主指标写进 Draft。</p>"),
        ("先让用户看交互配置 Mock", "<p><code>sr_spec_sheet</code> 展示将要执行的真实拓扑与算法。页面改动只回传 delta，并由同一 Draft 重新解析。</p>"),
        ("生成并通过 Gate 1", "<p><code>sr_generate(draft_id=...)</code> 不再绕开已确认配置；18 项数据体检有拦截就停止。</p>"),
        ("在同一样本上过 Gate 2/3", "<p><code>sr_compare_arms</code> 做配对统计、公平性与可发布性检查。未通过时只报告观察值，不写“提升 X%”。</p>"),
    ))
    body += """
<h2>进阶：把结论移到多小区干扰环境</h2>
<p>入门闭环跑通后，再把 preset 改成 <code>company_64t4r_multicell</code>，重新预注册、生成、
过 Gate 1，并在同一份新数据内配对。多小区 64T×272 RB 的生成成本显著更高；不要复用单小区
dataset，也不要把两种场景的均值直接相减。并行器会按 UE batch 收口 worker 数，避免每个一
样本分块都重复构造整批 UE 信道。</p>
"""
    body += """
<h2>把同一数据集继续送进 KPI 工作台</h2>
<p>链路级 Hello World 回答“哪种权的谱效更高”；下面这一步才回答有限话务下的首包、体验速率、
PRB 占用和用户差异。<code>sr_system_sim(evaluation_mode="experience")</code> 会自动写出自包含 HTML，
返回 <code>kpi_view.url/html_path</code>、小区/用户双 Tab 和完整 KPI 排序证据。</p>
""" + code(followup, "MCP tool sequence")
    body += """
<h2>环境与安装</h2>
<p>最低 Python 版本是 3.10。基础包只要求 NumPy/SciPy/Pydantic/PyYAML/structlog/MCP；
Sionna RT 是显式可选依赖。ChannelHub 源码默认在项目周边自动发现，也可用
<code>SUPERRAN_CHANNELHUB</code> 指定。</p>
""" + code(install, "PowerShell")
    body += callout(
        "note", "Windows 路径注意",
        "<p>不要照抄 ChannelHub 旧文档中的某台机器虚拟环境路径。MCP 配置应写当前机器的"
        "绝对 Python 路径；项目产物默认落在 <code>artifacts/</code>，可由环境变量整体迁移。</p>",
    )
    body += """
<h2>SuperRAN 重命名的最小兼容边界</h2>
<p>新代码与配置只写 <code>SUPERRAN_*</code>。<code>_compat.py</code> 仅为已有开发机保留一个临时、
可审计的环境变量迁移层：当新键不存在而旧产品键存在时，才把 ARTIFACTS、CHANNELHUB、SCENES、
PRESETS 等 8 个后缀逐项复制到当前进程；新键永远优先，不覆盖用户的新配置。重复调用幂等，
<code>legacy_environment_audit()</code> 可查看实际迁移项，同时发出 deprecation warning。它不兼容旧包名、
CLI 或数据 schema，也不会永久维护两套命名。</p>
"""
    body += "<h2>把服务接给 Agent</h2>" + code(mcp, "json")
    body += """
<p>不同 Agent 宿主的配置外壳略有差异，但核心永远是 stdio 启动命令。服务端不主动执行
外部自研代码；外部算法通过结果契约注册逐样本值，再进入相同的门 2/门 3。</p>
<h2>开发内环与发布外环</h2>
<div class="compare"><div><h3>秒级开发内环</h3><p>AST/合同测试、纯 NumPy toy example、文档结构和公式检查。
适合每次编辑后执行。</p></div><div><h3>真实物理外环</h3><p>ChannelHub/Sionna 生成、多小区干扰、蒙特卡洛与浏览器 QA。
适合算法改动和发布前执行，可能需要数分钟到数小时。</p></div></div>
"""
    return Page(
        "quickstart", "Hello World 与安装", "开始", "GETTING STARTED",
        "用同 SRS 诊断码本机制，再比较 UL-SRS/SVD 与 DL-CSI-RS/PMI 真实方案，并进入交互配置页和 KPI 工作台。", body,
        ("Hello World", "SRS", "PMI", "交互 Mock", "KPI 工作台", "Gate 1/2/3"),
    )


def architecture_page() -> Page:
    body = architecture_svg()
    body += """
<h2>五层，而不是一个大脚本</h2>
"""
    body += table(
        ["层", "职责", "主要模块", "禁止越界"],
        [
            ("编排层", "自然语言意图、分轮决策、预设、说明书", "server / plan / decisions / spec / sysscenes", "不能偷偷替用户改变实验问题"),
            ("数据层", "生成、落盘、加载、观察量", "generate / channelhub / loader / measure / scenes", "h_est 缺失时禁止复制 h_true"),
            ("算法层", "预编码、接收、MCS、MU、功控", "beamforming / linklevel / linkadapt / mumimo / power_control", "设计 CSI 与评估真值必须分离"),
            ("系统层", "连续 TTI、话务、PF、FIFO、KPI", "system / experience / traffic / kpi_view / rng", "capacity 与 experience 不混口径"),
            ("证据层", "校准、Gate、预注册、结果合同", "validate / calibration / gates / analysis / results", "Gate 不通过不得发布强结论"),
        ],
    )
    body += """
<h2>ChannelHub 边界</h2>
<p>superran 复用 ChannelHub 的物理算法与数据源，但不复用其产品壳。适配层负责发现源码、
注入核实过的 38.901 表、预热依赖、取出 serving/interference/估计信道，并把不稳定的内部对象
压成项目自己的数据合同。</p>
<p>物理代码根与射线追踪资产根允许分离：当前可从相邻 <code>MSG-Platform</code> 加载最新
<code>src/msg_embedding</code>，同时从完整 ChannelHub checkout 读取 <code>configs/scenes</code>。
用户可用 <code>SUPERRAN_CHANNELHUB</code> 指定代码根、用 <code>SUPERRAN_SCENES</code>
独立指定场景目录；资产回退绝不会把 Python 物理实现一起退回旧版本。</p>
"""
    body += callout(
        "danger", "为什么不直接透传 ChannelHub bridge",
        "<p>产品桥可能做归一化、截断或门控，物理量会在不显眼处改变。"
        "SuperRAN 只从明确的数据源/算法入口取值，并在落盘前校验形状、有限性和角色。</p>",
    )
    body += """
<h2>h_true 与 h_est 是架构轴</h2>
""" + F_DATASET
    body += """
<p><code>h_true</code> 是下行物理评估信道；<code>h_est</code> 是 gNB 设计预编码时可见的 CSI。
在 <code>link=BOTH</code> 的 TDD 数据里，后者来自上行 SRS，而不是下行 CSI-RS。
二者同形不代表同值；若估计源缺失，生成器硬失败。</p>
<h2>数据流中的三个时间尺度</h2>
"""
    body += table(
        ["尺度", "当前对象", "典型用途", "常见混淆"],
        [
            ("OFDM symbol", "ChannelHub 内部可生成 14-symbol slot", "TDD 导频/估计、符号选择性", "系统层不需要每 TTI 重放 14 个 symbol"),
            ("channel snapshot", "系统链路表的物理快照", "CSI 老化、PMI/CQI 更新、真实 SINR", "快照周期不是 SRS 周期也不是 PMI 周期"),
            ("TTI", "Phase B 队列与调度步", "到达、PF、grant、BLER、OLLA、KPI", "一个 TTI 只需引用一个 snapshot"),
        ],
    )
    body += callout(
        "good", "复杂度选择",
        "<p>物理数据源可保留 1~14 个 symbol 来做导频映射、估计和 Doppler 演化；"
        "superran 的窄腰适配器随后取中间 symbol，保留长度为 1 的时间轴作为 slot snapshot。"
        "绝不能对复信道跨 symbol 求均值，否则旋转相位会相消并制造假衰落。Phase B 每 TTI 只查一个 "
        "snapshot。这是典型的链路到系统抽象，且显著降低复杂度。"
        "若研究 symbol-level mini-slot/DMRS，则必须显式扩展系统状态，不能假装现有 TTI 表已覆盖。</p>",
    )
    body += "<p class=source-row>实现入口：" + source_ref("src/superran/generate.py", '"h_true_role"') + " · " + source_ref("src/superran/channelhub.py", "def serving_channel") + " · " + source_ref("src/superran/system.py", "def build_link_tables") + "</p>"
    return Page(
        "architecture", "架构与数据合同", "开始", "ARCHITECTURE",
        "五层职责、ChannelHub 边界、h_true/h_est 与三个时间尺度。", body,
        ("h_true", "h_est", "ChannelHub", "Phase A", "Phase B"),
    )


def agentloop_page() -> Page:
    body = agent_loop_svg()
    body += """
<h2>代码里已经有一套完整的 Agent 仿真闭环</h2>
<p>SuperRAN 不只是 35 个工具的集合。<code>decisions.py</code> 把无线领域判断编码成有限任务画像、
实验设计问题、高影响参数、推荐选项、sweep 与物理 guard；<code>plan.py</code> 把自然语言意图变成
可差分修改、可跨会话保存的 Draft；<code>algorithms.py</code> 与 <code>algo_defs*.py</code> 再把本次实际采用的
算法、替代项、公式、适用边界和推导步骤交给说明书。此前这些能力只在 API 表里出现，本章把它们串成一条真实调用链。</p>
""" + F_PROFILE_SCORE
    body += callout(
        "warn", "当前分类器是确定性关键词路由，不是 LLM 语义理解",
        "<p>它的优点是可复现、可测试、不会因模型版本改变而偷偷换 TaskProfile；缺点是同义表达可能落入 "
        "<code>generic</code>。Agent 可以用上下文解释和补充 intent，但执行层仍只接受有限画像与显式配置。</p>",
    )
    body += """
<h2>为什么只问结论真正缺的槽位</h2>
""" + F_REQUIRED_SLOTS
    body += """
<p>第一轮把基线、主指标等实验设计与信道模型、阵列、场景等高影响参数一次问完；第二轮补齐其余项，
目标两轮、最多三轮。<code>also_configurable</code> 只用于告诉用户“还能调什么”，不能全部展开成新一轮问题。
用户说“按默认”时立即停止；样本量由试点差值方差反解，不把该做的计算推回给用户。</p>
<h2>一份配置如何从页面走到真实执行</h2>
""" + F_CONFIG_PRECEDENCE
    body += table(
        ["代码层", "承载对象", "必须保持的合同"],
        [
            ("decisions.py", "TaskProfile / Decision / DesignQuestion / guard", "为什么要问、默认理由和跑得出但无意义的组合"),
            ("plan.py", "Draft / preset / translate / resolved_config", "只做差分修改；历史和用户显式键可追溯"),
            ("algorithms.py", "本次实际 algorithm_list 与 derivations", "choice 必须由 resolved config 派生，不写静态宣传文案"),
            ("algo_defs*.py", "算法族、流程、alternatives 与 caveat", "当前选择、可替换项和数据流三者同时展示"),
            ("spec.py", "拓扑、频域、TDD、PDP、算法与可编辑控件", "画的必须是会执行的配置"),
            ("bridge.py", "本地说明书回传", "仅 127.0.0.1、随机 token、单一白名单、标量值与幂等 nonce"),
        ],
    )
    body += """
<h2>交互配置 Mock 是运行前的主界面</h2>
<p><code>sr_spec_sheet</code> 返回的不是一张装饰截图，而是一份自包含 HTML 应用：上半部分把
resolved config 解释成拓扑、阵列、频域、TDD、PDP 与算法链，下半部分只开放
<code>spec._EDITABLE</code> 登记过的控件。用户点击“应用到仿真”后，Agent 收到的是带
<code>spec_id</code> 与 nonce 的 delta，不是浏览器直接启动的一次旁路仿真。</p>
"""
    body += real_ui_screenshot(
        "spec-workbench-config.png",
        "SuperRAN 交互配置页签真实截图，展示白名单控件、实时拓扑、重算影响和一键回传操作栏",
        "真实交互态：把 ISD 从 500 m 改为 600 m 后，四层重算影响立即点亮；尚未旁路执行仿真。",
    )
    body += table(
        ["界面能力", "用户看到什么", "执行侧怎样保证真实"],
        [
            ("真实配置预览", "用户指定/系统补全、实际站点吸附、64T/256T 阵列、PDP/TDD/算法", "全部从 resolved config 派生；不维护页面专属默认值"),
            ("对话相关高亮", "本轮特别关心的 SRS 周期、估计方式、端口顺序等被顶到首屏", "highlight 只改变展示顺序，不改配置"),
            ("安全参数回传", "修改白名单下拉框/标量后点击“应用到仿真”", "loopback + token + payload/类型/nonce 校验；只产生 delta"),
            ("明确降级", "服务不可用时显示复制粘贴路径", "writeback=clipboard 与 serve_error 可见，不伪装成已回传"),
        ],
    )
    body += callout(
        "danger", "页面不是第二份配置真相",
        "<p>控件列表与 POST 白名单都从 <code>spec._EDITABLE</code> 派生；回传只生成显式 delta，再由 "
        "<code>revise_draft()</code> 形成新的 resolved config。若页面显示、Agent 复述和执行参数有三份各自维护的默认值，"
        "迟早会出现“看见的是 A、跑的是 B”。</p>",
    )
    body += code(r'''proposal = sr_plan(intent="比较估计 CSI 下的 MU 预编码")
# 按 proposal["round_questions"] 一次问完；用户回答后只提交差分
proposal = sr_revise(
    proposal["draft_id"],
    design={"baseline": "Type-I", "metric": "cell throughput"},
    overrides={"channel_est_mode": "ls_mmse"},
)
sheet = sr_spec_sheet(
    draft_id=proposal["draft_id"],
    highlight=["channel_est_mode", "power_constraint"],
)
# 把 sheet["url"] 给用户。仅在他点“应用到仿真”后：
submission = sr_await_config(spec_id=sheet["spec_id"])
if submission["got"]:
    proposal = sr_revise(
        proposal["draft_id"], overrides=submission["overrides"])
# 最终仍由修订后的 draft_id 生成，页面本身从不执行仿真
''')
    body += "<p class=source-row>决策：" + source_ref("src/superran/decisions.py", "def next_round") + " · 计划：" + source_ref("src/superran/plan.py", "def build_proposal") + " · 说明书：" + source_ref("src/superran/spec.py", "def build_spec") + "</p>"
    return Page(
        "agentloop", "决策引擎、交互配置工作台与说明书闭环", "开始", "AGENTIC SIMULATION",
        "自然语言如何收敛成可执行配置，以及算法清单与页面回传怎样保持同一真相源。", body,
        ("TaskProfile", "Decision", "Draft", "algorithm_list", "spec", "bridge"),
    )


def hardware_page() -> Page:
    body = metric_cards((
        ("RF 端口", "64", "8H × 4V × 2pol"),
        ("物理阵子", "192", "8H × 12V × 2pol"),
        ("终端", "4Tx / 4Rx", "BOTH：UL SRS + DL 真值"),
        ("载波", "2.6 GHz", "n41 · 100 MHz · 30 kHz"),
        ("频域", "272 RB", "17 RBG × 16 RB"),
    ))
    body += array_svg()
    body += array_256_svg()
    body += """
<h2>两套已确认的公司阵列合同</h2>
"""
    body += table(
        ["profile", "RF 端口", "物理 AE / 馈电", "端口顺序", "垂直编号"],
        [
            ("64T 基线", "8H×4V×2pol = 64", "8H×12V×2pol = 192；1 驱 3", "pol_h_v", "top_to_bottom"),
            ("公司 256T", "16H×8V×2pol = 256", "16H×48V×2pol = 1536；1 驱 6", "pol_h_v", "top_to_bottom"),
        ],
    )
    body += callout(
        "good", "64T/256T 统一按产品图规则锁定",
        "<p>两者都采用 <code>r=p·N_H·N_V+h·N_V+v</code>（0-based），"
        "即先极化块、再水平列、垂直行最快，且 v=0 是物理顶部。64T 的关键端口是"
        " 1/5/33，256T 是 1/9/129。该顺序已同时贯通 InternalSim、Sionna RT、"
        "QuaDRiGa、Type-I/DFT 码本与系统链路表；历史 64T 顺序只经显式置换读取。</p>",
    )
    body += """
<h2>272 不是标准表写错</h2>
<p>38.104 在 100 MHz / 30 kHz 下给 273 RB；项目显式取 272，是为了得到完整的
17×16 RBG，丢弃最后一个残块 RB。凡是调度/TBS/RBG 统计都用 272；凡是解释标准表时
同时标出 273，不能混写。SRS 仍能按标准表覆盖这 272 RB：<code>C_SRS=63</code>、
<code>B_SRS=1</code> 时顶层带宽 272 RB、单次 16 RB、17 次完整轮转。</p>
<p>标准反查还必须知道频率范围：50 MHz / 60 kHz 在 FR1 是 65 RB，在 FR2 是 66 RB；
100 MHz / 60 kHz 则分别是 135 与 132 RB。因此实现保留两张独立表，默认公司 n41 场景用
<code>FR1</code>，载频不低于 24.25 GHz 的物理后端显式选择 <code>FR2</code>。非标准带宽不会
再用除法猜一个 RB 数；合成频域网格必须显式给 <code>num_rb</code>。</p>
<h2>默认值如何生效</h2>
"""
    body += steps((
        ("carrier defaults", "<p>补 2.6 GHz、30 kHz、100 MHz、272 RB、64T/4T4R 和 BOTH。</p>"),
        ("panel guard", "<p><code>[8,4,2]</code> 挂载 64T/1 驱 3；<code>[16,8,2]</code> 挂载 256T/1 驱 6。其它面板不猜馈电结构。</p>"),
        ("metadata", "<p>summary 记录模式、阵子数、间距、极化、方向图来源、calibration_id 与端口布局合同版本。</p>"),
        ("Gate", "<p>端口数、BOTH 上下行 UE 端口、方向图真实性和默认值漂移均有检查。</p>"),
    ))
    body += callout(
        "warn", "UE 面板仍是工程假设",
        "<p><code>2H×1V×2pol</code> 的 4R UE 不是公司实测手机天线。它可配置，文档和结果都不能称为硬件真值。</p>",
    )
    body += """
<h2>6° 电下倾从哪来</h2>
<p>当前 <code>fixed_downtilt_deg=6.0</code> 是公司 AAU 配置块的默认工程基线，不是由场景几何
反推出来的自然常数。它进入每个固定垂直子阵（64T 的 1 驱 3、256T 的 1 驱 6）内部相位递进；用户可以通过
<code>bs_antenna.fixed_vertical_subarray.fixed_downtilt_deg</code> 任意覆盖。改变它等价于改变馈电
校准，应同时改变/记录 <code>calibration_id</code>，并用垂直波束峰值与 F 矩阵列范数回归。</p>
""" + F_FEED
    body += "<p class=source-row>唯一默认真相源：" + source_ref("src/superran/hardware.py", "def company_antenna_block") + "</p>"
    return Page(
        "hardware", "默认硬件与载波", "物理内核", "HARDWARE BASELINE",
        "64T 基线与公司 256T 可选阵列、272 RB 和 6° 电下倾的来源、作用与覆盖方式。", body,
        ("64T4R", "256T", "1驱6", "272 RB", "n41", "downtilt"),
    )


def channel_page() -> Page:
    body = topology_svg() + ray_construction_svg()
    body += """
<h2>信道矩阵如何建模</h2>
<p>每条 path/cluster 把功率、时延、多普勒、收发角、阵列响应和极化 Jones 耦合成复数 MIMO
系数；频域相位由时延决定，时间相位由多普勒决定。CDL 有逐径角度，可显式形成阵列方向；
TDL 只有功率时延轮廓，空间相关是统计近似。</p>
""" + F_RAY_POLARIZATION + F_CHANNEL + F_CHANNEL_SHAPE
    body += """
<p>落盘时项目把链路方向整理为 <code>[T,RB,BS_ant,UE_ant]</code>，因此默认下行单个频点可看作
64×4（发射端优先）存储约定；通信教材常写成 4×64。矩阵乘法时必须先看函数约定，不能凭
“64×4”猜转置。</p>
<h2>CDL-A~E 现在展开到什么粒度</h2>
<p>五张 profile 的 delay、power、AOD/AOA/ZOD/ZOA、每簇角扩展和 XPR 均与
38.901 Table 7.7.1-1~5 逐字段交叉核对。每个 diffuse table component 按 Table 7.5-3
展开成 20 条 ray：四组角度 offset 会独立随机耦合，每条 ray 有自己的交叉极化矩阵、初相和
Doppler。profile 中心角再整体旋到实际 BS→UE 几何；到达方位是反向 bearing，不是把 AOD 原样复制。</p>
"""
    body += table(
        ["profile", "表分量", "实际 ray 项", "关键口径"],
        [
            ("CDL-A", "23", "23×20 = 460", "NLOS；cASD/cASA/cZSD/cZSA/XPR 全进入"),
            ("CDL-B", "23", "23×20 = 460", "NLOS；同上"),
            ("CDL-C", "24", "24×20 = 480", "NLOS；不是旧实现的 23 行"),
            ("CDL-D", "14", "1 + 13×20 = 261", "row 0 为确定性镜面项；K=13.3 dB 已在 row 0/1 功率差中"),
            ("CDL-E", "15", "1 + 14×20 = 281", "row 0 为确定性镜面项；K=22 dB 已在 row 0/1 功率差中"),
        ],
    )
    body += callout(
        "danger", "CDL-D/E 的 K 因子只能算一次",
        "<p>标准表已经把镜面项与 diffuse 首项的 K 比例写进功率列。若生成器再做一次 Rician "
        "<code>sqrt(K/(K+1))</code> 混合，会人为把 LOS 变得过强。回归测试会把 profile 的 "
        "<code>k_factor_dB</code> 元数据改成 100 dB，并要求同 seed 输出逐位不变。</p>",
    )
    body += callout(
        "note", "configured profile 不一定是 effective profile",
        "<p><code>configured_channel_model</code> 是用户入口；逐链路 LOS/NLOS 状态若与它不兼容，"
        "生成器会切到同家族的兼容剖面，并把真正在每个样本使用的名称写进 "
        "<code>effective_channel_model(s)</code>。例如 NLOS realization 配置 CDL-D 时实际会用 "
        "CDL-C。摘要、repr 和体检必须同时展示两者，不能只报配置名。</p>",
    )
    body += """
<h3>TDL 的边界</h3>
<p>TDL 没有 CDL 那套逐簇中心角表，因此不能伪造 20-ray 几何。当前实现保留标准 PDP，使用
空间相关投影形成多天线统计结构；LOS 分量使用实际 AOD/AOA/ZOD/ZOA 和同一套 Jones/阵列响应。
它适合快速统计回退，但不能替代场景确定性 ray tracing。</p>
<h2>极化在 H 中不是“天线数乘 2”</h2>
<p>每个路径带 2×2 极化耦合/XPR，收发阵元有各自的复 Jones 向量。+45°/-45° 只是局部极化
基；经过路径 Jones 矩阵后会发生共极化与交叉极化耦合。只有把该耦合与空间 steering 一起
代入，双极化才真正影响相关性、秩和 MU 干扰。</p>
""" + F_JONES
    body += callout(
        "note", "Jones/XPR 的当前精确边界",
        "<p>公司 <code>effective_subarray / physical_reference</code> 的 InternalSim CDL 路径已经调用 "
        "<code>element_jones()</code>，将理想 ±45° 基与逐 ray 的 2×2 <code>Jℓ</code> 收缩；legacy 面板仍按 "
        "V/H 基兼容。<code>element_xpd_db=8</code> 不是公司实测方向相关 XPD：CDL 优先使用 profile "
        "自带 XPR，它只在无 profile 值或统计回退路径中生效。方向相关复 Jones/XPD 仍需公司实测表。</p>",
    )
    body += """
<h2>同站共享、异站不复制</h2>
<p>这不是个人偏好：3GPP TR 38.901 §7.5 明确要求不同 BS–UT 链路的 LSP 不相关，且同址 BS
扇区的 LSP 要相关；§7.6.3.3 再给出了多链路 LSP 相关过程。同一 site 的扇区因此共享站点级
大尺度环境状态，但各扇区仍因方位图、端口和链路角度得到不同复信道；不同 site 不能复制
同一份 realization。规范原文可在 <a href="https://www.etsi.org/deliver/etsi_tr/138900_138999/138901/19.03.00_60/tr_138901v190300p.pdf" target="_blank" rel="noreferrer">ETSI TR 138 901 V19.3.0</a> 复核。</p>
"""
    body += callout(
        "warn", "共享不等于相同",
        "<p>同站扇区可共享 LSP/cluster state，但波束方位、路径相位、端口响应和服务/干扰角色仍不同；"
        "不同站即便属于同一 UMa 统计分布，也要使用独立随机流。当前项目要用不变量验证，而不能只在"
        "文档里宣称。</p>",
    )
    body += """
<h2>ChannelHub、Sionna RT 与系统层各做什么</h2>
"""
    body += table(
        ["来源", "擅长", "项目中的角色", "边界"],
        [
            ("ChannelHub internal_sim", "38.901 风格 CDL/TDL、多小区几何、导频估计", "默认快速物理源", "部分阵列/干扰模型是工程近似"),
            ("Sionna RT", "场景网格、材料、射线与确定性路径", "可选城市/室内 RT 源", "依赖重；场景/材料质量决定可信度"),
            ("SuperRAN", "合同、硬件默认、算法、TTI、统计门", "编排与证据层", "不重新发明传播求解器"),
        ],
    )
    body += """
<h3>Sionna RT 的 slot 快照为什么这样做</h3>
<p>Sionna 的 <code>Paths.cfr()</code> 会按设备速度计算路径 Doppler，但调用方必须同时设置
<code>Receiver.velocity</code> 和物理采样率。当前实现用完整 UE 速度向量，并令采样率为
<code>1 / 平均 OFDM-symbol 周期</code>；RB 频率网格围绕载波中心对称。旧实现没设 velocity、
还沿用 1 Hz 默认采样，14 个 symbol 只是静态重复，本轮已修。调用语义可在
<a href="https://nvlabs.github.io/sionna/rt/api/paths.html" target="_blank" rel="noreferrer">Sionna RT Paths API</a>
与<a href="https://nvlabs.github.io/sionna/rt/tutorials/Mobility.html" target="_blank" rel="noreferrer">官方 Mobility 教程</a>复核。</p>
<p>系统层每个 TTI 只需要一个信道快照：14-symbol 网格先服务导频/估计和真实 symbol 级 Doppler，
落盘边界再取中间 symbol，保留长度为 1 的时间轴。不能对复信道做 symbol 平均——相位抵消会
凭空制造深衰；也不能把 14 个 symbol 当成 14 个 TTI。</p>
"""
    body += callout(
        "good", "标准表错误现在会阻断生成",
        "<p>相邻 MSG-Platform 的 CDL-A~E 已直接修正；superran 仍保留独立标准副本，用来兼容"
        "旧 checkout。启动时会按安装版本支持的 dataclass 字段安全覆盖并逐表核对；若校准异常，"
        "<code>channelhub._ensure_path()</code> 硬失败，不再吞掉异常后继续产出伪标准数据。</p>",
    )
    body += "<p class=source-row>适配入口：" + source_ref("src/superran/channelhub.py", "def probe_capabilities") + " · " + source_ref("src/superran/scenes.py", "class SceneInfo") + "</p>"
    return Page(
        "channel", "信道、场景与传播状态", "物理内核", "CHANNEL MODEL",
        "H 的路径公式、极化耦合、同站/异站状态与 ChannelHub/Sionna 分工。", body,
        ("CDL", "TDL", "Sionna", "Jones", "传播状态"),
    )


def raytracing_page() -> Page:
    body = raytracing_probe_svg()
    body += """
<h2>射线追踪不是把 source 名字改成 sionna_rt</h2>
<p>场景管理层区分 Sionna 内置城市与 ChannelHub 携带的真实城市资产。后者的 VTK PLY 头可能含
Mitsuba 3.8 不接受的 <code>obj_info</code>；<code>prepare_scene()</code> 会复制到项目缓存后清理，
绝不修改上游原文件。<code>resolve_scene_config()</code> 再把 scene/preset 展开成 OSM 路径、站点数、
站高、ISD 与载频。结果中必须读取 <code>channel_generation_mode</code> 区分真实 RT 与
<code>tdl_fallback</code>，不能仅看一个 channel_model 标签。</p>
<h2>Paths 到 CFR 的物理时间轴</h2>
""" + F_CHANNEL + F_MAX_DOPPLER
    body += """
<p>Sionna 路径对象携带时延、复幅度、出发/到达角和材料交互；CFR 还需要完整 UE 速度向量与
物理采样率。当前适配器按平均 OFDM-symbol 周期设置采样，频率网格围绕载波中心；随后只把
中间 symbol 作为一个 slot snapshot 落盘。若未设置 velocity 或沿用 1 Hz 默认采样，14 个 symbol
会变成静态复制。当前 ChannelSample 只落 CFR，没有持久化 Sionna 的原始 Paths；因此 RT 数据调用
<code>Dataset.paths()</code> 会明确抛 <code>NotImplementedError</code>，而不是套一张 CDL 表伪造角度。
PDP、协方差、PMI 与几何量仍可从现有合同计算。这是当前数据合同的硬边界，不以空数组占位。</p>
<h2>InternalSim 的快速 probe 为什么能快、又为什么不能多算</h2>
""" + F_PROBE_SNR + F_SINR_COMBINE
    body += """
<p><code>scenario.probe()</code> 把正式配置压到最多 24 RB、4 symbol，并关闭 SSB，只返回与频域
小尺度矩阵无关的几何量。SIR、路损、距离、LOS、位置和 Doppler 在同 seed 下保持；SNR 因总功率
分给更少 RB 必须按上式修正，再与 SIR 在线性域重算 SINR/IoT。原始 SNR 若已撞 ±50 dB 夹逼，
对应样本会剔除并报数，不能把统一的 39.5 dB 假平台写进分布。</p>
"""
    body += table(
        ["路径", "可以回答", "不能回答/必须升级"],
        [
            ("InternalSim probe", "覆盖、SIR/IoT、路径损耗、LOS、位置、Doppler 的场景量级", "谱效、吞吐、PDP、宽带预编码、估计 NMSE"),
            ("InternalSim full", "统计 CDL/TDL、多小区、导频估计和完整 H", "不能冒充某栋真实建筑的确定性路径"),
            ("Sionna RT small-N", "真实场景几何与材料下的少量完整样本", "不存在只压 RB 就等价加速的 RT probe"),
            ("Sionna RT full", "路径/CFR、波束和场景级空间结构", "可信度仍受网格、材料、站点与遮挡资产质量限制"),
        ],
    )
    body += callout(
        "danger", "probe 数据默认不落盘是安全设计",
        "<p>它只覆盖 8.64 MHz，并非对 100 MHz 稀疏抽样。即使 shape 看起来像普通数据集，也不能拿去算 "
        "PDP、频选 rank 或吞吐；<code>not_available</code> 是硬边界清单，不是建议项。</p>",
    )
    body += "<p class=source-row>场景资产：" + source_ref("src/superran/scenes.py", "def prepare_scene") + " · 快速探测：" + source_ref("src/superran/scenario.py", "def probe") + " · RT 适配：" + source_ref("src/superran/channelhub.py", "sionna_rt") + "</p>"
    return Page(
        "raytracing", "射线追踪、场景资产与快速探测", "物理内核", "RAY TRACING",
        "Sionna RT 场景如何准备、形成时变 CFR，以及 InternalSim probe 的严格可用边界。", body,
        ("Sionna RT", "Mitsuba", "PLY", "probe", "channel_generation_mode"),
    )


def antenna_page() -> Page:
    body = array_svg()
    body += array_256_svg()
    body += port_contract_svg()
    body += """
<h2>F(192×64) 不是拍脑袋矩阵</h2>
<p>64T 的逻辑端口按 <code>r=p·32+h·4+v</code> 展平，物理阵子按
<code>e=p·96+h·12+(3v+q)</code> 展平；v=0 是顶部。端口 r 只连接
<code>q=0,1,2</code> 三个相邻物理阵子，所以每列恰有三个非零值。</p>
""" + F_FEED + F_COUPLING + F_EFFECTIVE
    body += """
<h2>公司 256T：同一个展平合同，不同的面板与馈电规模</h2>
<p>256T 不是把 64T 的 shape 改成 256；两者端口轴顺序现在相同。256T RF 端口是
16H×8V×2pol，按 <code>r=p·128+h·8+v</code>（0-based）展平。每个 T 在其背后驱动 6 个
垂直物理 AE，因此实际为 16H×48V×2pol=1536 AE，RF 垂直相位中心间距为 6×0.67λ=4.02λ。</p>
""" + F_COUPLING_256
    body += """
<h3>物理意义</h3>
<ul>
  <li><strong>列范数为 1：</strong>一个 RF 端口的单位输入功率被 3 或 6 个阵子重新分配，而不是凭空放大。</li>
  <li><strong>不同列不重叠：</strong>两种固定馈电下每个阵子只属于一个 RF 端口；因此 F 的列天然正交。</li>
  <li><strong>相位随 6° 下倾递进：</strong>子阵阵子相干叠加，使端口方向图主瓣向水平面下方转动；top-to-bottom 只是编号，不能翻转物理下倾。</li>
  <li><strong>快路径可验证：</strong><code>effective_subarray</code> 直接算端口响应；<code>physical_reference</code>
  先生成 192/1536-AE 信道再乘 F。二者必须在数值容差内一致。</li>
</ul>
<h2>阵元方向图</h2>
<p>当前有单元阵子方向图，但它是<strong>可配置的参数化临时模型</strong>，不是公司实测表。
水平 HPBW 110° 来自产品先验；垂直 65°、峰值 8 dBi 与 30 dB 截断当前仍是工程参数。
下图曲线由当前公式和默认参数直接生成，作用是帮助理解，不代表暗室实测。</p>
""" + element_pattern_svg() + F_PATTERN + F_PATTERN_COMBINE + F_JONES
    body += table(
        ["量", "当前默认", "在模型中的作用", "不能误读为"],
        [
            ("φ3dB", "110°", "水平功率增益在 ±55° 约下降 3 dB", "已导入的公司实测 cos 表"),
            ("θ3dB", "65°", "垂直元素包络；与固定子阵因子相乘", "整机端口垂直波宽"),
            ("Gmax", "8 dBi", "由 /20 转成复场幅，再参与每条 ray", "数字 64T/256T 波束增益"),
            ("Am", "30 dB", "参数化前后向衰减截断", "真实 front-to-back ratio"),
            ("ζp", "+45° / −45°", "理想线极化 Jones 基", "方向相关复 Jones 实测值"),
            ("element_xpd_db", "8 dB", "无 profile XPR/统计回退值与 provenance", "已贯通的公司方向相关天线 XPD"),
        ],
    )
    body += callout(
        "danger", "不要把它称为 cos 实测方向图",
        "<p>实现是 3GPP 风格的抛物线 dB 包络，<code>measured_jones</code> 入口目前硬报"
        " <code>NotImplementedError</code>。拿到公司 (az,el,f) 复 Jones 数据后，应新增插值、频率轴、"
        "极化端口校准与 hash，而不是只替换一个 HPBW 数字。</p>",
    )
    body += """
<h3>方向图怎样一步步影响最终信号</h3>
<ol>
  <li><strong>dBi 是功率增益：</strong>复电场幅度必须用 <code>10^(GE/20)</code>；若误用 /10，信道幅度会多平方一次。</li>
  <li><strong>极化方向：</strong>同一个标量包络乘 ±45° Jones 向量；InternalSim 在实现中把标量放在 steering、单位 Jones 放在极化收缩，乘积与公式一致。</li>
  <li><strong>固定馈电：</strong>每个物理 AE 的复场按 <code>wq</code> 相干叠加，形成 1 驱 3/1 驱 6 的下倾、旁瓣和潜在栅瓣，而不是把功率增益简单乘 3/6。</li>
  <li><strong>逐 ray 耦合：</strong>收发 Jones 经 <code>Jℓ</code>/XPR 收缩，并和收发 steering、时延、多普勒一起进入 <code>H(t,f)</code>。</li>
  <li><strong>数字波束：</strong>SVD/PMI/ZF/EBF 权在 RF 端口域作用于 H；它改变多端口相干合成与 MU 干扰，但不能再重复加一次元素/子阵增益。</li>
</ol>
""" + F_SUBARRAY_PATTERN + F_RAY_POLARIZATION
    body += """
<p>参数化阵子增益与固定 1 驱 3/1 驱 6 子阵因子先形成<strong>有效 RF 端口绝对增益</strong>，再进入
conducted-power 链路预算；数字 SVD/PMI/ZF 增益随后在端口域计算。InternalSim 的服务 H 会在
全部 ray 合成后做一次整体小尺度归一化，但保留不同 ray 之间的相对方向权重；绝对端口增益由
long-term link budget 单独带入。旧字段 <code>sector_gain_all_db</code> 为兼容保留，在有效阵列模式下
其物理语义是 element×subarray gain，不只是传统扇区包络。</p>
"""
    body += """
<h2>如何证明 F 正确</h2>
"""
    body += table(
        ["不变量", "期望", "失败意味着"],
        [
            ("shape", "64T=(192,64)；256T=(1536,256)", "阵子/端口索引或极化维错误"),
            ("nnz per column", "64T 每列 3；256T 每列 6", "固定馈电拓扑错误"),
            ("column norm", "每列 ||F[:,r]||₂=1", "端口输入功率不守恒"),
            ("column overlap", "FᴴF=I₆₄ / I₂₅₆", "不同 RF 端口错误共享阵子"),
            ("downtilt peak", "+6° 配置对应主瓣约 −6° elevation", "相位符号/Tx-Rx 共轭错误"),
            ("reference equivalence", "effective 与 192-AE reference 相对误差在容差内", "快路径公式或投影方向错误"),
            ("port permutation", "canonical↔Sionna↔Type-I 往返为 identity", "码本/物理端口错位"),
        ],
    )
    body += "<p class=source-row>项目配置：" + source_ref("src/superran/hardware.py", "COMPANY_RF_PANEL") + " · ChannelHub 物理实现由 <code>msg_embedding/phy_sim/effective_array.py</code> 承担。</p>"
    return Page(
        "antenna", "阵列、双极化与 F 矩阵", "物理内核", "ARRAY & POLARIZATION",
        "从 +45/-45° 阵元到 64T/1 驱 3 与 256T/1 驱 6，再到可验证的 F 矩阵。", body,
        ("F矩阵", "1驱3", "1驱6", "256T", "+45/-45", "方向图", "下倾"),
    )


def srs_page() -> Page:
    body = srs_matrix_svg()
    body += """
<h2>64×4 到底怎么来</h2>
<p>上行 SRS 时 UE 有 4 个发射端口，gNB 有 64 个接收端口。每个 UE 端口使用可分离的
参考信号资源，gNB 在每个接收端口上解扩四路，因此得到 64×4 的上行端口信道估计。
TDD 互易假设下，它经 RF 校准后转置/共轭到下行预编码约定；不是“凭空把 4×64 复制一份”。
当前 SuperRAN 把两个方向都存成 <code>[time,rb,bs_port,ue_port]</code>，因此
<code>h_precoding_est=conj(h_ul_est)</code>。该约定由 SuperRAN 自己版本化；数据源若带
<code>w_dl</code> 也会被忽略，发射权统一由本地 EBF/PEBF/NEBF 重算。</p>
""" + F_SRS_RX + F_LS
    body += callout(
        "danger", "当前观测边界：仍不是物理多端口 Y=HX",
        "<p>底层 <code>srs_sequence()</code> 能生成 1/2/4 端口序列，但 serving pilot 调用当前仍固定 "
        "<code>N_ap=1,n_ap_index=0</code>；观测端再把标量 X 广播到每个 BS×UE 系数。这样可以验证 "
        "LS/频域 LMMSE 数值，却无法真实产生端口 rank 不足、同码污染或空间协方差抑制。下一阶段必须先"
        "构造同一接收向量上的 <code>Y=HX+I+N</code>，再讨论时频空 LMMSE。</p>",
    )
    body += """
<h2>当前是 ZC 吗</h2>
<p>序列生成层是。<code>pilot_type_ul="srs_zc"</code> 走 38.211 SRS 基序列：长度足够时使用 Zadoff–Chu，
短长度走规范短序列；支持 comb、循环移位、group/sequence hopping 和频域 hopping。
“SRS 周期”指发送周期；某个 RBG 距离最近一次有效 SRS 的时间应叫
<strong>CSI 陈旧时长/lag</strong>，不叫“SRS 年龄”。</p>
<p>当前系统老化模型只支持 38.211 Table 6.4.1.4.3-1 中的公司基线：
<code>C_SRS=63/B_SRS=1/b_hop=0/n_RRC=0</code>。它在 SuperRAN 内固化为
<code>0,8,16,7,...,1,9</code> 的 17-hop 镜像序列，不依赖外部 helper，也不提供
恒等扫描兜底。默认公司预设使用
<code>T_SRS=20 slot</code>；在 30 kHz SCS 下 1 slot=0.5 ms，故发送周期为 10 ms，
17 跳的完整宽带采集窗为 170 ms。非 272 RB / 17×16 配置直接报错，
后续获得新带宽的明确资源参数后再扩展。可对照
<a href="https://www.etsi.org/deliver/etsi_ts/138200_138299/138211/18.07.00_60/ts_138211v180700p.pdf" target="_blank" rel="noreferrer">ETSI TS 138 211 V18.7.0 §6.4.1.4.3</a>。</p>
<h2>LS、LMMSE 与方向性</h2>
""" + F_LS + F_LMMSE
    body += table(
        ["模式", "做法", "保留方向性吗", "成本/边界"],
        [
            ("ideal", "直接返回真值", "是", "乐观上界，不是可实现估计"),
            ("ls_linear", "导频点 LS，再做频时线性插值", "是；每个 BS×UE 复系数独立估计", "噪声/干扰直接进入估计，低 SIR 方差大"),
            ("ls_mmse", "从真实、可非均匀的 pilot 位置直接以 R_tp 映射到全部目标 RB", "是；每个 BS×UE 复系数独立估计", "公开配置 canonical；需要 tau_rms/SNR prior；时间轴因无 Doppler prior 仍线性插值"),
            ("ls_lmmse", "与 ls_mmse 完全相同", "同上", "物理命名更精确的兼容 alias，不是另一种算法"),
            ("ls_hop_concat", "跨 hopping occasion 合并部分带估计", "是", "完整带宽获得时间变长，需显式记录 lag"),
        ],
    )
    body += callout(
        "note", "LS 不会让干扰失去方向",
        "<p>在正确的多端口 <code>Y=HX+I+N</code> 中，LS 只是用已知导频解扩；污染项仍携带干扰"
        "信道的 64 维空间向量，所以 LS 本身不会把方向抹掉。当前逐系数观测抽象却不能用来证明"
        "这件事，因为干扰在物理接收合成之前已经被分开。当前 LMMSE prior 主要是频域指数 PDP；"
        "它不等价于完整的时频空 MMSE/IRC。</p>",
    )
    body += callout(
        "warn", "LMMSE 不是每个 realization、每个 SNR 都必胜",
        "<p>匹配先验的低 SNR Monte Carlo 应优于 LS，高 SNR 且 full-pilot 时应退化回 LS；但指数 PDP "
        "若与某个固定 CDL realization 不匹配，单次高 SNR 误差可能高于线性插值。测试因此锁定"
        "统计性质、有限性和高 SNR 极限，不写不成立的逐样本万能不等式。</p>",
    )
    body += """
<h2>周期、报告与处理时延是三件事</h2>
""" + F_SRS_LAG
    body += table(
        ["参数", "当前默认", "物理含义"],
        [
            ("srs_period_ms", "10 ms", "UE 发 SRS 的周期；hopping 时每次只覆盖一部分带宽"),
            ("ChannelHub srs_periodicity", "20 slot", "30 kHz SCS 下等于 10 ms；不要把 slot 数直接写成 ms"),
            ("srs hopping", "on；固定 17-hop", "只支持 100 MHz / 272 RB；C_SRS=63/B_SRS=1/b_hop=0 每次 16 RB"),
            ("csi_processing_delay_ms", "2 ms", "估计到权值可用于调度的处理延迟"),
            ("csi_report_period_ms", "20 ms", "宽带 PMI/CQI 何时更新并保持；不是 5 ms 快照"),
            ("snapshot_ms", "由链路表配置", "物理信道采样间隔，只用于把毫秒 lag 离散成快照索引"),
        ],
    )
    body += callout(
        "warn", "关于 PMI 周期",
        "<p>项目把 CSI report 周期显式独立为 20 ms 工程默认，避免误用 5 ms snapshot。"
        "它不是宣称所有商用网都固定 20 ms；若现场 RRC/日志给出周期，应覆盖并记录。"
        "SRS 周期和 CSI report 周期不可合并成一个旋钮。</p>",
    )
    body += "<p class=source-row>实现入口：" + source_ref("src/superran/physical.py", "def srs_config") + " · " + source_ref("src/superran/csi_aging.py", "class CsiConfig") + "</p>"
    return Page(
        "srs", "SRS、64×4 与信道估计", "物理内核", "SRS & CHANNEL ESTIMATION",
        "ZC 序列、LS/LMMSE、17 跳频、周期/报告/处理时延的清晰边界。", body,
        ("SRS", "64x4", "LS", "LMMSE", "ZC", "PMI周期"),
    )


def reference_signals_page() -> Page:
    body = reference_signal_svg()
    body += """
<h2>physical.py 是一组可复用基线，不是零散 helper</h2>
<p>它把 ChannelHub 已实现的 38.211/38.213/38.214 物理入口整理成稳定接口：标准 RB 配置、
TDD pattern、SRS/SSB/Gold 序列、序列相关、CSI-RS DFT 扫描、干扰投影，以及 ideal/LS/LMMSE
信道估计。这样自研算法可以与生成信道时使用的同一套序列和资源映射比较，而不是在评估脚本中
重写一份“看起来差不多”的参考实现。</p>
<h2>TDD 不只是 D/S/U 三个字母</h2>
""" + F_TDD_FRACTION
    body += """
<p><code>tdd_pattern_info()</code> 返回周期内 D/S/U 时隙和特殊时隙的 DL/UL symbol 数；后端若显式
提供 GP 也一并返回，否则由 14−DL−UL 推出 guard symbols。
系统层的可用下行 RE、PRB utilization 与 TDD 归一都应从它派生。项目某些历史推导用 0.7 折算
S slot，那只是特定 pattern 的工程值；只要特殊时隙配置可读，就不应把 0.7 扩写成 NR 常数。</p>
<h2>序列、资源图案和估计器是三层</h2>
""" + F_SEQUENCE_CORR + F_SRS_RX + F_LS
    body += table(
        ["对象", "当前入口", "物理作用"],
        [
            ("SSB", "ssb_sequences(pci)", "PSS/SSS 完成小区搜索与同步，PBCH-DMRS支撑广播信道估计"),
            ("Gold", "gold_sequence(c_init,length)", "CSI-RS、DMRS 与 PDSCH 加扰的伪随机基础序列"),
            ("SRS", "srs_config / srs_sequence", "低 PAPR 基序列、comb、端口、周期和跳频共同决定上行观测"),
            ("相关性", "sequence_correlation", "把端口/小区序列正交性与污染风险变成可测量基线"),
            ("估计", "estimate_channel", "同一 noisy pilot observation 上比较 ideal、LS+linear 与频域 LMMSE"),
        ],
    )
    body += """
<h2>CSI-RS DFT 扫描与 PMI 必须分开</h2>
""" + F_BEAM_SELECT
    body += """
<p><code>dft_codebook()</code> 返回 <code>[beam,port]</code> 的 CSI-RS 扫描波束，并按当前
<code>pol_h_v + top_to_bottom</code> 端口合同置换；<code>select_beam()</code> 选最大接收功率索引。
PMI 页的 Type-I-style 集合是 <code>[port,column]</code>，还包含过采样、双极化共相与多层选择。
二者都使用 DFT 结构，不代表它们是同一个码本或同一个反馈过程。</p>
"""
    body += callout(
        "warn", "干扰投影能力存在，但当前数据源限制必须一起说",
        "<p><code>project_interference()</code> 可把邻区信道投到邻区自己的 SVD/DFT 服务子空间；但当前 "
        "ChannelHub 保存的单邻区干扰信道近似秩 1，<code>precoded</code> 与 <code>isotropic</code> 可能逐位相同。"
        "接口存在不等于当前数据能支撑波束化干扰增益结论。</p>",
    )
    body += "<p class=source-row>物理基线：" + source_ref("src/superran/physical.py", "def nr_rb_count") + " · 端口映射：" + source_ref("src/superran/hardware.py", "def type1_to_port_permutation") + "</p>"
    return Page(
        "referencesignals", "参考信号、TDD 与波束扫描", "物理内核", "REFERENCE SIGNALS",
        "NR 帧资源、SSB/Gold/SRS 序列、DFT 扫描和估计基线如何在同一物理合同下协作。", body,
        ("SSB", "Gold", "TDD", "CSI-RS", "DFT codebook", "sequence correlation"),
    )


def measurements_page(modules: list[ModuleDoc]) -> Page:
    measure = next(m for m in modules if m.name == "measure")
    funcs = [s for s in measure.symbols if s.kind == "function"]
    rows = []
    for item in funcs:
        rows.append((
            f"<code>{esc(item.name)}</code>", item.doc,
            source_ref(measure.rel, f"def {item.name}", f"L{item.line}"),
        ))
    body = """
<h2>落盘合同</h2>
""" + F_DATASET
    body += """
<p>每个数据集包含复信道、summary/config、位置、几何 SINR/SIR 与可选干扰/预编码量。
所有生成样本在落盘前必须满足 h_true/h_est 同形、有限、角色明确；summary 记录数据源、
阵列、标准表版本、随机种子与近似边界。</p>
<h2>观察量计算入口</h2>
"""
    body += table(["API", "用途", "源码"], rows, raw={0, 2})
    body += """
<h2>12 类 MCP 观察量与底层函数</h2>
<p>MCP 对外把细粒度函数组合为 12 类可发现观察量：PDP/时延、空间协方差与特征谱、
条件数、信道增益/RSRP、SRS 特征、PMI、几何/干扰、预编码谱效、链路吞吐、系统 KPI 等。
数量以 <code>sr_capabilities</code> 返回的 catalog 为准；底层函数表保留更细的开发入口。</p>
"""
    body += callout(
        "warn", "绝对量与归一量不要混",
        "<p><code>channel_gain_db</code>、绝对 RSRP、几何 SINR/SIR 和归一化协方差有不同的功率参考。"
        "做算法 A/B 时必须锁定相同的噪声、功率和归一化；不能拿归一化 H 的“增益”解释覆盖。</p>",
    )
    body += """
<h2>toy：从一个样本取可复核链路量</h2>
""" + code(r'''from superran.loader import load

ds = load("ds_xxxxxxxx")
H = ds.h_true[0]                 # [T, RB, BS_ant, UE_ant]
Hhat = ds.h_est[0]               # 同形，预编码侧可见 CSI
print(ds.nmse_db())
print(ds.pdp(0))
print(ds.pmi(0, max_rank=4))
print(ds.link_performance(0, h_for_precoding=Hhat))
''')
    return Page(
        "measurements", "数据集与观察量", "物理内核", "DATA CONTRACT",
        "h_true/h_est 的形状、角色与 measure/loader 全部公开入口。", body,
        ("Dataset", "PDP", "PMI", "RSRP", "NMSE"),
    )


def pdp_page() -> Page:
    body = pdp_pipeline_svg()
    body += """
<h2>PDP 回答什么，不能回答什么</h2>
<p>功率时延谱（Power Delay Profile）把频率选择性信道映射到时延域，回答能量集中在哪些
相对时延、均值时延和 RMS 时延扩展是多少。它是相干带宽、导频间隔和 LMMSE 频域先验的物理
依据；但仅凭 RB 中心频点不能恢复 RB 内部的细时延结构，也不能把超出无模糊周期的绝对时延唯一解开。</p>
<p><code>Dataset.pdp()</code> 是从落盘 <code>h_true</code> 惰性派生的观察量，不另存一份可能漂移的
PDP 数组。默认对 T、BS 端口和 UE 端口取平均；<code>per_antenna=True</code> 时保留 BS 端口轴，
输出 power shape 由 <code>[RB]</code> 变为 <code>[RB,BS]</code>。</p>
<h2>从 H[k] 到 P[ℓ]</h2>
""" + F_PDP_IFFT
    body += """
<p>实现先给频域样本施加均方值归一的 Hann 窗，再做带 <code>√N_RB</code> 的 IFFT，使变换采用
Parseval 能量口径。窗会随 realization 改变总能量，所以代码不是只在总体上乘一个经验常数，
而是对每个 <code>[T,BS,UE]</code> realization 恢复到原频域能量。</p>
<h2>分辨率与无模糊周期由采样网格决定</h2>
""" + F_PDP_AXIS
    body += """
<div class="toy"><div><b>100 MHz · 30 kHz SCS · 272 RB</b>
<p>RB 中心间隔为 12×30 kHz=360 kHz；时延分辨率约 10.21 ns，无模糊周期约 2.778 μs。</p></div>
<div><b>为什么不能靠 zero padding“提高精度”</b>
<p>补零只把同一个周期核插值得更密，图会更平滑，却没有新增频域观测；物理分辨率仍由 97.92 MHz
观测跨度决定。</p></div></div>
"""
    body += callout(
        "warn", "RB 中心采样的硬边界",
        "<p>无模糊周期只由相邻 RB 中心间隔决定，与 RB 数无关；RB 数增加会改善分辨率，却不扩大周期。"
        "当前 Gate 仅在剖面支持落入半窗约 1.389 μs 时对 RMS DS 做数值判定，超界样本标为不可可靠反演，"
        "不会硬给一个看似精确的结论。</p>",
    )
    body += """
<h2>为什么要做圆周解绕与窗核去嵌</h2>
""" + F_PDP_MOMENT
    body += """
<p>IFFT 的时延轴是圆周而不是无限直线。固定把后半轴解释成大正时延，会把靠近 0 的单径旁瓣绕到
周期末端并制造数百纳秒的假 RMS DS。实现先用功率加权圆周均值选择数据自己的分支，再把每个 tap
包回均值附近；最后扣除 Hann 仪器核的二阶矩 <code>σw²</code> 并在 0 处截断，避免单径被窗本身
制造出约数纳秒的扩展。</p>
<h2>返回字段与验证合同</h2>
"""
    body += table(
        ["字段", "单位/shape", "怎样解释"],
        [
            ("power / power_db", "[RB] 或 [RB,BS]；线性 / dB", "未做峰值归一；保留绝对相对能量，dB 仅为显示变换"),
            ("delays_s", "[RB]；秒", "0 到 Tamb−Δτ 的周期时延网格"),
            ("mean_delay_s", "标量；秒", "功率加权圆周均值选择的时延分支"),
            ("rms_delay_spread_s", "标量；秒", "圆周局部二阶矩减去 Hann 核方差后的结果"),
            ("delay_resolution_s", "标量；秒", "由观测频宽决定的物理分辨率，不是绘图采样步长"),
            ("unambiguous_period_s", "标量；秒", "由相邻 RB 中心间隔决定的周期"),
            ("power_conservation_ratio", "无量纲", "时延域总功率 / 原频域平均总功率；应接近 1"),
        ],
    )
    body += code(r'''from superran.loader import load

ds = load("ds_xxxxxxxx")
pdp = ds.pdp(0)
print(pdp.power.shape, pdp.delay_resolution_s * 1e9)
print(pdp.rms_delay_spread_s * 1e9, pdp.power_conservation_ratio)

# 端口级诊断：power 变为 [RB, BS]
pdp_port = ds.pdp(0, per_antenna=True)
assert pdp_port.power.shape[1] == ds.h_true.shape[-2]
''')
    body += callout(
        "good", "三类反例共同锁住实现",
        "<p>解析单径检查不制造时延扩展与负长时延；两径 0/500 ns、功率 0.8/0.2 检查 100 ns 均值和"
        "200 ns RMS；Parseval 与 <code>power_conservation_ratio</code> 检查窗前后能量没有丢失。</p>",
    )
    body += "<p class=source-row>核心：" + source_ref("src/superran/measure.py", "def power_delay_profile") + " · 惰性入口：" + source_ref("src/superran/loader.py", "def pdp") + " · Gate：" + source_ref("src/superran/validate.py", "def check_delay_spread_vs_profile") + "</p>"
    return Page(
        "pdp", "PDP、时延域与可分辨边界", "物理内核", "POWER DELAY PROFILE",
        "从频域 H 到未归一 PDP、圆周时延矩、分辨率与无模糊边界。", body,
        ("PDP", "RMS delay spread", "Hann", "Parseval", "时延分辨率"),
    )


def csi_page() -> Page:
    body = csi_lifecycle_svg()
    body += """
<h2>五个时间量不能再混为一个“周期”</h2>
""" + F_CSI_SWEEP
    body += table(
        ["时间量", "当前工程基线", "角色"],
        [
            ("channel snapshot", "例如 5 ms", "物理 H(t) 的采样间隔；只负责离散时间，不触发报告"),
            ("SRS period", "10 ms", "UE 发 SRS 的周期；项目不使用“SRS 年龄”这个说法"),
            ("SRS full sweep", "17×10=170 ms", "跳频覆盖 17 RBG 的完整采集窗"),
            ("processing delay", "2 ms", "估计、算权到调度可用的固定时延"),
            ("PMI/CQI report period", "20 ms，可扫 5/10/20/40/80", "报告到达后更新；不是 universal 5 ms"),
            ("CSI staleness", "逐 RBG、逐时刻", "当前发送所用 CSI 距其测量时刻的陈旧时长"),
        ],
    )
    body += """
<p>默认 17 跳时，平均陈旧时长是 <code>Hhop·TSRS/2 + Dproc = 87 ms</code>；这是时间轮转的全带
平均，不意味着某几个 RBG 永远更旧。<code>rbg_csi_staleness_ms()</code> 用 SuperRAN 本地版本化的
38.211 C_SRS=63/B_SRS=1 17-hop 次序得到每个时刻每个 RBG 的 staleness；当前不支持其他带宽，
也没有外部 helper 或 identity fallback。连续时延换 snapshot lag 时必须向上取整，防止偷用尚未完成处理的 CSI。</p>
<h2>报告到达后只能因果保持</h2>
""" + F_CSI_REPORT_HOLD
    body += """
<p>系统链路表只在 report instant 更新宽带 PMI/CQI，中间 TTI 复用最后一份已到达报告。
CQI 当前使用报告时刻 PMI-SINR 的因果 expanding mean；代码没有虚构现场 IIR 系数。配置中的额外
feedback latency 尚未单独建模，报告在其 report snapshot 到达；这项边界会写进 <code>CsiConfig.as_dict()</code>。</p>
<h2>PMI 当前究竟是什么</h2>
<p><code>pmi_type_i()</code> 在单面板 Type-I-style DFT 列集合上，用宽带发射协方差
<code>Rtx=E[HHᴴ]</code> 逐层贪心选择列，并在每次选择后投影掉已选方向。它会按 metadata 的
<code>port_order</code> 与垂直顺序，把协议 p/v/h 码本行重排到真实 64T/256T 信道端口顺序。</p>
"""
    body += callout(
        "warn", "Type-I-style 不等于完整 38.214 Type-I",
        "<p>当前实现不是完整枚举多层、子带、panel restriction 与反馈比特的标准码本。"
        "离线逐 snapshot 最佳码字也是候选上界；只有经过 report 周期、处理时延和保持逻辑的 PMI，"
        "才是系统仿真中 gNB 当时可用的 PMI。</p>",
    )
    body += """
<h2>老化不是给 SINR 减一个经验 dB</h2>
""" + F_CSI_AGING_SINR
    body += """
<p>预编码器在旧的 <code>h_prec</code> 上算 W，真实传输在当前 <code>h_true</code> 上经 post-MMSE
重新计算逐流 SINR。零时延时两者相同，结果必须逐位退化回原 SU rank-adaptation；有老化时，
错位会自然表现为 BF gain 下降和流间泄漏。MU 的 ZF 零陷同样在当前真值上复评，因此不会用一个
固定“老化损失”掩盖用户、速度和 RBG 的差异。</p>
"""
    body += steps((
        ("生成 SRS 覆盖", "<p>按 SRS 周期与标准 hopping order 标出每个 RBG 最近一次采样时刻。</p>"),
        ("加入处理时延", "<p>把估计/算权时延加入 staleness，并向上离散成 snapshot lag。</p>"),
        ("产生候选 PMI/CQI", "<p>在可见的估计信道上做 Type-I-style PMI 与宽带测量。</p>"),
        ("按报告周期提交", "<p>只有 report instant 把新值放入系统状态；其他时刻 hold previous。</p>"),
        ("真值复评", "<p>实际 H、邻区协方差与接收机共同得到发送结果和 BLER 输入。</p>"),
    ))
    body += "<p class=source-row>时间链：" + source_ref("src/superran/csi_aging.py", "class CsiConfig") + " · PMI：" + source_ref("src/superran/measure.py", "def pmi_type_i") + " · 系统因果表：" + source_ref("src/superran/system.py", "csi_report_source_snapshot") + "</p>"
    return Page(
        "csi", "CSI 报告、时序与老化", "物理内核", "CSI LIFECYCLE",
        "把 SRS 周期、跳频采集、处理时延、PMI/CQI 报告和 snapshot 分开建模。", body,
        ("CSI staleness", "PMI", "CQI", "report period", "causality", "Type-I-style"),
    )


def pmi_page() -> Page:
    body = pmi_pipeline_svg()
    body += """
<h2>PMI 在 SuperRAN 里有三个不同身份</h2>
"""
    body += table(
        ["入口/状态", "使用哪份 H", "结果角色", "不能怎样解读"],
        [
            ("Dataset.pmi()", "离线 h_true", "码本/端口合同诊断与候选上界", "不是当时 gNB 已收到的反馈"),
            ("系统 PMI report", "报告源时刻的 h_prec", "宽带 CQI 参照权，按周期更新并 hold", "不是每个 snapshot 的 oracle"),
            ("precoder=type1", "同一 h_prec", "把 PMI 权本身当实际发射权", "此时 BF Gain 按定义为 0，不代表没有阵列增益"),
        ],
    )
    body += """
<h2>候选列：二维过采样 DFT + 双极化共相</h2>
""" + F_TYPE1_COLUMN
    body += """
<p><code>type_i_codebook()</code> 的列按协议 <code>p/v/h</code> 逻辑生成，随后由
<code>type1_to_port_permutation()</code> 重排到数据集声明的真实 RF-port 顺序。新 64T 与 256T 都是
<code>pol_h_v + top_to_bottom</code>；旧 64T 只有在显式兼容边界才使用
<code>h_v_pol + bottom_to_top</code>。置换的是 W 的端口行，不是把 H 随意 reshape。</p>
"""
    body += table(
        ["阵列", "逻辑布局", "默认过采样", "双极化候选列数", "真实端口输出"],
        [
            ("公司 64T", "8H×4V×2pol", "O_H=O_V=4", "4×32×16 = 2,048", "W[64, rank]"),
            ("公司 256T", "16H×8V×2pol", "O_H=O_V=4", "4×64×32 = 8,192", "W[256, rank]"),
        ],
    )
    body += """
<h2>宽带搜索：先积功率，再逐层扣方向</h2>
""" + F_PMI_COVARIANCE + F_PMI_GREEDY
    body += """
<p>这里的“宽带”表示一个报告在全部 RB 上共享同一组码本列。它不是把复信道 H 先平均：后者会让
相差 π 的两个快照互相抵消。多层采用残余协方差上的增量贪心，仅保证不重复同一方向；候选列组合
没有经过完整 38.214 多层矩阵码本、subset restriction、子带 PMI 或反馈比特打包。</p>
<h2>RI、PMIResult.rank 与系统 rank 是三件事</h2>
""" + F_RANK
    body += """
<p><code>pmi_type_i(max_rank=4)</code> 返回的 <code>PMIResult.rank</code> 是实际选出的贪心列数，通常就是
可用维数与上限的较小者；它<strong>不是完整标准 RI 决策</strong>。<code>compute_precoder(type1)</code> 可先用
协方差特征值门限得到工程 RI；体验系统则保留 rank 1…4 候选，用 gNB 可见 CSI 上的可达 SE 选择
<code>rank_gnb</code>。报告必须写明是哪一种 rank 来源。</p>
<h2>PMI 为什么同时出现在 CQI 与 BF Gain 中</h2>
""" + F_PMI_REFERENCE
    body += """
<p>终端侧在当前真值上用所持 PMI 权测 <code>pmi_sinr_db</code> 并形成宽带 CQI；基站侧在自己可见的
陈旧 CSI 上比较实际发射权和同 rank PMI 权，得到额外 BF Gain。两条链必须使用相同 rank、功率约束、
噪声/干扰和接收机。实际发射为 Type-I 时两权相同，所以额外 BF Gain 为 0；PMI 权本身产生的阵列增益
已经在 CQI 参照 SINR 里，不能再加一遍。</p>
"""
    body += callout(
        "warn", "CSI-RS DFT 波束码本不等于 PMI Type-I-style 列集合",
        "<p><code>physical.dft_codebook()</code> 复用 ChannelHub 的 CSI-RS beam-sweep 码本，shape 是"
        " <code>[beam,port]</code>；<code>type_i_codebook()</code> 是带过采样和四种极化共相的 PMI 候选列，"
        "shape 是 <code>[port,column]</code>。两者都含 DFT 方向、都需要端口置换，但大小、方向和使用阶段不同。</p>",
    )
    body += code(r'''# 离线诊断：这不是系统报告时序
p = ds.pmi(0, max_rank=2)
assert p.precoder.shape == (ds.h_true.shape[-2], p.rank)
print(p.indices, p.layout, p.port_order, p.codebook_size)

# 系统链路表：报告源 snapshot、CQI 与 PMI-SINR 一起审计
table = build_link_tables(..., precoder="svd", csi=csi_cfg)[0]
print(table.csi_report_source_snapshot)
print(table.pmi_sinr_db, table.cqi_index_per_snapshot, table.bf_gain_db)
''')
    body += "<p class=source-row>列集合与搜索：" + source_ref("src/superran/measure.py", "def type_i_codebook") + " · 端口置换：" + source_ref("src/superran/hardware.py", "def type1_to_port_permutation") + " · 系统参照链：" + source_ref("src/superran/system.py", "w_pmi_s = _type1_precoder") + "</p>"
    return Page(
        "pmi", "PMI、Type-I 码本与反馈链", "物理内核", "PRECODING MATRIX INDICATOR",
        "从双极化码本列、端口置换和宽带搜索，一路追到 RI、CQI、BF Gain 与报告保持。", body,
        ("PMI", "Type-I", "RI", "codebook", "CQI", "BF Gain", "port order"),
    )


def robust_page() -> Page:
    body = robust_weight_svg()
    body += """
<h2>“鲁棒权”在项目里特指什么</h2>
<p>当前鲁棒权是<strong>带 CSI 误差协方差加载的 RZF</strong>：它改变的是用估计信道求逆时的
Gram 矩阵，不是 EBF/PEBF/NEBF 的功率归一。后者约束物理发射矩阵 Q 的总功率或每天线功率；
前者承认 <code>Ĥ</code> 不确定，避免把不可靠的零陷打得过深。两条轴可以任意组合，结果必须分别回传
regularization 与 power diagnostics。</p>
<h2>误差模型与单位</h2>
""" + F_CSI_ERROR_MODEL + F_CSI_ERROR_VARIANCE
    body += """
<p><code>mu_csi_error_variance</code> 表示<strong>每个复信道系数</strong>的线性误差功率，必须与送入
预编码器的 H 使用同一归一化、同一端口/频率粒度。离线可用 h_true/h_est 对账估计它；在线算法不能
逐 snapshot 偷看 h_true，应从估计器后验协方差、SRS SNR/干扰模型或独立标定表取得。</p>
"""
    body += callout(
        "danger", "NMSE dB 不能直接塞进 sigma_e²",
        "<p>NMSE 是误差能量相对真信道能量的比值，且常以 dB 表示；"
        "<code>csi_error_variance</code> 是当前 H 归一化下的线性每系数方差。必须先恢复线性量并对齐"
        "信道功率参考，否则加载会差几个数量级。</p>",
    )
    body += """
<h2>noise loading 与 uncertainty loading 相加</h2>
""" + F_ROBUST_RZF
    body += """
<p>常规 RZF 的噪声项为 <code>Ns·σn²/P</code>；独立同分布误差模型给出
<code>NBS·σe²</code> 的不确定性项。代码允许 <code>alpha</code> 覆盖前一项，但声明的误差项仍会加入。
当 <code>σe²=0</code> 时必须与历史 RZF 逐位兼容；当 <code>λ→0</code> 时趋近 ZF，加载增大时方向逐渐
趋向 MRT 式保守解。</p>
"""
    body += table(
        ["选择", "改变什么", "保持什么", "当前配置入口"],
        [
            ("ZF", "不加对角加载，追求估计信道上的零干扰", "总功率/每天线约束另选", "mu_precoder='zf'"),
            ("RZF", "加入噪声 loading", "同上", "mu_precoder='rzf', sigma_e²=0"),
            ("robust RZF", "再加入 CSI-error loading", "同上", "mu_precoder='rzf', mu_csi_error_variance>0"),
            ("EBF / PEBF / NEBF", "对物理 Q 做总功率/每天线处理", "不会替代 CSI 鲁棒化", "power_constraint"),
        ],
    )
    body += """
<h2>在 SU/MU 系统链中怎样落地</h2>
<p>鲁棒加载进入 <code>mu_precoder()</code>，随后物理 Q 才施加 EBF/PEBF/NEBF。Phase A 的 MU pair
表在同一 <code>csi_error_variance</code> 下分别用 h_prec 预测、用 h_true 复评，并保存每个 snapshot 的
<code>noise_loading</code>、<code>csi_error_loading</code>、总 loading、残留 leakage 和每天线诊断。
Phase B 只查这张表，不在 TTI 循环中重新估计误差。</p>
"""
    body += code(r'''from superran.mumimo import robust_rzf_regularization

reg = robust_rzf_regularization(
    n_stream=4, n_bs=64,
    mean_noise_power=0.01, total_power=1.0,
    csi_error_variance=1e-3,
)
print(reg.as_dict())
# noise_loading = 0.04
# csi_error_loading = 0.064
# total_loading = 0.104
''')
    body += callout(
        "warn", "当前可用边界",
        "<p>现在只有全局标量 <code>mu_csi_error_variance</code>，不区分 UE、RBG、snapshot、空间方向或 pair。"
        "它适合先验证鲁棒化机制与敏感性，不应冒充从在线 LMMSE 后验协方差逐资源自适应得到的最优鲁棒权。"
        "默认 <code>mu_precoder='zf'</code> 且方差为 0，因此不开 RZF 时这个参数不会产生效果。</p>",
    )
    body += "<p class=source-row>加载：" + source_ref("src/superran/mumimo.py", "def robust_rzf_regularization") + " · 预编码：" + source_ref("src/superran/mumimo.py", "def mu_precoder") + " · 系统配置：" + source_ref("src/superran/system.py", "mu_csi_error_variance: float") + "</p>"
    return Page(
        "robust", "鲁棒预编码与 CSI 不确定性", "链路算法", "ROBUST PRECODING",
        "把 CSI 误差写进 RZF 对角加载，并与 EBF/PEBF/NEBF 功率约束正交组合。", body,
        ("robust RZF", "CSI error", "diagonal loading", "ZF", "RZF", "sigma_e²"),
    )


def calibration_page() -> Page:
    body = calibration_stack_svg()
    body += """
<h2>校准、验证和算法统计不是一回事</h2>
<p><code>calibration.py</code> 按 3GPP TR 38.901 §7.8 规定的定义把校准量算出来；
<code>validate.py</code> 用项目不变量和可获得的标准表判断数据是否物理合理；Gate 2/3 再判断算法 A/B
差异是否统计成立、可否发布。没有参考曲线时，校准层只报分位点和适用性，绝不自造“通过阈值”。</p>
<h2>耦合损耗把多条物理链串在一起</h2>
""" + F_CAL_COUPLING
    body += """
<p>耦合损耗同时受路损、收发天线方向图、下倾和 serving-cell selection 影响，因此对整体平移错误很
敏感。geometry 则分别给含噪声 SINR 与不含噪声 SIR；多小区中若 SINR 与纯热噪声 SNR 逐点相同，
或 SIR 恒为 ChannelHub 兜底哨兵 49.9 dB，指标会标为不适用并说明干扰没有真正进入。</p>
<h2>角度扩展必须用圆周定义</h2>
""" + F_CAL_ANGLE
    body += """
<p>0° 与 359° 方向实际很近，普通标准差却会把它们拉到两端。圆周角扩展先把每条径映射到单位圆，
用路径功率做复矢量平均，再由合矢量模长恢复扩展；ASD/ASA/ZSD/ZSA 都走同一口径。</p>
<h2>PRB 奇异值为什么用 10log10(λ)</h2>
""" + F_CAL_SINGULAR
    body += """
<p>标准口径在 <code>t=0</code> 对每个 RB 的 <code>R=HᴴH</code> 取特征值。λ 已经是奇异值的平方，
因此画功率 dB 用 <code>10log10(λ)</code>，不能再用 <code>20log10(λ)</code>。绝对曲线需要把落盘
归一化 H 的耦合损耗折回去；最大/次大特征值之比对整体尺度不敏感，是更稳的空间秩校准量。</p>
"""
    body += table(
        ["38.901 校准量", "实现输出", "适用性边界"],
        [
            ("Coupling loss", "p5/p10/p50/p90/p95", "需要 tx/rx 绝对功率同参考面"),
            ("Geometry with/without noise", "SINR / SIR 分位点", "单小区 SIR 无定义；兜底哨兵会被识别"),
            ("Delay spread", "逐样本 DS 分位点", "固定 CDL/TDL profile 的 CDF 退化，不与系统级随机 DS 曲线硬比"),
            ("ASD/ASA/ZSD/ZSA", "按路径功率的圆周扩展", "RT 无 CDL 路径角结构时如实标不适用"),
            ("PRB λ1/λ2/ratio", "三条 dB CDF", "绝对量需耦合损耗；ratio 可消去尺度"),
        ],
    )
    body += """
<h2>跨引擎对标如何避免“显著但不重要”</h2>
<p><code>cross_engine_compare()</code> 对 internal_sim、Sionna/QuaDRiGa 等独立来源的同配置结果计算
两样本 KS 距离与 5% 临界值，同时报告中位数差。样本很大时极小差异也可能统计显著，所以 D、样本量、
中位数差和工程容差必须一起解释，不能只给一个 p-value 式结论。</p>
"""
    body += code(r'''from superran.loader import load
from superran.calibration import calibration_report, cross_engine_compare

ds = load("ds_internal")
report = calibration_report(ds, max_samples=200)
print(report.text())

# 若有同配置独立引擎数据：
other = load("ds_reference_engine")
print(cross_engine_compare(ds, other))
''')
    body += callout(
        "danger", "参考曲线缺失时不自动判通过",
        "<p>项目记录应对照 R1-165974、R1-165975 或 R1-1909704，但并未捆绑这些会议文稿的数字化"
        "参考 CDF。校准报告能证明计算口径与数据可追溯，不能单独证明当前引擎已经通过 3GPP 校准；"
        "正式结论仍需参考曲线或独立已校准引擎。</p>",
    )
    body += "<p class=source-row>校准汇总：" + source_ref("src/superran/calibration.py", "def calibration_report") + " · 跨引擎：" + source_ref("src/superran/calibration.py", "def cross_engine_compare") + " · MCP：" + source_ref("src/superran/server.py", "def sr_calibrate") + "</p>"
    return Page(
        "calibration", "3GPP 校准与跨引擎对标", "可信度", "MODEL CALIBRATION",
        "按 38.901 §7.8 口径计算 CL、geometry、DS/AS 与 PRB 奇异值，并明确适用性。", body,
        ("38.901", "calibration", "coupling loss", "angle spread", "singular value", "KS"),
    )


def beamforming_page() -> Page:
    body = power_constraints_svg()
    body += """
<h2>先固定矩阵约定</h2>
<p>项目统一使用 <code>Q[frequency, antenna, stream]</code>。因此第 m 根天线的功率是
<code>||Q[:,m,:]||²</code>（二维时是 Q 的<strong>行</strong>范数平方）。现场文档若把权写成
<code>[stream, antenna]</code>，它所说的“列归一”在本项目里就是“天线行归一”。</p>
<h2>三种功率约束</h2>
""" + F_EBF + F_PEBF + F_NEBF
    body += table(
        ["模式", "约束与缩放", "总功率", "流间几何", "典型表现"],
        [
            ("EBF", "SVD/码本方向按流等分 P", "通常用满", "保持", "总功率基线；可能有单天线超过 P/M"),
            ("PEBF", "由峰值天线决定一个全局 α", "通常用不满", "保持", "满足每天线功率且不破坏 ZF 零陷"),
            ("NEBF", "每根非零天线分别拉到 P/M", "用满", "可能破坏", "SU 常接近 EBF；强相关 MU 可低于 PEBF"),
        ],
    )
    body += """
<h2>为什么 SU 中 NEBF ≈ EBF，而 MU 中可能 NEBF &lt; PEBF</h2>
<p>SU 只有本用户流间干扰，接收侧 MMSE 还有自由度；逐天线重标通常主要改变阵列幅度，且把
功率用满，所以 NEBF 常接近总功率 EBF并明显优于被峰值天线卡住的 PEBF。MU ZF/RZF 的关键
是不同用户波束的精确相消。NEBF 对每根天线使用不同缩放，相当于左乘一个非标量对角矩阵，
原来的 <code>H_i W_j=0</code> 一般不再成立；高 SNR/强相关/单接收天线时残余干扰可压过功率收益。</p>
"""
    body += callout(
        "good", "反向哨兵",
        "<p>测试同时固定两个确定性例子：64T SU 中 <code>|SE_NEBF/SE_EBF−1|&lt;5%</code> 且"
        " NEBF&gt;PEBF；强相关 MU 中 NEBF 产生可测残余干扰并出现 NEBF&lt;PEBF。只测前者不足以证明"
        "每天线实现正确。</p>",
    )
    body += """
<h2>实现为什么返回三件东西</h2>
<p><code>equal_power_weights</code> 返回物理 Q、兼容旧 SINR 公式的 W_model、以及
<code>PowerDiagnostics</code>。诊断包含逐天线功率、总功率利用率、最大越界和 Gram 非对角能量。
这避免“函数名叫 NEBF”被误当作约束已经满足，也避免 PEBF/NEBF 被二次归一。</p>
""" + code(r'''from superran.beamforming import equal_power_weights

Q, W_model, diag = equal_power_weights(W_svd, mode="nebf", total_power=1.0)
assert diag.max_per_antenna_violation == 0
print(diag.as_dict()["utilization_mean"])
print(diag.as_dict()["orthogonality_error_mean"])
''')
    body += "<p class=source-row>实现：" + source_ref("src/superran/beamforming.py", "def constrain_physical_matrix") + " · 哨兵：" + source_ref("tests/test_physics_invariants.py", "64T SU") + "</p>"
    return Page(
        "beamforming", "预编码与每天线功率约束", "链路算法", "BEAMFORMING",
        "EBF、PEBF、NEBF 的矩阵约定、物理取舍与 SU/MU 反向验证。", body,
        ("EBF", "PEBF", "NEBF", "每天线功率", "SVD"),
    )


def powercontrol_page() -> Page:
    body = power_dof_svg()
    body += """
<h2>先拆开四个常被混称为“功控”的自由度</h2>
"""
    body += table(
        ["自由度", "变量/接口", "守恒或约束", "体验系统当前状态"],
        [
            ("空间每天线", "power_constraint = EBF/PEBF/NEBF", "总功率 P 或每天线 P/M", "SU/MU 均已进入真实 Q 与 SINR"),
            ("流间功率", "power_allocation = equal/waterfilling", "每 RB 的 Σp_l=P", "MU 链路库可选；体验 pair table 目前固定 equal"),
            ("频域功率", "q[cell,RB] / rb_power_overrides", "每小区 Σq=N_RB，且 0.1…4x", "MCP/UI 可配置，默认关闭即全 1x"),
            ("邻区活动", "neighbor_prb_util = η", "0…1 的活动比例", "默认 0.3；它是干扰负载，不是本小区结果 KPI"),
        ],
    )
    body += """
<p>预编码方向（SVD/Type-I、ZF/RZF）还在这些轴之前，它决定往哪里打，但不应该悄悄决定给多少功率。
只有把方向 W、流功率 p、空间约束与 RB profile 分开，才能知道性能变化到底来自方向、功率利用率还是频域重排。</p>
<h2>空间矩阵与 RB profile 如何组合</h2>
""" + F_POWER_COMPOSITION
    body += """
<p>代码等效地把下式作用到逐 RB 信号与干扰参考面，并保留到 post-MMSE 后再聚合 RBG。EBF/NEBF 的
基线每 RB 使用完整预算时，q 的均值 1 直接保证全带预算不变；PEBF 可能因峰值天线约束本来就没有用满，
因此 q 守恒的是<strong>预算包络</strong>，实际辐射功率仍可能低于包络，不能把 profile 均值 1 写成 PEBF 必然用满。</p>
<h2>用户能配置到什么粒度</h2>
""" + F_RB_POWER_CONSTRAINT + F_RB_AUTOBALANCE
    body += """
<p><code>RbPowerOverride</code> 支持一个 RB 或闭区间，作用于所有小区或指定
<code>cell_index</code>。未指定 RB 取唯一的等倍率补偿值；用户指定值原样保留。全部 RB 都指定时总和必须
恰好等于 RB 数。全局与该小区局部规则若重叠也会硬失败，而不是猜谁优先。该接口目前表达“给定 profile”，
尚未内置一个根据队列、边缘用户或跨小区价格自动优化 q 的闭环功控算法。</p>
""" + code(r'''from superran.power_control import RbPowerControlConfig

power = RbPowerControlConfig.from_raw(
    enabled=True,
    num_rb=272,
    overrides=[
        {"cell_index": 0, "rb_start": 0, "rb_end": 15, "multiplier": 2.0},
        {"cell_index": 1, "rb_start": 0, "rb_end": 15, "multiplier": 0.5},
    ],
)
q = power.resolve_profiles(num_cells=21)  # [cell,RB]
assert (abs(q.sum(axis=1) - 272) < 1e-12).all()
''')
    body += """
<h2>每个邻区必须独立进入分母</h2>
""" + F_RB_COUPLING
    body += """
<p><code>q[c,r]</code> 同时改变小区 c 对自己 UE 的目标信号和它对其他小区 UE 的干扰。由于每个邻区可以
选不同 profile，聚合 SIR 不能反推出 <code>Σq_kI_k</code>；数据集必须提供同参考面的
<code>dl_signal_power_mw</code>、<code>dl_thermal_noise_power_mw</code> 与
<code>dl_interference_power_per_slot_per_cell_mw[sample,slot,cell]</code>。服务小区列必须为 0，slot 行数与
信道快照不一致时拒绝复制或平均。</p>
<h2>流间功率已经有自由度，但系统默认还没有开放</h2>
""" + F_STREAM_POWER
    body += """
<p><code>mumimo.mu_precoder()</code> 支持 equal 与 waterfilling：先逐列单位化 W，再显式生成逐流 p。
但是当前体验仿真的真实 MU pair table 固定 equal，<code>sr_system_sim</code> 也没有暴露
<code>power_allocation</code> 参数。因此手册把它列为“链路库已有、系统产品面未开放”，不能让用户误以为一次 UI
选择已经在体验算法中生效。</p>
"""
    body += callout(
        "warn", "RBG0 抬升为什么可能让全局性能下降",
        "<p>固定 Σq 时，RBG0 从 1x 抬到 2x 会迫使未指定 RB 略低于 1x；本小区在 RBG0 的 S 增加，"
        "但所有邻区 UE 在同一 RB 看到的 I 也增加。如果 RBG0 不常被本小区调度、不是瓶颈，或跨小区干扰"
        "代价大于本小区收益，useful bytes、边缘体验甚至全网和速率都会下降。功率守恒只排除了白拿能量，"
        "并不保证任意频域搬运都增益。</p>",
    )
    body += table(
        ["硬边界", "当前行为", "为什么必须这样"],
        [
            ("关闭功控", "profile 全 1x，链路表与历史路径逐位相同", "保证默认行为无隐藏漂移"),
            ("profile 身份", "配置 fingerprint 写入链路表，simulate 拒绝错配复用", "防止结果标签与实际建表 profile 不同"),
            ("调度小区", "一个 SystemResult 只允许同一 serving cell 的 UE", "不同小区的 RBG 不是同一个互斥资源池"),
            ("capacity + MU + RB 功控", "当前拒绝，需切 experience", "legacy MU 只有标量增益，没有逐 RBG pair SINR"),
            ("跨 RBG 有效 SINR", "当前 RBG 内线性、RBG 间 dB 平均", "尚未用链路级 EESM/MIESM β 标定"),
        ],
    )
    body += "<p class=source-row>配置与守恒：" + source_ref("src/superran/power_control.py", "class RbPowerControlConfig") + " · 精确耦合：" + source_ref("src/superran/power_control.py", "def couple_rb_power") + " · 系统入口：" + source_ref("src/superran/system.py", "rb_power_control") + "</p>"
    return Page(
        "powercontrol", "功控自由度与逐 RB 功率耦合", "链路算法", "POWER CONTROL",
        "拆开每天线、逐流、逐 RB 与邻区负载，并说明 q 如何同时改变本小区信号和跨小区干扰。", body,
        ("power control", "RB profile", "waterfilling", "EBF", "neighbor load", "S/I/N"),
    )


def sinr_page() -> Page:
    body = """
<h2>从 H 和 W 到 post-MMSE SINR</h2>
<p>预编码权由 <code>h_est</code> 设计，等效信道和判错由当前 <code>h_true</code> 评估。
接收机看到期望流、同用户其他流、MU 其他用户、邻区空间协方差和热噪声。</p>
""" + F_MMSE + F_STREAM_SINR
    body += """
<h2>先钉住信号参考面：总载波、每 RB、数字 BF 是三层</h2>
""" + F_RB_LINK_BUDGET + F_PREBEAM_ANCHOR
    body += """
<p><code>tx_power_dbm</code> 是整个活动载波的导通总功率，<code>noise_power_dBm</code> 是一个
活动 RB 的 kTB+NF；所以 273 RB 必须先减 24.36 dB。大尺度预算已包含阵元方向图、固定子阵和
电下倾，但不包含 64 端口数字预编码。ChannelHub 的 first-party SNR/SIR/SINR 因而都在
<strong>预数字波束、每 RB</strong>参考面。</p>
<p>链路级用 <code>E[|H|²]</code> 反标总损伤；rank-1 的 <code>E[σ₁²]</code> 是后波束诊断量。
上式给出一个直接反例：若 H 的数字 BF 增益为 14 dB，那么 rank-1 post-BF SINR 应比几何
SINR 高 14 dB；拿 σ₁² 反标噪声会把这 14 dB 人为抵消。</p>
"""
    body += callout(
        "warn", "两个量都叫 SINR，但所在参考面不同",
        "<p><code>sinr_dB</code> 是大尺度预数字波束工作点；<code>sinr_per_rb_stream_db</code> 是指定"
        "预编码、rank、接收机后的逐 RB/逐流结果。前者不是后者的宽带平均值，而是后者的功率"
        "锚点。文档、代码和测试必须把两者名字带全。</p>",
    )
    body += """
<h2>“全带谱效”现在如何计算</h2>
"""
    body += steps((
        ("逐快照、逐候选 rank", "<p>对 r=1..4 用 gNB 可见 CSI 设计 SVD/Type-I 权，并在真实当前信道上算逐 RB×流 post-MMSE SINR。</p>"),
        ("RB → RBG", "<p>每 16 RB 在线性功率域平均各流 SINR，得到 17 个 RBG；若输入已经是 RBG，组长为 1。</p>"),
        ("RBG/流 → 一个宽带 SINR", "<p>每个 RBG 先对流取 dB 均值，再对 17 RBG 的 dB 值取算术平均；顺序等价。</p>"),
        ("单码字 MCS", "<p>用该宽带 SINR 查目标 BLER 10% 的最高 MCS；不能逐 RB 各选一档再平均。</p>"),
        ("rank 谱效", "<p><code>SE(r)=r×MCS.se</code>，选择 SE 最大的 rank；不是直接把 Shannon log2(1+SINR) 当系统 MCS 谱效。</p>"),
    ))
    body += F_RANK
    body += table(
        ["名字", "使用的视角", "计算/用途"],
        [
            ("se / best_se", "h_est 设计、h_true 评估", "真实接收 SINR → 单码字 MCS → rank×SE；用于结果/legacy 实发记账"),
            ("se_gnb / best_se_gnb", "全在 gNB 当前可见 CSI 上", "调度器估计的可达谱效；避免偷看未来/真实信道"),
            ("SINR_AMC_PRED", "CQI 门限 + gNB BF Gain", "先反折无 OLLA MCS；不是物理 TX/RX SINR，不查 BLER"),
            ("sinr_tx_db（历史字段）", "同 SINR_AMC_PRED", "仅为 API 兼容保留；新文档和结果解释不得简称 SINR_TX"),
            ("SINR_NEBF/PEBF/EBF_RX", "h_true + 实际物理 Q", "最终 MCS 的 BLER 查询输入；默认 NEBF"),
            ("TBS(17)", "slot、MCS、rank、17 RBG", "experience PF 排序的 fullband potential，单位 bytes"),
            ("grant TBS(n)", "实际 grant bitmap", "experience 实发与 PF credit；功控时对 subset 重聚合/重选 MCS"),
        ],
    )
    body += callout(
        "warn", "当前有效 SINR近似",
        "<p>跨 RBG 使用 dB 算术平均，是透明、偏保守的工程基线，但不是经 BLER 曲线标定的 EESM/MIESM。"
        "因此本文把它称为“宽带有效 SINR近似”，不称为标准链路抽象。要对频选深衰给出高精度 BLER，"
        "下一阶段应按 MCS/码块标定 β 或 MI 映射。</p>",
    )
    body += """
<h2>Shannon 谱效在哪里</h2>
<p><code>linklevel.link_performance</code> 仍提供 <code>Σlog₂(1+γ)</code> 作为物理链路谱效和独立
注水容量参考；系统仿真使用离散 MCS/TBS。二者都叫“谱效”时必须带限定词，不能把 SVD 曲线
冒充容量上界，也不能把 MCS 表值解释成 Shannon 容量。</p>
"""
    body += "<p class=source-row>聚合：" + source_ref("src/superran/mumimo.py", "def user_sinr_db") + " · 建表：" + source_ref("src/superran/system.py", "def build_link_tables") + "</p>"
    return Page(
        "sinr", "接收机、SINR 与全带谱效", "链路算法", "POST-MMSE SINR",
        "明确回答全带谱效的五步计算、gNB/真实视角和当前有效 SINR近似。", body,
        ("MMSE", "全带谱效", "best_se", "单码字", "EESM"),
    )


def bfgain_page() -> Page:
    body = bf_gain_svg()
    body += """
<h2>BF Gain 究竟是什么</h2>
<p>SuperRAN 中的 BF Gain 不是天线口径增益、不是奇异值比，也不是用真实接收
SINR 事后反推的余量。它的定义是：<strong>基站当前可见 CSI 上，实际发送方向
相对 PMI 参照方向的 post-MMSE SINR dB 差</strong>。方向与功率约束是两个轴：默认
发送方向为 SVD，默认功率约束为 NEBF，所以物理 TX 分支称 <code>SINR_NEBF</code>；
显式选择 PEBF/EBF 时分别称 <code>SINR_PEBF</code>/<code>SINR_EBF</code>。PMI 分支使用
同一个约束，但业务名保持 <code>SINR_PMI</code>。</p>
<h2>先复现两套方向，再复现物理功率</h2>
<p>代码信道记为 <code>Hcode[time,frequency,BS-port,UE-port]</code>。常见教科书的下行
矩阵是 <code>Hmath=(Hcode)ᴴ</code>；下面所有矩阵维度都据此确定。SVD 路径取发射协方差
主特征方向；PMI 路径生成过采样二维 DFT + 双极化共相位列，并在宽带残余协方差上逐层
贪心选列。后者是明确标注的 Type-I-style 工程近似，不是完整 38.214 多层矩阵码本。</p>
"""
    body += F_SVD_DIRECTION + F_PMI_CODEBOOK + F_SPATIAL_POWER
    body += callout(
        "good", "“每天线列归一”在代码里为什么写成行范数",
        "<p>代码的物理矩阵是 <code>Q[frequency, antenna, stream]</code>，所以第 m 根天线功率是 "
        "<code>sum_k |Q[m,k]|²</code>，即 Q 的<strong>第 m 行</strong>范数平方。现场若把权写成 "
        "<code>[stream,antenna]</code>，同一件事就是每根天线对应的<strong>列</strong>范数归一；"
        "只是矩阵转置约定不同，不是算法不同。</p>",
    )
    body += "<h2>SINR_PMI 与 SINR_NEBF/PEBF/EBF 的精确算法</h2>"
    body += F_BF_STREAM + F_BF_RBG + F_BF_GAIN
    body += table(
        ["名称", "信道与物理 Q", "用途", "能否直接查 BLER"],
        [
            ("SINR_PMI,gNB", "h_prec + Q(PMI,C)", "BF Gain 参照；与实际 TX 同 rank/约束", "不能"),
            ("SINR_NEBF/PEBF/EBF,gNB", "h_prec + Q(SVD,C)", "减 SINR_PMI 得 gNB 可见 BF Gain", "不能"),
            ("SINR_AMC_PRED", "Γ(MCS(CQI)) + BF Gain", "反折无 OLLA MCS；不是物理接收测量", "不能"),
            ("SINR_NEBF/PEBF/EBF,RX", "h_true + 同一个 gNB 设计 Q(SVD,C)", "与最终发送 MCS 一起查 NewTx 曲线", "能"),
        ],
    )
    body += F_AMC_PRED + F_RX_BLER
    body += table(
        ["量", "使用的信道视角", "是否进入当次 MCS", "用途"],
        [
            ("bf_gain_user_db", "h_prec / gNB 可见 CSI", "是", "加到 CQI 初始 MCS 的目标 BLER SINR 门限"),
            ("bf_gain_rbg", "h_prec / gNB 可见 CSI", "是（频率感知路径）", "逐 RBG grant 聚合"),
            ("bf_gain_true_user_db", "当前 h_true", "否", "事后审计实际波束命中情况"),
            ("bf_gain_prediction_error_db", "true 审计 − gNB 预测", "否", "观察 CSI 估计/老化误差，由后续 OLLA 闭环吸收"),
            ("actual_receive_sinr_db", "h_true + 实际 Q", "否（发送决策已完成）", "final MCS 的唯一 BLER 查询 SINR"),
        ],
    )
    body += """
<h2>从信道到一个 BF Gain 的实际顺序</h2>
"""
    body += steps((
        ("取 gNB CSI", "<p>系统仿真使用当前可用的 <code>h_prec</code>；打开 SRS 老化后它可能来自较早快照。</p>"),
        ("构造两套方向", "<p>PMI 由 Type-I-style 宽带码本搜索得到；发送方向默认为 SVD。<code>precoder=type1</code> 时两方向相同。</p>"),
        ("形成两套物理 Q", "<p>两边强制同 rank、每流 P/r，并经过同一 C。默认 NEBF 将每根天线功率强制到 P/M。</p>"),
        ("算 gNB post-MMSE SINR", "<p>在同一 h_prec 和总损伤上分别计算 SINR_NEBF/PEBF/EBF 与 SINR_PMI 的逐 RB×流线性值。</p>"),
        ("RB → RBG → 宽带", "<p>RBG 内先线性平均 RB，转 dB 后对流平均；最后对全带 RBG 平均并作 TX−PMI。</p>"),
        ("进入 AMC", "<p><code>Γ(MCS(CQI))+G_BF</code> 得到 SINR_AMC_PRED，再反折无 OLLA MCS；它不用于 BLER。</p>"),
        ("真实判错", "<p>把同一个发送 Q 作用到 h_true，聚合得到 SINR_*_RX，再用最终 MCS 查 NewTx 曲线。</p>"),
    ))
    body += callout(
        "danger", "h_true 不能进当次 BF Gain",
        "<p>如果先用 h_est 设计权，却在 h_true 上评估 SVD/PMI 差后加到当次 MCS，"
        "就等于让基站预知波束是否打准。当 CSI 完美时两者恰好一致，这个 bug 不会显形；"
        "只有开老化/估计误差才会暴露。</p>",
    )
    body += callout(
        "warn", "BF Gain 不保证非负",
        "<p>EBF 下、完美 CSI 且同 rank 时，SVD 通常不会输给量化 PMI。但陈旧 CSI、"
        "NEBF 破坏正交性、码本/rank 边界或数值工作点都可使差值为负。代码不把它静默钳到0。</p>",
    )
    body += callout(
        "danger", "13.3272 dB 这个例子为什么不能报 BLER=1",
        "<p>CQI5→MCS9，其 10% 门限为 8.3272 dB；BF Gain=5 dB 只得到 "
        "<code>SINR_AMC_PRED=13.3272 dB</code>，反折 MCS14，再加 OLLA +2 得最终 MCS16。"
        "MCS16 的 10% 门限 14.8955 dB 高于这个预测坐标，只说明 OLLA 把发送档位推到"
        "名义门限之上；<strong>不说明真实 TB 一定误块</strong>。没有 "
        "<code>SINR_NEBF_RX/actual_receive_sinr_db</code> 时 BLER 必须写 unknown；有数据集时"
        "只把该接收端 SINR 与 MCS16 送入预置曲线。</p>",
    )
    body += table(
        ["边界情形", "预期结果", "原因"],
        [
            ("TX 权 = PMI 权", "BF Gain = 0 dB", "两条计算链逐值相同"),
            ("h_prec = h_true", "predicted = true audit", "无 CSI 估计/老化失配"),
            ("h_prec 陈旧", "predicted 可高于 true", "基站在旧信道上以为 SVD 仍对准"),
            ("rank 不同", "拒绝比较", "否则混入层数和每流功率差"),
        ],
    )
    body += "<p class=source-row>" + source_ref(
        "src/superran/system.py", "BF Gain = SVD − PMI") + " · " + source_ref(
        "src/superran/csi_aging.py", "def mmse_stream_sinr") + " · " + source_ref(
        "src/superran/mumimo.py", "def user_sinr_db") + " · " + source_ref(
        "src/superran/loader.py", "def tdd_mcs") + "</p>"
    return Page(
        "bfgain", "BF Gain：SVD 相对 PMI 的可见增益", "链路算法", "BEAMFORMING GAIN",
        "明确 BF Gain 用哪份 CSI、哪两套权、什么功率/接收参考面以及如何从逐 RB/流聚合为宽带 dB 值。", body,
        ("BF Gain", "SVD", "PMI", "h_prec", "post-MMSE", "CSI 老化"),
    )


def linkadapt_page() -> Page:
    body = link_flow_svg()
    body += """
<h2>SINR_AMC_PRED 不是物理发送 SINR，更不是接收真值</h2>
""" + F_AMC_PRED + F_RX_BLER
    body += """
<p>CQI 是终端用Type-I/PMI参照权在真实信道上测得并按报告周期更新的长期宽带量；历史API使用
表行0..14，对应上报4-bit CQI1..15，256QAM映射为
<code>[0,2,4,6,8,10,12,14,16,18,20,22,24,26,28]</code>。先映射
初始 MCS，再取该 MCS 的 10% BLER SINR 门限 Γ。BF Gain 则是 gNB 在自己可见的（可能陈旧）
SRS CSI 上，实际发送方向相对 PMI 方向的 post-MMSE SINR 差；默认两边都施加 NEBF。
两者相加形成 <code>SINR_AMC_PRED</code> 后重选 MCS，最后由用户级 MCS-domain SU OLLA
调整。物理发射分支叫 <code>SINR_NEBF/PEBF/EBF</code>，同一个 Q 打到 h_true 后得到的
<code>SINR_*_RX</code> 才能查最终BLER。真实上报CQI0是out-of-range；历史row0映射MCS0
并明确对应reported codepoint 1。</p>
"""
    mapping_rows = la.internal_cqi_mapping_rows(mcs_table=3)
    body += table(
        ["历史表行", "上报4-bit CQI", "原始映射 MCS", "当前曲线实际 MCS", "状态"],
        [
            (
                str(row["cqi_row"]), str(row["reported_cqi_codepoint"]),
                str(row["requested_mcs"]), str(row["mcs"]),
                "上界钳位：缺 MCS28 BLER 曲线"
                if row["mcs_clipped_to_profile"] else "逐项精确映射",
            )
            for row in mapping_rows
        ],
    )
    body += callout(
        "warn", "最高CQI行的原始编号与当前曲线覆盖不同",
        "<p>历史表行14（上报CQI15）请求MCS28，但 "
        "<code>preset_20b_256qam</code>只有MCS0..27。代码保留"
        "<code>requested_mcs=28</code>并显式钳到27；自动CQI15使用MCS27的"
        "NewTx门限，不伪造MCS28曲线。</p>",
    )
    body += callout(
        "danger", "禁止 oracle",
        "<p>把真实接收 SINR 的全仿真均值回填给发送侧，会同时泄露未来信道和实际波束命中效果。"
        "这会掩盖 CSI 老化与 OLLA 的作用。CQI expanding mean 只能使用 0..s 的历史报告，"
        "BF Gain 必须来自 gNB 自己的 CSI。</p>",
    )
    body += """
<h2>OLLA 如何闭环</h2>
""" + F_OLLA + F_FINAL_MCS
    body += """
<p>用户默认只设目标 BLER。给定 ACK 上调步长 <code>s_up</code> 后，系统用
<code>s_down=s_up(1-p)/p</code> 反解 NACK 下调步长；例如 <code>p=10%</code>、
<code>s_up=0.01 MCS</code> 得 <code>s_down=0.09 MCS</code>。用户若显式填入 down 步长，
系统不覆盖，但结果会标成 <code>explicit_user_override</code>，与默认的
<code>auto_from_target_bler</code> 区分。warmup 可用同一比例的加速因子加快收敛；
测量窗和预热窗分别记录。SU OLLA 与 MU OLLA 都是用户级数组，
后者不区分具体配对关系。</p>
<p><strong>顺序已统一：</strong>先以 <code>base_tx_sinr_db</code> 查询预置 BLER 表，
得到并记录 <code>mcs_without_olla</code>；再把用户级连续 MCS offset 加到该 MCS，
<code>floor</code> 并钳位后得到 allocation 中的最终 <code>mcs</code>。MU 先在 SINR 域叠加
<code>corr_loss_db</code> 与 <code>power_loss_db</code> 并反折基准 MCS，再加 SU/MU 两份
MCS-domain OLLA。历史 <code>*_before_db</code> 字段名仅为 API 兼容保留。</p>
"""
    body += table(
        ["阶段", "SU", "MU", "结果留痕"],
        [
            ("基准档", "S(γbase,target)", "S(γbase+CorrLoss+PowerLoss,target)", "mcs_without_olla"),
            ("最终发送档", "floor(m_base+SU-OLLA)", "floor(m_base,MU+SU-OLLA+MU-OLLA)", "mcs"),
            ("真实判错", "最终 mcs + true SINR", "最终 mcs + pair true SINR", "bler / ack"),
        ],
    )
    body += callout(
        "warn", "历史 *_db 参数名只是兼容层",
        "<p><code>sr_tdd_mcs</code>、<code>sr_system_sim</code> 和 <code>experience_v2</code> "
        "现在都使用连续 MCS-index OLLA。系统状态仍按用户、SU/MU 分开收敛；"
        "旧 <code>olla_step_*_db</code> 名称暂时保留，返回值会用 <code>olla_domain</code> "
        "明确其单位。</p>",
    )
    body += """
<h2>体验路径当前只使用预置 MCS Table 3</h2>
<p><code>experience_v2</code> 只接受 <code>preset_20b_256qam / table=3</code>。
传入 Table 1/2 会硬失败，因为仅放开 MCS 索引却没有同套 BLER、TBS/profile
元数据，会把两个物理口径拼在一起。<code>TbsLookup.build(..., mcs_table=...)</code>
和链路表仍显式保留 table/profile 接口；未来要扩展时按完整 profile 插件增加，
不需要改调度主循环。</p>
<h2>TBS 为什么不能用除法反推 RBG</h2>
""" + F_TBS + F_RBG_SEARCH
    body += """
<div class="toy"><div><b>实算：MCS 12 / rank 2 / D slot</b>
<p>1 RBG = 1,729 B；若线性外推，17×1,729 = 29,393 B；38.214 量化后的真实 17 RBG
= 29,722 B，偏 +1.119%。</p></div><div><b>会怎样错</b><p>payload=29,394 B 时，除法会认为“17 个也不够”或在其他边界少给一个；
<code>searchsorted(side='left')</code> 在单调不减表上准确返回第一个够用的 17 且可装下。</p></div></div>
"""
    body += callout(
        "good", "表合同",
        "<p><code>TbsLookup</code> 建 2×28×4×17 = 3,808 个 int64（D/S 两类 slot）。"
        "初始化时全扫并要求每行单调不减；量化平台（相邻前缀 TBS 相同）合法，"
        "只有资源增加却让 TBS 下降才当场失败。<code>searchsorted(side='left')</code> "
        "会在平台上返回第一个够用的前缀。</p>",
    )
    body += """
<h2>BLER 与 HARQ 边界</h2>
<p>表 1/2 是 38.214 MCS/CQI + 分析 BLER 模型；表 3 是内置的 20B 256QAM 28 档预置
MCS 曲线。系统初传与重传都只消费 NewTx 曲线：每个 TB 最多一次重传，默认 IR 用半谱效
等效 MCS 查表，可选 CC 用同档曲线 +3.0103 dB。原始 ReTx 行保留作来源审计，不进入系统判错。</p>
"""
    body += "<p class=source-row>发送侧：" + source_ref("src/superran/system.py", "发送侧 SINR = CQI") + " · TBS：" + source_ref("src/superran/experience.py", "class TbsLookup") + "</p>"
    return Page(
        "linkadapt", "CQI、BF、OLLA、MCS 与 TBS", "链路算法", "LINK ADAPTATION",
        "发送/接收 SINR 分离、因果 CQI、OLLA 与非线性 TBS 反查。", body,
        ("CQI", "BF Gain", "OLLA", "MCS", "TBS", "searchsorted"),
    )


def bler_page() -> Page:
    body = bler_pipeline_svg()
    body += """
<h2>先分清当前主链与两条可选旁路</h2>
<p><strong>当前体验系统主链只有：</strong>逐 RB/stream SINR → RBG 内线性平均 →
跨 RBG/选定 rank streams 做 dB 平均 → 预置 BLER 表选最终发送 MCS并判 TB ACK/NACK。
它不调用 QAM 约束容量，也不调用 MIESM/EESM。</p>
<p>旁路 A 是表 1/2 的分析 <code>BlerModel</code>，会用 QAM 互信息和有限码长形状；
旁路 B 是显式链路级 <code>effective_sinr()/link_adaptation()</code>，可选 MIESM/EESM。
MCS/CQI 表与 TBS 算法来自 38.214，但分析 BLER 和预置曲线都不能写成“3GPP BLER 曲线”。</p>
<p>因此每个 BLER 结果至少要带 <code>backend</code>、<code>model_version</code>、空口 MCS、
码字级有效 SINR、NewTx/重传身份与曲线 profile。TBS、RBG 数和 rank 仍要随 allocation 保存，
用于证明重传身份没有改变，但按当前预置 profile 它们不是 BLER 曲线的查询轴。</p>
<h2>先钉住可执行链：MCS 表 → 单码字 SINR → 曲线选档</h2>
""" + F_MCS_PROFILE + F_CODEWORD_SINR + F_MCS_SELECT
    body += """
<p>预置 Table 3 固定 28 档 MCS。每档由 <code>Qm</code>、码率 <code>R</code>、名义谱效
<code>η=QmR</code> 和一条 NewTx BLER 曲线共同定义；MCS index 才是曲线身份，不能只看谱效。
系统收到逐 RB、逐 stream 的线性 SINR 后，先在每个 16-RB RBG 内逐流做线性平均，再对实际
grant 的全部 RBG 与选定 rank streams 做 dB 平均，得到唯一 <code>γcw</code>。最后逐档查询
NewTx 曲线，选 BLER 不超过目标值的最高 MCS。详细版给出完整 28 行表、1,824 个原始点和独立
NumPy 重实现。</p>
<h2>预置表口径：一次 TTI 的 TB 就是一次 BLER 事件</h2>
""" + F_PRESET_TTI_BLER
    body += """
<p>当前表驱动仿真不单独查询 CBLER，也不在系统层用 CB 数再次合成 TBLER。每个用户在一个 TTI
中的 grant 视为一个独立、单码字 TB；调度器先确定 RBG、rank、MCS 和 TBS，随后只用该用户的
单码字有效 SINR与 MCS 查询一次通用 NewTx 曲线，再抽一次 ACK/NACK。跨 RBG 与跨 rank stream
均采用 dB 算术平均；RBG 内多个 RB 先在线性功率域平均。</p>
"""
    body += table(
        ["层次", "已经确认的口径", "SuperRAN 当前实际承载"],
        [
            ("预置表误块事件", "一个已调度 TTI 中该用户的单码字 TB；CB 不单独暴露",
             "每个 grant/用户只抽一次 ACK/NACK，与预置事件单位一致"),
            ("预置表查询输入", "单码字有效 SINR + MCS",
             "TBS、RE、RBG、rank、码字数、场景和接收机细节均不作为查询轴"),
            ("曲线范围", "MCS0..27 的通用初传曲线",
             "同一曲线跨 TBS/rank/场景复用是已确认产品口径，不再标成数据缺口"),
            ("CB 的位置", "物理编码内部可以存在多个 CB，但预置表 BLER 接口不单报 CB",
             "表 3 禁止再套 CB→TB 公式；表 1/2 分析后端才使用该公式"),
        ],
    )
    body += callout(
        "decision", "当前是单码字通用 TB-BLER 抽象，不展开 RE/TBS/CB",
        "<p><code>experience_v2</code> 仍为 1～17 个 RBG 精确计算 TBS，因为它决定可发送字节、"
        "padding、PF 记账与重传身份；但 BLER lookup 明确保持 <code>(mcs, codeword_sinr_db)</code>。"
        "这不是忘记传 TBS，而是当前预置 profile 的显式约定。</p>",
    )
    body += """
<h2>可选链路级分析能力：当前预置表系统路径不使用</h2>
""" + F_QAM_MI + F_MIESM + F_EESM
    body += """
<p><strong>这三条公式不在当前 <code>experience_v2 + table=3</code> 主链路中。</strong>
QAM 约束容量用于表 1/2 的分析 BLER 模型，回答“给定星座时理论上最多承载多少互信息”；
MIESM/EESM 是把频选 SINR 压成一个等效值的通用链路级方法，只有调用
<code>linkadapt.effective_sinr()</code> / <code>link_adaptation(..., esm=...)</code> 时才生效。
当前预置表系统路径采用前文明确的“RBG 内线性、跨 RBG 与选定 rank streams 做 dB 平均”，
然后直接查预置 NewTx 曲线。因此它们在本章只用于解释可选后端与未来升级方向，不参与当前体验结果。</p>
"""
    body += table(
        ["能力", "当前代码中的用途", "experience_v2 + table=3"],
        [
            ("QAM constrained MI", "表 1/2 分析 BLER、通用链路级诊断", "不使用"),
            ("MIESM/EESM", "显式调用 effective_sinr/link_adaptation 的频选压缩", "不使用"),
            ("RBG/stream dB 平均", "预置表系统路径的单码字 SINR", "当前实际使用"),
            ("预置 NewTx 曲线", "MCS 选择与 TB BLER", "当前实际使用"),
        ],
    )
    body += """
<h2>分析 BLER 后端怎样从 CB 合到 TB</h2>
""" + F_TB_BLER
    body += """
<p>分析后端先用 QAM MI 判断码率相对约束容量的裕量，再用码长和实现损失形成单码块瀑布，
最后按码块数合成 TB BLER。<code>anchor_check()</code> 只能把各 MCS 的 10% 门限摆出来与独立公开曲线
对照；没有参考曲线时，它不是自动“校准通过”证书。调制切换点允许门限小幅回落，单调性只在同一
Qm 内检查。这个分析后端用于表 1/2，不描述预置表 3 的运行逻辑。</p>
<h2>预置 20B 曲线的数据合同</h2>
""" + F_LOG_BLER_INTERP
    body += table(
        ["字段", "当前事实", "不能外推的内容"],
        [
            ("事件单位", "一个用户 grant/TTI 的单码字 TB，CB 不单独建模", "不能再套独立 CB 合成公式"),
            ("系统使用", "MCS0..27 的 28 条 NewTx 曲线", "原始 ReTx 行保留审计，但不进入当前系统 BLER"),
            ("原始资产", "NewTx/ReTx 各 28 条，共 56 条曲线、1,824 个点", "源脚本额外未映射 MCS 的行，其具体码率/点/语义未被原始导入保留；当前合同不需要它们"),
            ("横轴", "源标签 Es/No；预置表解释为单码字经典 MMSE 有效 SINR", "跨 RBG/rank 采用已确认的 dB 平均，不是原始天线前 SNR"),
            ("插值", "log10(BLER) 域线性；范围外保守钳位", "不外推未测量的低 BLER 尾部"),
            ("完整性", "SHA-256、28 MCS 覆盖、横轴/BLER 单调、10% crossing", "hash 一致只证明数据没漂，不证明现场代表性"),
            ("CQI", "默认表3使用版本化256QAM离散映射 + 预置NewTx目标BLER门限",
             "表1/2的38.214 CQI分支只在显式选择时使用；不能把预置映射说成3GPP标准"),
        ],
    )
    body += "<h2>只允许一次重传：默认 IR，可选 CC</h2>" + F_HARQ_CC + F_HARQ_IR
    body += callout(
        "decision", "等效 MCS 只改 BLER 查表，不改空口发送参数",
        "<p>初传 NACK 后，TB 进入唯一一次重传：MCS、RBG 数、rank 与 TBS 全部冻结，"
        "并在相同 D/S slot 类型上发送。默认 IR 把初传 MCS 的谱效除以 2，再用"
        " <code>searchsorted</code> 式的向下查表得到等效 MCS；CC 保持原档并增加 3.0103 dB。"
        "两者都只查询 NewTx 曲线。重传失败则结束本次 HARQ，字节留在 DRB 队列，后续作为新 TB；"
        "不会发生第二次重传。当前仍未展开 RV、软比特、并行 process 和标准 HARQ timing。</p>",
    )
    body += (
        '<p>标准边界可直接回查 ETSI 发布的 '
        '<a href="https://www.etsi.org/deliver/etsi_ts/138200_138299/138212/18.05.00_60/ts_138212v180500p.pdf" '
        'target="_blank" rel="noreferrer">3GPP TS 38.212 V18.5.0</a>（TB CRC、CB 分段与 LDPC）和 '
        '<a href="https://www.etsi.org/deliver/etsi_ts/138200_138299/138214/18.03.00_60/ts_138214v180300p.pdf" '
        'target="_blank" rel="noreferrer">3GPP TS 38.214 V18.3.0</a>（MCS 与 TBS）。两份标准都不会替特定接收机提供一套通用预置 BLER 瀑布。</p>'
    )
    body += "<p class=source-row>映射与分析模型：" + source_ref("src/superran/linkadapt.py", "def effective_sinr") + " · 预置曲线：" + source_ref("src/superran/bler_curves.py", "def verify_curves") + " · TTI 判错：" + source_ref("src/superran/experience.py", "def _bler_lookup") + "</p>"
    return Page(
        "bler", "BLER：MCS 表、曲线与 HARQ 复现", "链路算法", "BLER & LINK MAPPING",
        "从 28 档 MCS、单码字有效 SINR 和 1,824 个原始点，逐步复现 TB 判错与一次 CC/IR 重传。", body,
        ("BLER", "MCS Table 3", "有效 SINR", "preset_20b_256qam", "HARQ", "复现"),
        detail_extra=bler_detail_atlas(),
    )


def mu_page() -> Page:
    body = link_flow_svg() + mu_decision_svg()
    body += """
<h2>MU 的发送侧 MCS</h2>
""" + F_MU_SINR + F_POWER_LOSS
    body += """
<p>SU 链先得到 CQI + BF + SU OLLA。MU 再加三项：用户间残留相关性折算的
<code>CorrLoss≤0</code>；同一 RBG 总功率在全部 MU layers/users 间平分的
<code>PowerLoss</code>（两个 rank2 用户相对单用户 rank2 为 −3 dB）；以及独立的用户级 MU OLLA。
真实接收 SINR来自 pair 信道、ZF/RZF 权和当前 h_true，仍不等于这些 dB 项的简单和。</p>
<h2>Phase A 的真实 pair 表</h2>
"""
    body += steps((
        ("候选对", "<p>按用户有效信道相关性与门限筛选，两用户一组。</p>"),
        ("预编码", "<p>当前 experience 边界为 2 用户、每用户 rank2，使用 ZF 或带噪声/CSI-error loading 的 RZF。</p>"),
        ("双视角", "<p>在 gNB 估计 CSI 上得到预测 CorrLoss/MCS 输入；在真实当前信道上得到逐用户/逐 RBG SINR 与 BLER 输入。</p>"),
        ("持久表", "<p>保存 correlation、CorrLoss、PowerLoss、true/predicted SINR 与可选逐 RBG 数组；Phase B 不做矩阵求逆。</p>"),
    ))
    body += """
<h2>Phase B 为什么比较 useful bytes</h2>
<p>PF 先排一次优先级，然后分别构造“全 SU”和“允许 MU”的完整 TTI 计划。两者都按队列实际剩余
字节截断收益：TBS 超出业务包的 padding 不算谱效收益。若 SU 能传完所有当前队列，强制 SU；否则
MU useful bytes ≥ SU 时走 MU。这个规则避免在轻载/小包场景为了理论空间复用而引入无意义干扰。</p>
"""
    body += callout(
        "warn", "50% 话务已经校准，MU 正式收益仍被收敛门阻断",
        "<p>独立校准阶段以 8 次 replication 搜索到 "
        "<code>inter_arrival_scale=0.36206688</code>（负载倍率 2.761921），"
        "参考臂测得平均 PRB 利用率 <strong>50.77%</strong>，命中 50%±3% 目标；随后冻结同一份话务"
        "给 SU/MU 两臂，避免各臂各自校准污染 A/B。</p>"
        "<p>但 8/8 个 adaptive-MU pilot 都没有通过 MU OLLA 收敛门，SU OLLA 则全部通过。"
        "MU 用户 grant share 约 3.3%～12.7%，大多数重复的预期 MU BLER 约 0.07%，远低于 10% 目标。"
        "因此正式 16 次重复没有启动，当前不能声称 MU 带来体验收益。"
        "校准与门禁证据分别在 <code>experience_mu_50pct_load_calibration.json</code> 和 "
        "<code>experience_mu_power_pilot_gate2_diagnostics.json</code>；这也暴露出用户级、非 pair-specific "
        "MU OLLA 在动态配对下的下一阶段边界。</p>",
    )
    body += table(
        ["对象", "当前口径", "常见错误"],
        [
            ("MU PRB", "共享 RBG 在小区物理资源只计一次", "给两个用户各计一份导致利用率>100%"),
            ("用户 exposure", "每个配对用户都暴露于该 MU RBG", "误以为每人只拿一半频域"),
            ("用户归因", "共享 RBG 在两 UE 间等分，跨用户可加", "把 exposure 相加做小区资源"),
            ("MU OLLA", "每用户一条、所有 pair 共用", "误称为 pair-specific OLLA"),
            ("legacy MU", "MU/SU 聚合标量比值", "把它当 experience_v2 的 pair 实现"),
        ],
    )
    body += callout(
        "decision", "下一阶段 MU 细化",
        "<p>当前落地的是可验证的最小真实 MU：2UE×rank2、ZF/RZF、用户级 MU OLLA。"
        "一般 rank 组合、3/4 用户、pair-specific OLLA、HARQ 进程与更大候选图仍需业务/性能约束后再扩展。</p>",
    )
    body += "<p class=source-row>pair 表：" + source_ref("src/superran/system.py", "def build_mu_pair_tables") + " · TTI 决策：" + source_ref("src/superran/experience.py", "SU_clears_all_queues") + "</p>"
    return Page(
        "mu", "MU-MIMO 与 SU/MU 自适应", "链路算法", "MU-MIMO",
        "CorrLoss、PowerLoss、双 OLLA、真实 pair 表和 useful-bytes 计划比较。", body,
        ("MU", "ZF", "RZF", "CorrLoss", "PowerLoss", "pair table"),
    )


def modes_page() -> Page:
    body = phases_svg()
    body += """
<h2>两种模式，不是两档精度</h2>
"""
    body += table(
        ["维度", "capacity / legacy_v1", "experience / experience_v2"],
        [
            ("问题", "满带调度下的容量/历史 KPI 复现", "有限业务包下的排队、资源与用户体验"),
            ("每 TTI 调度", "SU 或 legacy 标量 MU；用户通常拿全带", "一次 PF 排序后可服务多个 UE，按需 RBG"),
            ("MU", "预计算聚合 ratio 后主循环标量折算", "候选 pair 的真实链路表与完整 SU/MU plan"),
            ("PF numerator", "gNB best_se", "假设全带 TBS(17)"),
            ("PF credit", "best_se/受 MU rank 修正的 SE", "默认实际 scheduled TBS；可选 ACK goodput"),
            ("队列", "历史 traffic/burst 抽象", "arrival-object FIFO、NACK 留队、warmup 切窗"),
            ("体验速率", "legacy trim", "DRB busy-period + fractional small burst + 含头速率"),
            ("资源 KPI", "整带占用为主", "PRB utilization、0..17 占用、MU/used、用户归因"),
            ("HARQ", "一次 IR/CC；同 MCS/RBG 数/rank/TBS", "一次 IR/CC；按需 RBG 身份冻结并有 allocation 证据"),
        ],
    )
    body += callout(
        "danger", "禁止横向偷换",
        "<p>不能把 experience 的按需 RBG 结果与 capacity 的全带 legacy 结果直接相减后称为“算法提升”；"
        "两边必须共享 evaluation profile、话务、CSI、功率、warmup 和 KPI 定义。"
        "同名字段若语义不同，结果 JSON 会带 profile/version/notes。</p>",
    )
    body += """
<h2>为什么要预热</h2>
<p>例如总仿真 5 s、<code>warmup_s=1</code>：业务、SRS、CSI/PMI、PF 平均和 OLLA 从 0 s 开始运行，
但体验/KPI 只统计 1–5 s。这样既让状态真实收敛，又不把初始空队列、SRS 未扫齐和 OLLA 冷启动损失
混入稳态指标。预热时可以加速 OLLA，但测量窗应恢复正常步长，并回传切窗时的状态。</p>
"""
    body += "<p class=source-row>模式路由：" + source_ref("src/superran/system.py", "evaluation_mode") + " · 体验入口：" + source_ref("src/superran/experience.py", "def simulate_experience") + "</p>"
    return Page(
        "modes", "容量评估与体验评估", "系统仿真", "EVALUATION PROFILES",
        "capacity/legacy_v1 与 experience/experience_v2 的语义、实现和 KPI 边界。", body,
        ("capacity", "experience", "legacy_v1", "experience_v2", "warmup"),
    )


def experience_page() -> Page:
    body = phases_svg() + mu_decision_svg()
    body += """
<h2>系统层的载波是固定合同，不是调参项</h2>
<p>当前 TDD 系统/体验仿真统一使用 <code>100 MHz @ 30 kHz</code>，
<code>272 RB = 17 RBG × 16 RB</code>。38.104 标准表对应 273 RB；SuperRAN 在信道
生成前就有意去掉最后 1 RB，让系统层永远是完整的 17 个 16-RB 组。
<code>CarrierGrid.company_tdd()</code> 同时核对张量宽度、带宽标签、SCS 与 BWP 起点；
任一不一致都硬失败，不会把 20 MHz 数据猜成一套 7-RBG 系统口径。</p>
<p>通用 <code>CarrierGrid.from_config()</code> 和 Type-0 首尾 partial-RBG 工具仍保留，
但只用于链路级、旧数据诊断和数学单测；它们不在 TDD 系统页面上暴露。</p>
"""
    body += table(
        ["层级", "RBG 分组", "执行规则"],
        [
            ("TDD 系统/体验", "16×17（17 RBG）", "唯一对外口径；不接受其他 RB/RBG 形状"),
            ("链路级通用工具", "可表示 51/106/273 等 Type-0 边界", "不等于已支持对应系统仿真"),
            ("标准 100 MHz 对照", "273 RB", "第 273 RB 在生成前丢弃，不进入资源利用率分母"),
        ],
    )
    body += callout(
        "warn", "为什么仍保留通用 Type-0 工具",
        "<p>它们用来检查导入数据、验证 TBS 单调性和阻止尾 PRB 被静默丢失；"
        "保留数学能力不等于把它变成产品功能。对外入口只调固定 profile，"
        "页面不再显示 <code>num_rb</code> 或 <code>rbg_size_config</code> 控件。</p>",
    )
    body += callout(
        "warn", "TBS 只需单调不减，不要求严格递增",
        "<p>38.214 的 TBS 量化会让相邻 RBG 前缀偶尔得到相同字节数。"
        "<code>searchsorted(side='left')</code> 在平台上仍会返回第一个够用的前缀；"
        "只有 TBS 随资源增加反而下降，才应硬失败。用全带 TBS 除以 17 或按名义 P "
        "估算都会在量化边界或部分尾组上少给/多给资源。</p>",
    )
    body += """
<h2>一个 DL TTI 的完整顺序</h2>
"""
    body += steps((
        ("话务先到达", "<p>所有 D/S/U/G 时隙都执行 arrival step；上行时隙不能让业务凭空消失。</p>"),
        ("选择物理快照", "<p><code>snap=(tti//snap_every)%n_snap</code>，读取 gNB/真实链路表与 OLLA。</p>"),
        ("计算 fullband potential", "<p>按当前 rank/MCS 计算 TBS(17)，作为 PF numerator；队列小不改变排序的链路机会。</p>"),
        ("PF 排序一次", "<p>经典 PF 默认；QoS-PF 在 α=β=1、γ=0、w=1 时逐分配退化为经典 PF。</p>"),
        ("构造 SU/MU plan", "<p>按顺序用 searchsorted 给最小够用 RBG；没有候选需求时剩余资源留空。</p>"),
        ("按 useful bytes 选 plan", "<p>SU 能清空所有队列则强制 SU；否则 MU≥SU 才选 MU。</p>"),
        ("真实 grant 判错", "<p>按实际 bitmap/MCS/TBS，真实 SINR 查 BLER；NACK payload 留队。</p>"),
        ("更新 OLLA/PF/KPI", "<p>记录分配、资源归因、arrival/busy period、首包与完成时延，再更新平均速率。</p>"),
    ))
    body += """
<h2>PF 的 R̄u（RU）到底怎样维护</h2>
""" + F_PF + F_RAVG
    body += """
<p><code>r_avg</code> 的单位是<strong>每个可下行调度 TTI 的 EWMA 字节机会</strong>。每个 D/S TTI
统一更新一次；未被调度用户本次 <code>R_credit=0</code>，平均值自然衰减。U/G TTI 不更新，因为没有
下行资源机会。初值为 1e−6 防除零；<code>a=1/pf_window_tti</code>。</p>
"""
    body += table(
        ["pf_accounting", "R_credit", "含义与取舍"],
        [
            ("scheduled_tbs（默认）", "实际 grant 的 TB bytes，不论 ACK/NACK", "与占用资源机会一致，避免无线随机失败让 PF 过度补偿"),
            ("acked_goodput", "本次 ACK 的 payload bytes", "面向实际好吞吐，但 BLER 随机性直接影响公平平均"),
            ("legacy_fullband", "同 MCS/rank 的 TBS(17)", "反向哨兵/兼容；按需 RBG 下会严重高记"),
        ],
    )
    body += (
        "<p>HARQ 重传也占用真实 RBG，因此默认同样按冻结的 scheduled TBS 进入 "
        "<code>R_credit</code>；但它不进入首传 BLER、SU/MU OLLA 或 SU/MU plan 收益统计。"
        "同 D/S 类型的待重传 TB 先于新 TB 调度，重传后无论 ACK/NACK 都结束本次 HARQ。</p>"
    )
    body += """
<div class="toy"><div><b>正确记账</b><p>若 TPF=100、旧 R̄=1,000 B、用户只获 1 RBG，
MCS12/rank2 的 TBS=1,729 B：新 R̄=0.99×1,000+0.01×1,729=<strong>1,007.29 B</strong>。</p></div>
<div><b>旧全带 bug</b><p>若误记 17 RBG 的 29,722 B：新 R̄=<strong>1,287.22 B</strong>。
同一次 1-RBG 服务把平均速率抬高约 40 倍增量，后续 PF metric 被过度压低，小包用户被饿死。</p></div></div>
"""
    body += callout(
        "good", "PF 记账的正反向证据已经闭合",
        "<p>确定性拥塞哨兵同时跑正确 <code>scheduled_tbs</code> 与故意恢复的 "
        "<code>legacy_fullband</code>：两臂均满足字节守恒，并分别观察到 209/182 次部分 RBG grant；"
        "错误口径使小包平均等待增加 <strong>0.9525 ms</strong>、P95 增加 <strong>9.5 ms</strong>，"
        "到达即服务比例下降 <strong>9.148 个百分点</strong>。证据位于 "
        "<code>artifacts/results/experience_pf_accounting_deterministic_sentinel.json</code>。</p>"
        "<p>真实 mixed 数据集的反向对照虽然各有约 990 次部分 grant，但小包 P95 都是 0.5 ms，"
        "A/B 差异为 0，Gate 3 因而阻断性能结论。它说明当前负载没有形成足够 PF 竞争，不能拿"
        "“方向符合预期”替代统计证据；原始记录在 "
        "<code>artifacts/results/experience_pf_accounting_reverse_control.json</code>。</p>",
    )
    body += F_QOS_PF
    body += callout(
        "note", "经典 PF 已冻结",
        "<p>当前决策 D1 先使用经典 PF。QoS-PF 作为参数化扩展保留，但默认 α=β=1、γ=0、"
        "priority weighting=none，必须逐分配退化为经典 PF；现场 EPF 定义未冻结前不冒充标准算法。</p>",
    )
    body += "<p class=source-row>载波栅格：" + source_ref("src/superran/carrier.py", "class CarrierGrid") + " · 排序/计划/记账：" + source_ref("src/superran/experience.py", "potential[i] = (") + " · " + source_ref("src/superran/experience.py", 'accounting == "scheduled_tbs"') + "</p>"
    return Page(
        "experience", "体验模式调度与 PF 记账", "系统仿真", "EXPERIENCE_V2",
        "逐 TTI 的 PF、SU/MU plan、按需 RBG 和 R_avg 正确记账。", body,
        ("PF", "R_avg", "RU", "按需RBG", "scheduled_tbs", "QoS-PF"),
    )


def traffic_page() -> Page:
    body = traffic_kpi_svg()
    body += """
<h2>话务由包大小与包间隔共同定义</h2>
<p>经验 CDF 文件使用 <code>value,cdf</code> 两列；分别对 packet size 和 inter-arrival 做逆变换采样。
所有用户可共享一个 profile，也可按 <code>ue_ids</code> 映射到 video/XR/FTP 等不同 profile。</p>
""" + code(r'''traffic:
  model: cdf
  profiles:
    - name: video
      packet_size_cdf: presets/traffic/video_size.csv
      inter_arrival_cdf: presets/traffic/video_interval.csv
      packet_size_scale: 0.5
      inter_arrival_scale: 1.0
      ue_ids: [0, 1, 2, 3]
    - name: xr
      packet_size_cdf: presets/traffic/xr_size.csv
      inter_arrival_cdf: presets/traffic/xr_interval.csv
      packet_size_scale: 1.0
      inter_arrival_scale: 0.5
      ue_ids: [4, 5]
''', "yaml")
    body += table(
        ["旋钮", "业务量效果", "同时改变什么"],
        [
            ("packet_size_scale ×0.5", "平均 offered bytes 约减半", "包完成所需 RBG、padding、busy period"),
            ("inter_arrival_scale ×0.5", "来包约加密一倍", "并发队列、MU 触发概率、首包等待"),
            ("用户数增加", "总 offered load 增加", "多用户分集、PF 竞争与 pair 候选"),
            ("UE profile mix", "改变小/大包比例", "RBG 占用直方图与用户级公平性"),
        ],
    )
    body += """
<h2>按目标 PRB 利用率校准</h2>
<p><code>target_prb_utilization=0.30</code> 不是把结果字段硬写成 30%。校准器用公共随机数重复运行，
调整包大小与包间隔标量，直到测得利用率进入容差；随后用独立正式重复实验验证。失败时保留实测值并
报告未达标，不能回填目标。</p>
"""
    body += callout(
        "warn", "30% 是场景，不是算法常数",
        "<p>10%/30%/50% 常用于轻/中/重载；MU 研究常看 50%，日常体验常聚焦 30%。"
        "最终 PRB 利用率由 CDF、标量、用户数、无线条件、调度和空包共同决定。"
        "话务校准必须与算法 A/B 分离：先校准 baseline scene，再用同一 offered process 比算法。</p>",
    )
    body += """
<h2>为什么 mixed 才能看出按需分配收益</h2>
<p>全大包时每人都需要 17 RBG，按需分配退化成全带；全小包时缺少大流量体验对象，收益难落到
体验速率。mixed 让小包不再偷走整个 TTI，同时保留大包用户作为体验速率测量对象，RBG 占用呈
0/1 与 17 两端高、中间低。</p>
"""
    body += "<p class=source-row>CDF 合同：" + source_ref("src/superran/traffic.py", "class EmpiricalCdf") + " · 话务配置：" + source_ref("src/superran/system.py", "class TrafficConfig") + "</p>"
    return Page(
        "traffic", "话务模型与 PRB 负载校准", "系统仿真", "TRAFFIC",
        "包大小/间隔 CDF、多 profile、双标量校准与 mixed 话务物理意义。", body,
        ("CDF", "包大小", "包间隔", "30% PRB", "mixed", "校准"),
    )


def kpi_page() -> Page:
    body = traffic_kpi_svg()
    body += """
<section data-kpi-workbench="standard-output">
<h2>KPI 工作台是体验仿真的标准交付物</h2>
<p>当 <code>evaluation_mode="experience"</code> 时，数值结果完成后会自动生成一份自包含、可离线打开的
HTML 工作台，并把 <code>html_path</code>、可用时的 loopback <code>url</code>、双 Tab、支持的 KPI 清单和
本次排序证据一起放进 <code>result["kpi_view"]</code>。页面生成失败不会吞掉仿真结果，但会显式返回 error，
因此交付者不能在没有页面的情况下假装工作台已经生成。</p>
</section>
"""
    body += real_ui_screenshot(
        "kpi-workbench-cell.png",
        "SuperRAN 单算法 KPI 工作台小区级真实截图，包含 Agent 关注项、置信区间、PRB 与下载分享操作栏",
        "真实单臂小区级页签：用于先判断一组结果自身是否可信，再进入多算法配对比较。",
    )
    body += real_ui_screenshot(
        "kpi-workbench-user.png",
        "SuperRAN KPI 工作台用户级真实截图，左侧逐 UE 误差棒，右侧跨 UE 经验 CDF",
        "真实用户级页签：每个指标同时给逐 UE 95% 区间和跨 UE 经验 CDF，颜色区分 video/XR profile。",
    )
    body += callout(
        "good", "真实工作台浏览器烟测已通过",
        "<p><code>scripts/run_kpi_browser_qa.py</code> 用合成 6 UE video/XR CDF 运行 8 次重复，"
        "把 50% 目标校准到实测 <strong>49.79%</strong>（容差 ±4%），再生成真正的双 Tab 工作台。"
        "隔离 Chromium 在 1440×900 与 375×812 下均为 0 px 页面级溢出、0 个控制台错误；"
        "默认小区级，点击用户级后面板互斥切换正确，并检测到 23 个有数据的用户指标面板；"
        "完整 JSON、两份 CSV 与整页 SVG 截图均由浏览器实际下载并解析。"
        "完整页面、截图、校准轨迹与逐项检查写在 "
        "<code>output/kpi-browser-qa.json</code>。该烟测证明呈现与统计合同，不代表生产话务 CDF 或现场收益。</p>",
    )
    body += """
<h2>多算法对比是主场景：Tab 按问题分，不按算法分</h2>
<p>典型实验同时包含 1 个基线和 1~4 个候选。若把每个算法放进独立 Tab，读者查看候选时看不到基线，
只能凭记忆比较；因此算法在全页保持固定颜色与可见性开关，基线始终固定。顶层六个 Tab 回答不同问题：
总览看绝对量和置信区间，KPI 矩阵看跨指标取舍，用户分布看边缘与公平，TTI 趋势找分叉时刻，单 TTI
解释机制，统计门禁决定能否发布胜负结论。</p>
<p class="source-row">交互取舍参考：
<a class="src" href="https://docs.wandb.ai/models/runs/compare-runs" target="_blank" rel="noreferrer">W&amp;B baseline/pinned runs</a> ·
<a class="src" href="https://docs.wandb.ai/models/app/features/panels/line-plot" target="_blank" rel="noreferrer">W&amp;B multi-run line plots</a> ·
<a class="src" href="https://grafana.com/docs/grafana/latest/visualizations/dashboards/variables/" target="_blank" rel="noreferrer">Grafana shared variables</a> ·
<a class="src" href="https://grafana.com/docs/grafana/latest/visualizations/explore/trace-integration/" target="_blank" rel="noreferrer">Grafana metric→trace drill-down</a> ·
<a class="src" href="https://plotly.com/javascript/plotlyjs-events/" target="_blank" rel="noreferrer">Plot click/hover event contract</a>。
SuperRAN 取其“固定基线、跨 panel 共用筛选、从趋势点进入明细”的结构，但统计判决仍使用本项目 Gate，而非照搬 ML 平台口径。</p>
"""
    body += real_ui_screenshot(
        "kpi-workbench-comparison.png",
        "SuperRAN 三算法 KPI 对比工作台真实截图，固定基线与候选颜色、六个问题型页签和带置信区间柱状图",
        "真实 Edge/Chromium 截图：经典 PF、QoS-PF 与 RR 同屏；截图中的合成结果只验证 UI/证据合同，不代表算法收益。",
    )
    body += real_ui_screenshot(
        "kpi-workbench-tti-drilldown.png",
        "SuperRAN 同一 TTI 三算法并排复盘真实截图，展示 RBG、UE、MCS/rank、SINR、BLER 与 ACK",
        "同一绝对 TTI 并排：未采样与真实 idle 严格区分；单 TTI 只解释算法为何分叉，不代替 Gate 3。",
    )
    body += table(
        ["对比 Tab", "主要图形/控件", "回答的问题"],
        [
            ("总览", "算法 KPI 卡 + 分组柱形图 + 95% CI", "各算法绝对表现是多少；不能肉眼拿单臂 CI 判断差值"),
            ("KPI 矩阵", "算法列 × KPI 行", "容量、体验、时延、资源、可靠性之间是否存在取舍"),
            ("用户分布", "每算法一条跨 UE 经验 CDF", "优化是否只照顾均值，边缘 UE 是否恶化"),
            ("TTI 趋势", "固定颜色折线 + 均匀锚点 + 关键事件点", "算法从哪个 TTI 开始分叉；点击点进入详情"),
            ("单 TTI", "同一 TTI 算法卡 + grant 明细", "候选/调度、RBG、MCS/rank、SINR、BLER draw、OLLA/PF 到底哪里不同"),
            ("统计门禁", "配对差值 CI + Wilcoxon + Holm", "在多候选场景下，哪条结论真的允许发布"),
        ],
    )
    body += code(r'''# 必须在生成数据前锁主 KPI 与基线；否则页面只允许标“探索性”
prereg = sr_lock_analysis(
    primary_metric="cell_experienced_mbps", metric_unit="Mbps",
    baseline="经典 PF", higher_is_better=True,
)
dataset = sr_generate(..., prereg_id=prereg["prereg_id"])

# 每个算法臂用同一 dataset / seed / replication，只改变预注册的算法参数
base = sr_system_sim(..., scheduler="pf", algorithm_label="经典 PF")
qos  = sr_system_sim(..., scheduler="qos_pf", algorithm_label="QoS-PF 候选")
rr   = sr_system_sim(..., scheduler="rr", algorithm_label="Round Robin")

comparison = sr_compare_system_results(
    result_ids=[base["kpi_view"]["result_id"],
                qos["kpi_view"]["result_id"],
                rr["kpi_view"]["result_id"]],
    baseline_result_id=base["kpi_view"]["result_id"],
    primary_kpi="cell_experienced_mbps",
)
print(comparison["url"] or comparison["html_path"])
''', "MCP tool sequence")
    body += callout(
        "good", "三算法比较与 TTI 钻取已过真实浏览器 QA",
        "<p><code>scripts/run_kpi_compare_browser_qa.py</code> 用同一合成信道、同一批 8 个 RngRun "
        "运行 PF/QoS-PF/RR。桌面与 375 px 手机均检测到 6 个 Tab、3 条用户 CDF、3 条 TTI 轨迹、"
        "同一 TTI 的 3 张算法卡与 grant 表；完整比较 JSON、算法×KPI CSV、TTI/grant CSV 均实际下载解析，"
        "页面级横向溢出 0 px、控制台错误 0。该演示没有生成前预注册，因此统计页正确保持"
        "<code>exploratory_unregistered</code>，不会因图好看而发布胜负。证据在 "
        "<code>output/kpi-compare-browser-qa.json</code>。</p>",
    )
    body += table(
        ["工作台区域", "默认承载", "为什么不能只看小区均值"],
        [
            ("Agent 关注横幅", "kpi_focus/intent、命中标签、排序来源与理由", "读者能审计为何这些 KPI 位于首屏"),
            ("小区级 Tab", "26 项已登记 KPI、95% CI、负载表、0..17 RBG 分布、MU/HARQ/话务画像", "回答整体容量、体验、资源和可靠性"),
            ("用户级 Tab", "24 项已登记 KPI、逐 UE 图、跨 UE 经验 CDF、全量明细", "暴露边缘 UE、饿死、覆盖不足和 profile 差异"),
            ("折叠证据", "其余 KPI、公式口径、告警与 Result JSON", "自适应展示只重排，不删除或重算不利证据"),
        ],
    )
    body += code(r'''result = sr_system_sim(
    dataset_id=dataset_id,
    evaluation_mode="experience",
    traffic_model="mixed", duration_s=5.0, warmup_s=1.0,
    target_prb_utilization=0.30, num_replications=8,
    kpi_intent="关注首包、边缘体验、PRB 利用率和用户差异",
)
page = result["kpi_view"]
print(page["url"] or page["html_path"])
print(page["kpi_selection"])  # 优先/折叠顺序及其理由，完整可审计
''', "MCP tool sequence")
    body += """
<h2>体验速率、首包时延与含头速率</h2>
""" + F_FIRST_PACKET + F_BUSY_RATE
    body += """
<p>首包时延对每个 arrival object 记录“生成 → 第一次实际调度”的等待。掐头去尾速率从第一次
发送开始计时，并按 DRB busy-period 规则排除最后一次排空 piece；含头速率使用相同分子，但
分母从首个 arrival 开始，因此把首包等待纳入体验。小 burst 可用 fractional-slot 口径，不能把
1,500 B 除以完整 0.5 ms 后称为用户体验。</p>
<h2>PRB 利用率与 0..17 RBG 分布</h2>
""" + F_PRB_UTIL
    body += table(
        ["KPI", "分子", "分母/样本", "边界"],
        [
            ("serving_cell_prb_utilization", "测量窗每 TTI 实际占用 RBG×slot_fraction", "可用 17 RBG×D/S slot_fraction", "只算本小区；full buffer≈100%"),
            ("TTI occupancy 0..17", "恰占 k 个物理 RBG 的 TTI 数", "所有测量窗 DL/S TTI", "0 必须入直方图；不是 per-grant 大小"),
            ("MU share of used", "生效 MU 的 PRB-equivalent", "已用 PRB-equivalent", "用户确认口径；共享 MU RBG 只计一次"),
            ("MU utilization", "生效 MU 的 PRB-equivalent", "全部可用 PRB-equivalent", "另一个辅助 KPI，不替代上一项"),
        ],
    )
    body += """
<h2>单臂仍保留小区级与用户级两个 Tab</h2>
<p>单次算法结果的小区页展示体验/吞吐、首包/PDB、资源、MCS/rank/BLER 与负载；用户页提供按 UE 柱图、跨 UE
经验 CDF 和明细表。MU 资源同时提供 <em>grant exposure</em>（每个配对 UE 都看到完整共享 RBG）
和 <em>attributed PRB</em>（配对 UE 等分，跨 UE 可加）以避免资源对账混乱。</p>
<h2>Agent 自适应编排，不在库内暗调 LLM</h2>
<p>调用本工具的 Agent/LLM 根据用户问题传 <code>kpi_focus</code>；库内只做可审计的 tag/关键词与
场景兜底排序，并返回 <code>source / tags / reasons / full_order</code>。排序只影响首屏，所有可用
KPI 仍保留在折叠区和结果 JSON。这保留 agent 式灵活性，也避免结果页偷偷改变数值。</p>
"""
    body += callout(
        "good", "例：用户问 MU 为什么没收益",
        "<p>Agent 可优先传 <code>[mu_paired_prb_share_of_used, mu_bler_first_tx, "
        "serving_cell_prb_utilization, payload_fill_ratio]</code>；页面先呈现“MU 是否真正生效、"
        "MU BLER 是否恶化、负载是否足够、padding 是否吃掉理论收益”，其余体验/CDF 仍可展开。</p>",
    )
    body += """
<h2>统计窗与覆盖率</h2>
<p>首包时延只对在测量窗内实际获得首次调度的 arrival 可观察，因此必须同时报告
<code>first_packet_delay_observed_share</code>；未完成/过期 arrival 进入 PDB miss 分母，避免只统计
成功样本的幸存者偏差。所有 KPI 都要标 warmup、测量窗和 replication 聚合方式。</p>
"""
    body += "<p class=source-row>单臂页面：" + source_ref("src/superran/kpi_view.py", "CELL_KPIS") + " · 多算法比较：" + source_ref("src/superran/kpi_compare.py", "def build_comparison") + " · TTI 证据：" + source_ref("src/superran/experience.py", "class Allocation") + "</p>"
    return Page(
        "kpi", "体验 KPI、多算法对比与 TTI 复盘", "系统仿真", "KPI WORKBENCH",
        "单臂与 2~5 算法对比、首包/含头速率、用户 CDF、逐 TTI 钻取和配对统计门禁。", body,
        ("多算法", "首包时延", "含头速率", "用户CDF", "TTI钻取", "Holm", "KPI Tab"),
    )


def interference_page() -> Page:
    body = rb_power_svg()
    body += """
<h2>先把 S、I、N 拆对</h2>
""" + F_IOT
    body += """
<p>IoT 是干扰加噪声相对热噪声的抬升。<code>snr_dB−sinr_dB</code> 只有在两个量共享同一
信号/功率/聚合口径时才有意义；当前 first-party 后端恰好共享“预数字波束、每 RB”参考，
所以差值可作一致性旁证。项目主契约仍用同一样本的几何 SIR 与 SINR 反解 S/I/N，兼容
参考面未声明的外部/旧数据。SIR≈SINR 表示干扰可忽略，此时 IoT→0 dB，而不是无穷。</p>
<h2>邻区 PRB 负载</h2>
<p>ChannelHub 几何 SINR默认邻区都在发，相当于 100% 资源负载。系统场景通过
<code>neighbor_prb_util=η</code> 把干扰项缩为 ηI，同时保持 SIR/SINR 同口径；30% 是默认中载
场景，不是所有网络的事实。</p>
<h2>功控从哪里接入干扰预算</h2>
""" + F_RB_COUPLING
    body += """
<p><code>q[c,r]</code> 同时作用于小区 c 在 RB r 上对自己 UE 的服务信号和对所有邻区 UE 的干扰。
每小区满足总功率/均值约束与逐 RB 上下界。计算路径保留 272 RB 到 MMSE SINR 后，再在线性域
聚合成 17 RBG；不能先压成中心 RB 后只改一个标量。InternalSim 与 Sionna RT 会在形成几何
预算时落下同参考面的 <code>S/N/I_k</code>，来源的 symbol 网格只对应一个 slot，因此元数据只保留
一个 slot 行，避免把 14 个 symbol 冒充 14 个 TTI。</p>
<p>profile 的生成、自动平衡、0.1…4x 边界、流间注水与每天线约束的组合，见独立的
<a href="#/powercontrol">功控自由度与逐 RB 功率耦合</a>章；本章只负责 S/I/N 与邻区活动口径。</p>
"""
    body += callout(
        "warn", "为什么 RBG0 抬升可能整体变差",
        "<p>RBG0 上本小区目标 UE 的 S 增强，但邻区 UE 的 I 也增强；总功率守恒又迫使本小区其他"
        "RBG 的 S 降低。若 RBG0 原本不是瓶颈、被调度概率低，或它造成的跨小区干扰代价大于本小区"
        "增益，整体 useful bytes/边缘体验就会下降。NEBF/PEBF 是空间维功率约束，RB power control"
        "是频域分配，两层必须作用到同一个物理 Q 后再算 SINR。</p>",
    )
    body += table(
        ["层", "对象", "守恒/约束", "错误捷径"],
        [
            ("空间", "Q[f,antenna,stream]", "总功率 P 或每天线 P/M", "只归一 W 方向却不核对物理 Q"),
            ("频率", "q[cell,RB]", "每小区跨 RB 均值/总和 + 上下界", "只改服务小区，不改它对邻区的干扰"),
            ("调度", "RBG bitmap", "物理共享 MU RBG 只计一次", "用 17-RBG 平均替代 grant subset"),
        ],
    )
    body += "<p class=source-row>IoT：" + source_ref("src/superran/interference.py", "def iot_db") + " · RB 耦合：" + source_ref("src/superran/power_control.py", "def couple_rb_power") + "</p>"
    return Page(
        "interference", "干扰、IoT 与邻区负载", "可信度", "INTERFERENCE",
        "S/I/N、邻区活动与逐 RB 信号/干扰的共同参考面。", body,
        ("IoT", "SIR", "SINR", "S/I/N", "邻区负载"),
    )


def rng_page() -> Page:
    body = """
<h2>随机数按用途分流</h2>
<p><code>RngBook(master_seed, replication)</code> 使用稳定的 stream key，把 channel、neighbor_load、
traffic、scheduler、harq 等流彼此隔离。调用顺序改变不能改变某条流；新增用途必须先注册，禁止
临时从一个全局 RNG 多抽一次。</p>
""" + code(r'''books = rng.replications(master_seed=20260811, n=8)

# A/B 两臂复用同一批 RngBook
run_a = simulate_replications(tables_a, books=books, ...)
run_b = simulate_replications(tables_b, books=books, ...)
comparison = rng.compare_replications(run_a, run_b, books_a=books, books_b=books)
''')
    body += """
<h2>master seed、replication 与 CRN</h2>
""" + F_CRN
    body += table(
        ["量", "角色", "何时改变"],
        [
            ("master_seed", "一个实验宇宙 / ns-3 RngSeed", "换整批物理/业务宇宙时"),
            ("replication", "同一配置下独立重复 / ns-3 RngRun", "估计 KPI 分布与置信区间时"),
            ("stream name", "同一重复内的随机用途", "新增独立随机机制时注册"),
            ("CRN", "A/B 第 k 次使用相同 (master,replication,stream,event index)", "公平比较算法时必须"),
        ],
    )
    body += callout(
        "good", "事件索引也必须稳定",
        "<p>HARQ 与 scheduler tie-break 预先按 <code>[TTI,UE]</code> 生成，而不是“谁被调度才抽一次”。"
        "否则 A/B 调度路径一分叉，后续随机数立即错位，公共随机数名存实亡。</p>",
    )
    body += """
<h2>两类 workers：生成样本与重复实验</h2>
<p>static <code>internal_sim</code> 的所有 worker 共享同一个 seed，并用不重叠的
<code>sample_index_offset</code> 切全局事件流；串行/并行的复信道、SINR、LSP 和 SRS 时序必须
逐样本逐位相同。旧做法给每个 worker 用不同 seed，会把一个固定 UE 几何的数据集变成多个
几何的混合分布，不能称为“统计等价”。移动轨迹、拒绝采样及尚无全局 index 的 source 当前
显式回退串行，摘要同时记录 requested/effective workers 和原因。</p>
<p><code>sr_system_sim</code> 的 <code>replication_workers</code> 是另一条轴：链路表先建一次，
随后把独立 RngRun 分给进程。默认 <code>auto</code> 以 <code>n_rep×TTI×UE</code> 粗工作量判断；
短任务串行，长任务最多 4 进程，用户也可显式设 1/2/4/8。2026-08-23 本机同一 6 UE、8 次
重复的冻结基准中，5 s 由 1.60 s 降到 0.99 s（1.61×），50 s 由 14.20 s 降到
4.49 s（3.16×）；有限 KPI exact、非有限类别一致且差异路径为空。4 线程只有 0.72~0.74×，
所以产品没有提供一个会让任务变慢的线程旋钮。</p>
<h3>先向量化，再并行</h3>
<p>逐 RB post-MMSE/IRC/ZF 原先在 Python 中循环 8×272 个小矩阵；现在把损伤协方差、
有效信道和逆矩阵交给 NumPy 批量内核。固定 rank4/4R 基准的 MMSE、IRC、ZF 分别约
9.83×、11.44×、11.34×，输出逐位一致；MRC 约 102.6×且最大绝对误差
1.34e−15。完整原始记录在 <code>artifacts/results/performance_audit.json</code>。</p>
<h3>耗时估计只用于调度，不是 SLA</h3>
<p>20-ray CDL 落地后，旧的 24 ms/样本标定失效。2026-08-11 热态锚点为：
1 cell/32T/20 MHz 约 0.158 s，1 cell/64T/100 MHz 约 1.074 s，
21 cells/16T/20 MHz 约 7.48 s；最后一组 24 样本串行/4 workers 为
179.5/49.3 s。冷态单样本又测到 1.15~3.03 s，说明初始化与缓存不能忽略。
<code>estimate_seconds()</code> 因而只做 worker 决策，实际运行必须读
<code>elapsed_s</code>。probe 的当前一组交错对照约 1.80x，也不是跨版本常数。</p>
"""
    body += """
<h2>为什么至少 6 次、默认 8 次</h2>
<p>一次系统仿真的末位数字只是一个 realization；项目对少于 6 次给硬警告，默认 8 次，并回传
均值、95% CI、标准差和 n。想分辨更小差异应做功效分析或增加 replication，不能仅靠延长单次
仿真假装获得独立样本。</p>
"""
    body += "<p class=source-row>实现：" + source_ref("src/superran/rng.py", "class RngBook") + " · 系统重复：" + source_ref("src/superran/system.py", "def simulate_replications") + "</p>"
    return Page(
        "rng", "随机数、重复实验与 CRN", "可信度", "RANDOMNESS",
        "稳定分流、RngRun 语义、事件索引和 A/B 公共随机数。", body,
        ("RngBook", "CRN", "replication", "HARQ stream"),
    )


def gates_page() -> Page:
    body = gates_svg()
    body += """
<h2>门 1 的 18 项当前清单</h2>
"""
    checks = [
        "路损对标 38.901", "CDL 表逐簇对标", "角度扩展对标", "场景/信道模型自洽",
        "小区数与配置一致", "干扰确实进入 SINR", "IoT 自洽", "基站阵列模型",
        "距离在公式范围", "路损不低于自由空间", "时延扩展与 profile", "Parseval 能量守恒",
        "SISO 退化到 Shannon", "谱效不超独立容量", "预编码排序合理", "估计误差合理",
        "蒙特卡洛收敛", "SINR 分布覆盖",
    ]
    body += '<ol class="check-grid">' + "".join(f"<li><span>{i}</span>{esc(name)}</li>" for i, name in enumerate(checks, 1)) + "</ol>"
    body += """
<h2>门 2：比较口径与统计</h2>
<p>两臂必须逐样本/逐 replication 可配对，除被测变量外配置一致；优先按独立 drop/position 聚类，
再对 cluster difference 做置信区间与 Wilcoxon。CI 跨 0、样本不足或 CRN 无法核实时，强结论阻断。</p>
<h2>门 3：可发布性</h2>
<p>生成前锁主指标/基线；报告效应绝对值、相对值、CI、n、检验和适用边界；支持性 KPI 不能替代
预注册主指标。失败时结论句必须写“不成立/证据不足”，不能用“总体来看”“趋势上”绕门。</p>
"""
    body += callout(
        "danger", "测试通过 ≠ 物理正确",
        "<p>Gate/测试能证明合同、自洽、不漂移；预置 BLER 曲线、实测 Jones 方向图、现场 CQI filter"
        "若没有独立外部数据，测试只能保护 hash/边界，不能证明模型等同真实网络。</p>",
    )
    body += """
<h2>字节与资源守恒</h2>
""" + F_CONSERVE
    body += """
<p>体验模式还逐 TTI 对账 RBG bitmap、MU 共享资源、scheduled/payload/padding、arrival/queue/ACK。
这些是系统结果可信的第一层；任何一项不守恒都应硬失败，而不是在 KPI 汇总时“修平”。</p>
"""
    body += "<p class=source-row>18 项列表：" + source_ref("src/superran/validate.py", "def full_report") + " · 三门统计：" + source_ref("src/superran/gates.py", "def paired_compare") + "</p>"
    return Page(
        "gates", "三道门与统计结论", "可信度", "EVIDENCE GATES",
        "18 项数据体检、配对/聚类统计与可发布结论边界。", body,
        ("Gate1", "Gate2", "Gate3", "Wilcoxon", "置信区间", "守恒"),
    )


def external_results_page() -> Page:
    body = external_contract_svg()
    body += """
<h2>为什么外部算法不能靠 MCP exec 接入</h2>
<p>自研 CSI、预编码、估计或调度算法运行在用户自己的 Python 进程里；MCP 只交付数据句柄和一份
可直接运行的评测模板，再接收标准化 ResultArtifact。这样服务端既不成为任意代码执行面，也不把
逐样本大数组塞进 JSON。模板本身用 <code>h_est</code> 设计、<code>h_true</code> 评价，先不修改也能跑通
SVD vs Type-I 全链路。</p>
<h2>生成数据之前锁住主指标与基线</h2>
""" + F_PREREG_DIGEST + F_PREREG_CLASS
    body += """
<p><code>analysis.lock()</code> 每次生成新的不可原地修改 prereg 文件；<code>sr_generate</code> 把其 ID
与摘要绑定进数据集 summary。换指标需要新 prereg 与新数据，不能在同一批结果上挑一个赢的再改名为
主指标。没有绑定时状态是 <code>unregistered</code>，不是默认 primary；指标方向也必须随合同保持一致。</p>
<h2>ResultArtifact 锁住数据、顺序、指标与代码版本</h2>
""" + F_RESULT_CONTRACT + F_CRN
    body += table(
        ["合同字段", "为什么存在", "失败时行为"],
        [
            ("dataset_digest", "同名 dataset_id 的 NPZ 或物理语义 summary 仍可能被替换", "摘要不同，硬阻断"),
            ("ordered sample_ids", "长度相同也可能排序或筛选不同", "报告首个错位及集合是否相同"),
            ("values_sha256", "逐样本值保存在压缩 NPZ，不进入 MCP JSON", "文件内容可复核；非有限值注册时拒绝"),
            ("code_sha256", "三个月后定位真正跑数的脚本版本", "未提供可为空，但复现证据变弱"),
            ("method_metadata", "声明算法超参和 CSI 角色", "外部进程不可观测，缺声明只能告警而不能假装查过"),
            ("prereg_id/digest", "区分 primary、secondary 与 exploratory", "摘要篡改或身份不符时 Gate 3 阻断强结论"),
        ],
    )
    body += callout(
        "danger", "统计检验无法发现样本错配",
        "<p>把 B 臂 ID 顺序平移一位，Wilcoxon 仍会对两列数字给出一个看似合法的 p 值。"
        "只有逐位置 ID 合同能知道第 i 个结果是否来自同一个信道 realization；因此该检查是门 2 的前提，不是元数据美化。</p>",
    )
    body += code(r'''template = sr_export_eval_template(dataset_id, metric="spectral_efficiency")
# 用户在自己的进程运行模板，得到 res_A / res_B；MCP 不执行其中代码
verdict = sr_compare_results(res_A, res_B)
# 只有 pairable、Gate 2/3 与 prereg identity 全部通过，statement 才可引用
''')
    body += "<p class=source-row>预注册：" + source_ref("src/superran/analysis.py", "def lock") + " · 结果合同：" + source_ref("src/superran/results.py", "class ResultArtifact") + " · 取货模板：" + source_ref("src/superran/deliver.py", "def build_code") + "</p>"
    return Page(
        "externalresults", "预注册、外部算法与结果合同", "可信度", "EXTERNAL ALGORITHM EVIDENCE",
        "用户自研代码如何在服务端不执行任意代码的前提下，进入同一配对统计与发布门。", body,
        ("preregistration", "ResultArtifact", "sample_ids", "dataset_digest", "external algorithm"),
    )


def tools_page(tools: list[SymbolDoc]) -> Page:
    groups: list[tuple[str, tuple[str, ...]]] = [
        ("发现与规划", ("sr_capabilities", "sr_system_scene", "sr_list_presets", "sr_plan", "sr_revise", "sr_list_scenes", "sr_missing_slots")),
        ("生成与交付", ("sr_generate", "sr_deliver", "sr_describe_dataset", "sr_list_datasets", "sr_spec_sheet", "sr_await_config")),
        ("可信度与统计", ("sr_validate", "sr_calibrate", "sr_gate", "sr_compare_arms", "sr_sample_size", "sr_lock_analysis")),
        ("链路与吞吐", ("sr_link_performance", "sr_throughput", "sr_mcs_info", "sr_bler_curve", "sr_tdd_mcs", "sr_sweep_snr")),
        ("干扰与场景", ("sr_interference_report", "sr_iot_convert", "sr_design_interference", "sr_probe_scenario", "sr_compare_scenarios")),
        ("外部算法", ("sr_export_eval_template", "sr_compare_results", "sr_list_results")),
        ("系统仿真", ("sr_system_sim", "sr_compare_system_results")),
    ]
    by_name = {t.name: t for t in tools}
    seen: set[str] = set()
    sections = []
    for title, names in groups:
        cards = []
        for name in names:
            tool = by_name.get(name)
            if tool is None:
                continue
            seen.add(name)
            cards.append(
                f'<details class="api tool"><summary><code>{esc(tool.name)}</code><span>{esc(tool.doc)}</span></summary>'
                f'<div><pre class="signature">{esc(tool.signature)}</pre><p>{esc(tool.doc)}</p>'
                f'<p>{source_ref("src/superran/server.py", "def " + tool.name, "server.py:L" + str(tool.line))}</p></div></details>'
            )
        sections.append(f"<h2>{esc(title)}</h2>" + "".join(cards))
    missing = [t.name for t in tools if t.name not in seen]
    if missing:
        sections.append(callout("danger", "工具分类遗漏", "<p>" + esc(", ".join(missing)) + "</p>"))
    body = metric_cards((("当前工具数", str(len(tools)), "AST 自动扫描 server.py"),))
    body += """
<p>下列签名来自当前源码，不是手写摘要。MCP 工具只返回 JSON/路径/代码，不把大型 ndarray 塞进对话；
生成数据用 dataset_id 引用，外部算法用结果契约进入统计门。</p>
""" + "".join(sections)
    body += callout(
        "note", "默认不弹浏览器",
        "<p><code>sr_spec_sheet(open_browser=False)</code> 默认只返回 URL。只有用户明确要求时传 true；"
        "KPI 结果页同样应把路径/URL作为可审计产物，而不是依赖当前桌面焦点。</p>",
    )
    return Page(
        "tools", f"{len(tools)} 个 MCP 工具", "平台接口", "MCP TOOL REFERENCE",
        "按工作流分组的全部 sr_* 签名、职责与源码入口。", body,
        tuple(t.name for t in tools),
    )


def skill_page(skills: list[dict[str, Any]]) -> Page:
    body = skill_flow_svg()
    body += """
<h2>Skill 不是提示词装饰</h2>
<p><code>channel-sim</code> 规定何时追问、计划如何收敛、门 1/2/3 何时阻断、CRN 如何保持、
系统级 A/B 如何写结论。它还规定可见计划恰为四项，避免用十几个待办制造“很专业”的错觉。</p>
"""
    rows = []
    for item in skills:
        headings = " · ".join(item["headings"][:8])
        if len(item["headings"]) > 8:
            headings += " · …"
        rows.append((
            f'<code>{esc(item["rel"])}</code>', str(item["lines"]), esc(headings),
        ))
    body += table(["文件", "行数", "承载内容"], rows, raw={0, 2})
    body += """
<h2>reference 路由</h2>
"""
    body += table(
        ["需要回答", "读取"],
        [
            ("怎样问清实验问题", "asking.md"),
            ("默认 64T4R/载波", "default-hardware.md"),
            ("Gate、统计与结论句", "gates-and-stats.md"),
            ("MCS/TBS/BLER/OLLA", "link-adaptation.md"),
            ("性能、样本数、并行", "performance.md"),
            ("压力测试与已知事故", "pressure-tests.md"),
            ("场景、干扰与 IoT", "scenarios-and-interference.md"),
            ("说明书/回传页面", "spec-sheet.md"),
            ("capacity/experience 系统仿真", "system-sim.md"),
        ],
    )
    body += callout(
        "danger", "HARD-GATE",
        "<p>未通过 Gate 时，不得直接报提升百分比，不得手算一个检验去“救”结论，不得把 notes 压成"
        "“仅供参考”。阻断是产品行为，不是写作风格。</p>",
    )
    body += "<p class=source-row>主 Skill：" + source_ref("skills/channel-sim/SKILL.md", "<HARD-GATE>") + "</p>"
    return Page(
        "skill", "channel-sim Skill 工作流", "平台接口", "AGENT WORKFLOW",
        "四阶段收敛、HARD-GATE 与全部 reference 的职责地图。", body,
        ("Skill", "HARD-GATE", "头脑风暴", "计划", "门"),
    )


def presets_page(presets: dict[str, Any]) -> Page:
    channel = presets.get("presets/presets.yaml", {})
    system = presets.get("presets/system_presets.yaml", {})
    body = metric_cards((
        ("信道预设", str(len(channel)), "拓扑 + 信道 + 测量配置"),
        ("系统场景", str(len(system)), "generate + system + evidence"),
        ("覆盖优先级", "用户 > preset > 默认", "最终 resolved_config 可审计"),
    ))
    body += """
<h2>配置不是一张扁平字典</h2>
<p>一个可复现实验由四层组成：信道预设给物理骨架；用户 overrides 改显式旋钮；
<code>plan.py</code> 把 64T4R 等人话展开为 ChannelHub 参数；系统仿真再追加话务、调度、
OLLA、MU 与 KPI 口径。最终写入数据集的是解析后的配置，不是用户输入的片段。</p>
"""
    body += code(r'''draft = sr_plan(
    intent="30% PRB 话务下比较 SU 与 MU 的用户体验",
    preset="company_64t4r_multicell",
    overrides={"channel_est_mode": "ls_mmse"},
)
# 人工确认 draft 后：sr_generate(...) -> sr_system_sim(...)
''')
    body += callout(
        "warn", "label 是意图，不是实测保证",
        "<p>预设名写“高干扰”不代表结果一定高 IoT。只有 <code>expect.measured=true</code> "
        "且带数据集、重复次数、模型版本和区间的锚点才能当证据；旧的 "
        "<code>legacy_v1_pre_physics_audit</code> 只用于历史回归。</p>",
    )

    def preset_cards(items: dict[str, Any], *, system_mode: bool) -> str:
        cards = []
        for name, item in items.items():
            item = item or {}
            label = str(item.get("label", name))
            summary = str(item.get("summary", ""))
            group = str(item.get("group", "未分组"))
            if system_mode:
                cfg = item.get("system", {}) or {}
                meta = (
                    f'channel=<code>{esc(item.get("channel_preset", "—"))}</code> · '
                    f'mode=<code>{esc(cfg.get("evaluation_mode", "—"))}</code> · '
                    f'traffic=<code>{esc(cfg.get("traffic_model", "—"))}</code>'
                )
                expect = item.get("expect", {}) or {}
                evidence = (
                    '<span class="badge ok">有实测锚点</span>'
                    if expect.get("measured") else '<span class="badge">未实测</span>'
                )
            else:
                cfg = item.get("config", {}) or {}
                meta = (
                    f'source=<code>{esc(cfg.get("source", "internal_sim"))}</code> · '
                    f'{esc(cfg.get("num_sites", 1))}站×{esc(cfg.get("sectors_per_site", 1))}扇区 · '
                    f'<code>{esc(cfg.get("channel_model", "—"))}</code>'
                )
                evidence = '<span class="badge">信道骨架</span>'
            cards.append(
                f'<details class="preset"><summary><span><small>{esc(group)}</small>'
                f'<strong><code>{esc(name)}</code> · {esc(label)}</strong></span>{evidence}</summary>'
                f'<div><p>{esc(summary) if summary else "无摘要"}</p><p class="muted">{meta}</p>'
                f'<pre class="mini-json">{esc(json.dumps(cfg, ensure_ascii=False, indent=2))}</pre></div></details>'
            )
        return "".join(cards)

    body += "<h2>信道预设全集</h2>" + preset_cards(channel, system_mode=False)
    body += "<h2>系统级场景全集</h2>" + preset_cards(system, system_mode=True)
    body += callout(
        "good", "对照组由代码校验",
        "<p><code>pair_with</code> 两边除 <code>pair_varies</code> 外必须逐字相同。"
        "这使“只改一个变量”从文案承诺变成可失败的契约。</p>",
    )
    body += "<p class=source-row>解析：" + source_ref("src/superran/plan.py", "def load_presets") + " · 系统预设：" + source_ref("src/superran/sysscenes.py", "def check_pairs") + "</p>"
    return Page(
        "presets", "配置、预设与场景契约", "平台接口", "CONFIGURATION",
        "全部信道/系统预设、覆盖规则、历史锚点边界与成对场景约束。", body,
        tuple(channel) + tuple(system) + ("resolved_config", "pair_with"),
    )


def extension_page() -> Page:
    body = """
<h2>扩展点的共同原则：先定义窄腰，再接入口</h2>
<p>新增功能不能只在 MCP 工具里“能调用”。它至少要同时有：数据合同、实现、反向测试、
可发现入口、Skill 路由、文档和发布边界。下面五条路径覆盖最常见扩展。</p>
"""
    body += steps((
        ("新增链路/调度算法", "<p>在独立模块实现纯函数或 dataclass；输入只取 Dataset/Phase-A 表，输出带版本和诊断。把算法挂进 profile 选择，不改基线默认。</p>"),
        ("新增 KPI", "<p>先写分子/分母、统计窗口、用户级与小区级聚合，再把字段加入 <code>ReplicationResult</code> 与 <code>kpi_view.py</code>；展示优先级不得改变数值。</p>"),
        ("新增 MCP 工具", "<p>在 <code>server.py</code> 增加公开 <code>sr_*</code>；返回 JSON/句柄，不返回 ndarray；补 server 测试并重建本页，工具数自动更新。</p>"),
        ("新增随机机制", "<p>先 <code>register_stream(name,purpose)</code>，再按稳定事件索引取 RNG；A/B 两臂必须能核验同 stream fingerprint。</p>"),
        ("新增场景/预设", "<p>预设写设计意图；只有跑过 Gate 与重复实验后才写 <code>expect.measured=true</code>。对照组声明 <code>pair_with/pair_varies</code>。</p>"),
    ))
    body += """
<h2>toy example：加一个用户级 jitter KPI</h2>
""" + code(r'''# 1) 口径：只在 KPI window 内，以每个用户成功 ACK 间隔计算
@dataclass
class UserKpi:
    ack_gap_jitter_ms: float

# 2) 仿真：记录原始 ACK 时间，最后统一聚合；不要在循环里滚动“修平”
gaps = np.diff(user_ack_times_s) * 1e3
value = float(np.std(gaps, ddof=1)) if len(gaps) >= 2 else np.nan

# 3) 展示：CELL_KPIS/USER_KPIS 注册定义、单位、方向和可画 CDF 属性
# 4) 测试：恒定间隔 -> 0；少于 2 个 gap -> NaN；warm-up ACK 不得进入
''')
    body += callout(
        "decision", "Agent 式展示的边界",
        "<p>LLM 可以根据用户问题重排 KPI、解释为什么优先；不能删掉不相关 KPI 的原始结果，"
        "不能改指标定义，也不能让同一仿真因提示词不同而产生不同数值。当前实现把排序证据与"
        "完整 KPI 一起写入 HTML/JSON。</p>",
    )
    body += table(
        ["扩展", "必须新增的哨兵", "最常见错误"],
        [
            ("算法", "基线退化 + 极端反例", "只测‘能跑’，不测方向"),
            ("KPI", "手算 toy trace", "统计窗口或分母漂移"),
            ("工具", "签名/工具数/JSON 可序列化", "把大数组塞回对话"),
            ("随机流", "调用顺序不变性", "从全局 RNG 临时多抽一次"),
            ("预设", "pair contract + Gate", "把 label 当实测结论"),
        ],
    )
    body += "<p class=source-row>KPI 页面：" + source_ref("src/superran/kpi_view.py", "def render_html") + " · RNG 注册：" + source_ref("src/superran/rng.py", "def register_stream") + " · 外部结果：" + source_ref("src/superran/results.py", "def register") + "</p>"
    return Page(
        "extension", "如何扩展而不破坏可信度", "平台接口", "EXTENSION GUIDE",
        "算法、KPI、工具、随机流和预设的端到端扩展清单。", body,
        ("extension", "KPI", "MCP", "register_stream", "preset"),
    )


def tests_page(tests: list[dict[str, Any]], modules: list[ModuleDoc]) -> Page:
    lines = sum(item["lines"] for item in tests)
    checks = sum(item["check_sites"] + item["assert_sites"] for item in tests)
    covered, exempt, missing = detailed_module_coverage(modules)
    body = metric_cards((
        ("测试文件", str(len(tests)), "tests/test_*.py 自动扫描"),
        ("测试代码", f"{lines:,} 行", "当前工作树"),
        ("静态断言点", str(checks), "check(...) + assert；非运行总数"),
        ("主题章节覆盖", f"{len(covered)}/{len(modules)} 模块", f"{len(exempt)} 个基础设施显式豁免"),
    ))
    body += """
<h2>快速内环与重型验收</h2>
<p>测试文件不是同一种成本：公式/合同测试适合每次改动跑；信道生成、浏览器与多重复压力测试
用于阶段性验收。不要把“某次运行通过 1,227 项”写成永恒事实；测试总数由参数化和环境决定。</p>
"""
    body += """
<h2>代码能力到详细章节的反向覆盖</h2>
<p>生成器读取每章的 <code>source_paths</code>，要求除显式基础设施豁免外，每个
<code>src/superran/*.py</code> 至少被一个详细章节点名。它不是按文件名凑页数：相关模块先聚合成
能力簇，再用源码入口、物理边界、反例和测试共同承载。新增模块若没有进入任何主题章，构建会直接失败。</p>
"""
    body += table(
        ["本轮补齐的能力簇", "此前隐藏在哪里", "现在的独立章节", "关键审计边界"],
        [
            ("Agent 决策与说明书闭环", "decisions / plan / algorithms / algo_defs* / spec / bridge", '<a href="#/agentloop">决策引擎、算法目录与说明书闭环</a>', "确定性关键词路由；单一 resolved config；loopback 白名单回传"),
            ("射线追踪与场景探测", "scenes / scenario / channelhub", '<a href="#/raytracing">射线追踪、场景资产与快速探测</a>', "channel_generation_mode 判真；资产只读；probe 不可算 PDP/SE"),
            ("参考信号与物理基线", "physical / hardware / csi_aging", '<a href="#/referencesignals">参考信号、TDD 与波束扫描</a>', "38.104 RB 表；SRS 周期；CSI-RS DFT 不等于 PMI"),
            ("BLER 与有效 SINR", "linkadapt / bler_curves / bler_data_20b", '<a href="#/bler">BLER：MCS 表、曲线与 HARQ 复现</a>', "MIESM/EESM 尚未进入体验链；预置曲线不是 3GPP 曲线"),
            ("外部算法证据合同", "analysis / results / deliver", '<a href="#/externalresults">预注册、外部算法与结果合同</a>', "MCP 不执行外部代码；摘要与有序 sample_ids 先于统计"),
        ],
        raw={2},
    )
    infra = ", ".join(f"<code>{esc(name)}.py</code>" for name in sorted(exempt))
    body += callout(
        "note", "哪些文件没有独立无线章节",
        f"<p>{infra} 只负责包入口与公式渲染，已由全量 API 图谱和文档构建测试承载。"
        "它们不是被静默遗漏；若未来加入实验语义，必须移出豁免并进入主题章。"
        + (f" 当前仍缺：{', '.join(sorted(missing))}。</p>" if missing else " 当前没有未分类模块。</p>"),
    )
    module_chapters: dict[str, list[str]] = {module.name: [] for module in modules}
    for chapter_key, spec in DETAIL_SPECS.items():
        for source_path in spec.source_paths:
            if source_path.startswith("src/superran/") and source_path.endswith(".py"):
                module_name = Path(source_path).stem
                if module_name in module_chapters and chapter_key not in module_chapters[module_name]:
                    module_chapters[module_name].append(chapter_key)
    mapping_rows = []
    for module in modules:
        chapter_links = " ".join(
            f'<a href="#/{esc(key)}"><code>{esc(key)}</code></a>'
            for key in module_chapters[module.name]
        )
        if module.name in exempt:
            status = '<span class="badge">显式基础设施豁免</span>'
            chapter_links = chapter_links or '<a href="#/api"><code>api</code></a>'
        else:
            status = '<span class="badge ok">主题章已承载</span>' if chapter_links else '<span class="badge">缺失</span>'
        mapping_rows.append((f'<code>{esc(module.name)}.py</code>', chapter_links, status))
    body += (
        '<details class="api"><summary><code>module → chapter</code>'
        '<span>展开查看全部 43 个模块的主题章归属</span></summary><div>'
        + table(["模块", "详细章节", "状态"], mapping_rows, raw={0, 1, 2})
        + "</div></details>"
    )
    rows = []
    for item in tests:
        purpose = " · ".join(item["sections"][:4]) or "以文件内断言为准"
        rows.append((
            f'<code>{esc(item["name"])}</code>', str(item["lines"]),
            str(item["check_sites"]), str(item["assert_sites"]), esc(purpose),
            source_ref(item["rel"], "", "源码"),
        ))
    body += table(
        ["文件", "行", "check", "assert", "章节/职责", "入口"], rows,
        raw={0, 4, 5},
    )
    body += """
<h2>本次全项目审计中当场修复的纰漏</h2>
"""
    audit_rows = [
        ("ChannelHub 跨站传播状态", "一个 UE 级 LOS/DS/SF 被复制到所有小区；扇区反而换 seed", "按 physical site 独立抽样；同站扇区共享状态与 cluster seed，方位角作用于阵列", "MSG-Platform 21/21"),
        ("自定义站点三扇区", "custom positions 永远只建 sector 0", "按 0/120/240° 展开，2站×3扇区 toy case 固定为 6 cells", "契约测试"),
        ("扇区服务选择", "azimuth_deg 不进 path gain，三扇区同功率、按列表先后胜出", "110° 水平阵子图给相对 sector gain；pathloss 保持纯传播量", "boresight 反例"),
        ("SRS 时序", "样本 idx 直接当 slot；可在 DL/guard slot 合成 SRS", "idx 映射到第 n 个满足 TDD+T_SRS+offset 的真实机会；无交集硬失败", "paired 3→13 slot toy"),
        ("SRS 带宽与跳频", "ChannelHub 只硬编码 C_SRS 0..17，默认 row 3；多级 F_b 有空循环且混淆 n_RRC/n_shift", "补全 64 行 38.211 表，分离 freqDomainPosition/freqDomainShift，奇偶 N_b 逐式实现；公司预设冻结 63/1/0、20 slot", "64 行×各 B_SRS/b_hop 穷举 + 17 跳覆盖 272 RB"),
        ("小载波 SRS 默认值", "Sionna/QuaDRiGa 固定 C_SRS=3；4 RB toy carrier 在历史 hopping 回看时映射到 RB[8,12) 并崩溃", "四种 source 均按实际载波自动选最宽合法 C_SRS；显式非法资源仍硬失败", "跨 backend 86 passed / 1 conditional skip"),
        ("CDL 标准表校准", "旧 A/B/C 角度错、D/E 行数短；新 dataclass 字段又让兼容覆盖 TypeError，异常被吞后继续生成", "MSG A~E 源表直接修正；兼容层只写已支持字段；shape mismatch 全表判错且校准异常阻断生成", "A/B/C/D/E 分别 23/23/24/14/15 行，逐字段 0 mismatch"),
        ("CDL ray 与 LOS", "每簇只生成一个 rank-1 方向，忽略 20-ray spread/XPR；D/E 又二次混 K；显式 UMa_LOS 仍随机出 NLOS", "20-ray 偏移/角耦合/逐 ray Jones+Doppler；D/E K 只用表功率；显式 LOS 强制 LOS/CDL-D", "CDL 定向 19/19 + LOS 反例"),
        ("配置/实际剖面", "摘要只突出 configured CDL-D，但 NLOS 链路实际由 CDL-C 生成，24-component 结果容易被误读成 D", "新增 configured_channel_model；repr、摘要与 E2E 同时展示 effective_channel_model_counts", "NLOS configured D→effective C 反例"),
        ("TDL/阵列链路预算", "TDL 缺少实际 ZOD/ZOA/Jones；有效阵子峰值和电下倾未进入 conducted link budget", "TDL LOS 接实际几何与 Jones；element×subarray absolute gain 进入预算，数字 BF 单独计算", "方向性功率、下倾与 physical-reference 等价哨兵"),
        ("CSI-RS DFT 码本", "physical.dft_codebook() 导入不存在的 csirs_precoding，真实调用直接崩", "补 2D oversampled DFT 码本、明确端口顺序；选 beam 时先算功率再跨时频平均", "8H×4V×2pol → (512,64)，unit norm"),
        ("LMMSE 路径", "只在 compact pilot grid 上平滑，再线性补洞；非均匀 SRS 不是 LMMSE；模式名漂移", "直接 R_tp(R_pp+R_v)^−1 从真实 pilot 映射所有目标 RB；公开配置 canonical 为 ls_mmse，ls_lmmse 是精确 alias", "非均匀位置 + 匹配先验 Monte Carlo + 高 SNR 极限"),
        ("业务域/测量域 SIR", "paired 估计完成后用 pilot-domain best SIR 覆盖 sir_dB，业务干扰画像被悄悄换域", "sir_dB 永久保留业务几何聚合；ul_sir_dB/dl_sir_dB 只承载导频估计域；metadata 声明 domain contract", "InternalSim + Sionna paired 反例"),
        ("总载波功率到每 RB", "tx_power_dbm 是全载波总功率、noise 是单 RB kTB+NF，但旧 SNR 漏减 10log10(N_RB)，273 RB 高估 24.36 dB", "两后端统一 P_RB=P_total/N_RB 并落 per_rb_tx_power_dbm；4→8 RB 精确下降 3.0103 dB", "两后端单元测试 + full/probe 解析重构"),
        ("链路级工作点锚点", "把几何 SINR 锚到 rank-1 σ₁²，抵消了 H 中数字 BF 增益；64T 权的真实增益被归一化抹掉", "first-party 标量明确为预数字波束每 RB；以 E[|H|²] 反标 I+N，rank-1 σ₁² 仅保留为诊断", "linklevel/MU/物理不变量反例"),
        ("几何 SINR 锚点测试", "旧断言把 rate-equivalent SINR 与线性功率域几何 SINR直接比较；20-ray 频选增强后 Jensen gap 被误报成重复 BF", "以逐 RB SINR 转线性后的均值核对几何锚点；另锁定 rate-equivalent 不高于功率均值", "实测锚点误差 0.014 dB，Jensen gap 0.335 dB"),
        ("逐小区 S/N/I 契约", "旧 _system_sinr 移除后，RB 功控依赖的 dl_signal/noise/interference metadata 一并消失，单元算法虽绿但真实数据无法启用", "InternalSim/Sionna 在同一几何预算落每 RB S/N/I_k；一个 symbol 网格只写一个 slot 行；缺字段仍硬失败", "source→NPZ→loader 重构误差 3.6e−15 dB"),
        ("Doppler 投影", "先把速度投影到最近站径向，CDL 再按每 ray 方向余弦投影，方向作用两次", "metadata 交付 f_max=|v|/λ 与完整速度方向，CDL 每 ray 只投影一次；static 仅冻结跨 snapshot 几何", "350 km/h @ 2.6 GHz = 842.59 Hz"),
        ("static Doppler 测试夹具", "预设没写 ue_speed_kmh 时，测试把缺失键当成 0；但 InternalSim 的公开默认是 3 km/h，实际 9.72 Hz 被误报为实现失败", "反例显式固定 static + 36 km/h，以 f_max=|v|/λ 核对；不再从可漂移的预设缺省推断期望", "完整 interference 回归 1002 s 全绿"),
        ("paired UL 天线轴测试", "旧单测期待公开 h_ul_true 保持物理 [UE,BS] 转置布局；实际 ChannelSample 窄腰早已统一为 [BS,UE]，把正确输出误判失败", "锁定两层语义：内部物理 H_UL=H_DL^H；返回时恢复 canonical [T,RB,BS,UE]，零校准误差时数值为 conj(H_DL)", "interference/bridge/mobility/export 定向 41 passed / 1 ONNX skip"),
        ("track 移动性测试", "MOBILITY_MODES 新增 track 后，两个遍历全模式的旧测试未传必需 waypoints，因 ValueError 失败", "为 track 夹具给显式两点轨道；形状与高度守恒继续覆盖全部模式", "test_mobility 定向回归"),
        ("Sionna RT 时变", "Receiver.velocity 未设置且 Paths.cfr 默认 1 Hz 采样，多个 symbol 实为静态重复；频率网格还从 0 单边展开", "写完整 UE 速度，CFR 采样率=1/平均 OFDM symbol 周期，RB 频率以载波中心对称", "真实 Munich RT symbol 演进反例"),
        ("Probe SRS 资源", "全带显式 C_SRS=63 覆盖 272 RB，直接塞进 24-RB probe 后越界", "probe-only 重新选最宽合法标准资源并报告 63→7；正式生成仍对显式非法配置硬失败", "company_64t4r probe 回归"),
        ("系统时间轴", "14 symbol 被误当 14 个 TTI 落盘", "14 symbol 先完成估计，再取中间 symbol 为 1 slot snapshot；禁复数平均", "64×4 E2E"),
        ("TDD 系统载波", "通用 Type-0 工具被直接暴露给系统入口，使 51/106/273 RB 也会自动变成新的系统口径", "对外冻结 100 MHz@30 kHz、272 RB=17×16；张量宽度与标签任一不符就硬失败；通用工具仅保留为内部数学能力", "fixed-profile 合同 + UI 无 num_rb/rbg_size_config 控件"),
        ("UL→DL 互易映射", "若跟随外部 helper/w_dl 的轴序与共轭约定，数据源演进可悄悄改变发射权", "SuperRAN 版本化 [time,rb,bs,ue] 合同，h_precoding_est=conj(h_ul_est)；新数据忽略 source w_dl，权值由本地 EBF/PEBF/NEBF 重算", "复数逐位哨兵 + 轴形状反例 + source-w_dl 忽略测试"),
        ("SRS 17-hop 所有权", "老化模型运行时调外部 srs_rb_indices，依赖失败时还可退回恒等扫描", "SuperRAN 固化 C_SRS=63/B_SRS=1/b_hop=0 的 0,8,16,...,1,9 序列；只接受 17×16，无外部 helper、无 fallback", "17 跳不重不漏 + 非标 profile 硬失败"),
        ("PF 平均速率", "按需 1 RBG 用户若用全带 best_se 记账会被约 17×过罚", "默认用实际 scheduled TBS credit，ACK bytes 只作独立 KPI", "确定性反控：小包均值等待 +0.9525 ms、P95 +9.5 ms；真实 mixed 差异为 0 并被 Gate 3 拦截"),
        ("比较样本独立性", "80 个 snapshot 被直接当 80 个独立统计样本，但实际只有 10 个 UE 位置；实现修正后，test_gates 的旧断言仍期待 raw n=30", "内置 compare_arms 先按 UE position 聚类；回归改为期待 10 个独立位置，并反向断言 n 必须小于 30 个快照", "Hello World 80→10；Gate E2E 30→10；主实验 CI 跨零、Wilcoxon p=0.846"),
        ("并行 worker 上限", "请求 worker 数超过 UE batch 数时会产生空块/额外 spawn，并让并行摘要看起来像真的使用了全部 worker", "worker 数钳到实际 UE batch 数并写 parallel.cap_reason，不改变样本、seed 或排序", "worker cap 合同测试"),
        ("MU 50% 正式门禁", "只看单次 PRB 利用率或 MU grant 数就可能在 OLLA 未稳态时报告虚假收益", "独立 8-rep 校准后冻结 50.77% 话务；8/8 MU pilot 未收敛即停止，不启动正式 16-rep A/B", "Gate 1 全过；Gate 2 正确 fail；无正式收益结论"),
        ("BLER/TDD 极端输入", "NaN/−Inf SINR 插值可能传播非有限 KPI，+Inf 可能误落低端；纯 UUU pattern 又让下行统计分母失真", "NaN/−Inf 保守判 BLER=1、+Inf 走高端曲线；TDD pattern 必须至少含一个 D/S 机会", "边界单测 + 系统合同"),
        ("文档合同漂移", "33/34 tools、273/272 RB、17/18 Gate、默认弹浏览器等旧说法", "README/Skill/算法卡与源码统一，并加语义哨兵", "test_interference"),
        ("说明书 RB 粒度合同", "独立 algo_defs2 页面仍宣称‘RB 级没有算法使用’，与已上线的逐 RB 功控精确路径矛盾；且未说明几何量的预数字波束参考面", "明确区分功控关闭的中心 RB 快路径与功控开启的 272-RB→post-MMSE→RBG 路径，并写出 prebeam/per-RB/EESM 边界", "说明书关键短语哨兵 + 完整 interference 回归"),
        ("代码根/场景资产根", "当前 MSG-Platform 代码可用但不带 configs/scenes，能力探测报 Sionna 可用而场景列表为 0", "代码与资产独立发现；SUPERRAN_SCENES 可显式覆盖，候选目录必须真的含 JSON 才接受", "10 场景恢复 + prepare_scene 资产回归"),
        ("并行样本语义", "static 串行固定 1 个 UE 几何；旧 worker 各用 seed+id，4 进程混入 4 个几何，KS p=0", "ChannelHub 增加 sample_index_offset；同 seed 全局索引分块；有状态/拒绝采样路径显式回退", "workers=1/2/4 的 h_true、h_est、SINR 逐位一致"),
        ("带宽→RB 反查", "生成器曾用 0.95×BW/(12·SCS) 近似；随后共享表又把 FR1/FR2 字典覆盖合并，使 50/100 MHz@60 kHz 的 FR1 值被 FR2 静默替换", "按 FR1/FR2 保留独立 38.101 表；后端依据载频显式选 range；非标准组合硬失败，synthetic grid 要显式 num_rb", "20M@30k→51；50M@60k FR1/FR2=65/66；100M=135/132；9 单测"),
        ("系统字节守恒显示", "offered_mbps 先四舍五入到 3 位而 served_mbps 保留全精度；恰好供需平衡的 trace 被显示成多发送 0.000333 Mbps", "保留吞吐全精度并新增 offered_bytes/served_bytes 整数真相源；守恒断言只用整数", "100k TTI×8 UE 全系统 14 章 + 精确字节反例"),
        ("性能标定漂移", "20-ray 内核上线后仍宣称旧单簇 24 ms/样本、probe 11.5x，CI 又把硬编码旧数字打印成‘实测’", "重测热/冷与 21-cell 对照；估时降格为版本化调度启发式；文档、Skill、MCP 统一以 elapsed_s 为准", "0.158/1.074/7.48 s anchors；probe 交错两轮约 1.80x"),
    ]
    body += table(["问题", "原症状", "修复", "证据"], audit_rows)
    body += callout(
        "note", "测试证据的准确读法",
        "<p>本轮新增的体验随机属性压力覆盖 18 个 case×12 条不变量，共 <strong>216/216</strong>；"
        "话务校准压力覆盖 5 类边界，共 <strong>40/40</strong>。<code>test_system.py</code> 的 14 个章节全部通过，"
        "其中包含 100,000 TTI×8 UE；<code>test_rng.py</code> 为 <strong>125/125</strong>；"
        "MU、物理不变量与开发手册定向回归均通过。MSG-Platform 的多 UE static 位置轮转回归也已通过。</p>"
        "<p>这些证据证明合同、守恒、机制反例与边界处理，不等价于所有性能假设已经成立。"
        "SRS/PMI Hello World 的 Gate 3 仍阻断，50% MU pilot 的 Gate 2 仍阻断；公司实测方向图、"
        "完整 Type-I 多层码本和现场标定的 EESM/MIESM 仍是外部校准边界。主手册浏览器 QA 已覆盖"
        "桌面/平板/手机：38/38 页、89/89 个 KaTeX 公式、276 条路由、36 张图、0 px 页面级溢出、"
        "0 个控制台错误；真实 KPI 工作台另通过双 Tab 点击、桌面/手机与 19 个用户指标面板检查。</p>",
    )
    return Page(
        "tests", "测试、压力验证与本次审计", "可信度", "VERIFICATION",
        "全测试文件地图、分层运行策略及本轮实现修复与证据。", body,
        tuple(item["name"] for item in tests) + ("audit", "regression"),
    )


def limitations_page() -> Page:
    body = """
<h2>已经拍板并写进默认行为</h2>
"""
    body += table(
        ["主题", "当前决定", "原因/影响"],
        [
            ("PF", "经典 PF；α=β=1、γ=0、无业务权重", "现场 EPF 定义未知前不自造厂商算法"),
            ("尾料 RBG", "业务传完即留空", "PRB 利用率反映真实话务，不虚构 padding 调度"),
            ("误块/HARQ", "单码字 TB；最多一次 IR/CC 重传", "同 MCS/RBG 数/rank/TBS；失败字节留队成为新 TB"),
            ("小 burst", "fractional-slot 推荐口径", "保留单时隙 burst，不制造体验 KPI 盲区"),
            ("预启动", "默认 1 s，PF/OLLA/SRS 演进但不计 KPI", "避开冷启动；结果仍检查收敛"),
            ("物理 SRS", "C_SRS=63/B_SRS=1/b_hop=0，T_SRS=20 slot", "30 kHz 下每 10 ms 发 16 RB，17 跳覆盖 272 RB；与系统级 srs_period_ms 分清单位"),
            ("PMI/CQI 周期", "20 ms 工程基线，可配置 5/10/20/40/80 ms", "协议是 slot 配置，不存在统一 5 ms"),
            ("RB 功控", "默认关闭；开启后 q[cell,RB] 限 0.1…4x 且每小区均值严格为 1", "开放频域自由度但不增加宽带预算；逐小区 S/N/I 精确耦合"),
            ("SU/MU", "先 PF 排序；比较 useful bytes；SU 可清空则强制 SU", "超出队列的谱效不算收益"),
            ("MU 比例", "MU PRB / 已用 PRB", "不是 MU TTI / 全部 TTI"),
        ],
    )
    body += """
<h2>仍是工程近似，不能包装成标准真值</h2>
"""
    body += table(
        ["边界", "当前实现", "升级需要"],
        [
            ("Agent 任务画像", "确定性关键词命中与 generic fallback；不是 LLM 语义分类器", "扩充可测试同义词/结构化 intent；模型只辅助解释，执行仍落有限合同"),
            ("阵子方向图", "110°×65° 参数化 3GPP-style cos/抛物近似，+45/−45° Jones", "公司实测复 Jones pattern、频率/温度/校准版本"),
            ("电下倾", "默认 6° 产品先验，可任意配置并进入 F", "实际 AAU 校准表与波束档位"),
            ("LMMSE", "真实 pilot→target 的频域 LMMSE；指数 PDP + 白噪声默认，时间仍线性", "实测/在线 PDP、Doppler/空间协方差、Kalman 或 2D LMMSE 路径"),
            ("宽带有效 SINR", "库支持 MIESM/EESM；experience 仍为 RBG 内线性、跨流/RBG dB 算术均值", "公司链路级标定并显式接入体验链的 EESM/MIESM β"),
            ("PMI/RI", "Type-I-style 宽带列集合、端口置换与独立 rank 选择", "严格 38.214 多层/子带/subset restriction/反馈比特与 RI pipeline"),
            ("RB 功控算法", "给定 profile 的守恒、逐小区耦合与逐 RBG 调度已实现", "跨小区闭环优化目标、约束信令与现场策略；当前不是自动功控算法"),
            ("MU", "SUS + ZF/RZF、pair table、用户级 MU-OLLA", "现场配对细则、最大用户/层数、接收机与 CSI error 标定"),
            ("BLER/HARQ", "预置通用 NewTx 曲线；每 TB 最多一次 IR/CC，空口身份冻结", "若升级为标准 HARQ，再补 RV、LLR、并行 process 与严格 timing"),
            ("话务 CDF", "可插拔经验 CDF + 标量 size/interval 校准", "公司视频/XR/FTP CDF 文件与用户 mix"),
            ("CDL 几何", "标准 profile 的 20-ray 相对几何旋到实际链路；仍非场景确定性 ray tracing", "Sionna RT Paths 或实测 CIR/角度"),
            ("RT 快速探测", "InternalSim 有几何 probe；Sionna RT 只能减少 UE/drop 跑小 N 完整路径", "若后端提供路径缓存/增量求解，再单独定义可验证 RT probe"),
            ("外部算法 CSI 角色", "method_metadata 声明 h_est/h_true 用法，MCP 不执行也无法观察用户进程", "受控沙箱、可检查中间产物或可复现容器"),
        ],
    )
    body += callout(
        "danger", "最容易误读的三个词",
        "<p><strong>全带谱效</strong>是当前宽带聚合口径，不等于 EESM；"
        "<strong>真实谱效</strong>在 SU/MU 决策里指不计 padding 的 useful bytes，不等于实验真值；"
        "<strong>perfect CSI</strong>是上界臂，不是现场可实现方案。</p>",
    )
    body += """
<h2>下一批需要业务/产品拍板</h2>
<ol>
<li>现场 EPF 的确切公式：乘性/加性时延因子、HoL/平均时延、budget 来源。</li>
<li>公司 AAU 的实测 Jones pattern、6° 下倾来源与频段/波束校准编号。</li>
<li>MU 配对/层数/接收机的产品细节，以及用户级 MU-OLLA 是否需按场景再分状态。</li>
<li>现场话务 CDF 与 BLER 标定曲线；它们决定 30%/50% 负载校准是否有现场意义。</li>
<li>有效 SINR 是否引入 EESM/MIESM，以及 β 的链路级标定协议。</li>
<li>是否把 MU 流间 <code>waterfilling</code> 暴露到体验系统，以及 RB 功控的优化目标是小区吞吐、边缘体验还是跨小区加权效用。</li>
</ol>
"""
    body += "<p class=source-row>默认配置：" + source_ref("src/superran/hardware.py", "DEFAULT_ELECTRICAL_DOWNTILT_DEG") + " · KPI/调度：" + source_ref("src/superran/experience.py", "def simulate_experience") + "</p>"
    return Page(
        "limitations", "当前限制、已决策项与路线图", "参考", "BOUNDARIES",
        "明确哪些是默认合同、哪些仍是近似、哪些必须等待产品数据。", body,
        ("limitations", "EPF", "EESM", "Jones", "roadmap"),
    )


def api_page(modules: list[ModuleDoc]) -> Page:
    symbol_count = sum(len(module.symbols) for module in modules)
    member_count = sum(len(symbol.members) for module in modules for symbol in module.symbols)
    body = metric_cards((
        ("Python 模块", str(len(modules)), "src/superran/*.py"),
        ("公开顶层符号", str(symbol_count), "非下划线 class/function"),
        ("公开成员/字段", str(member_count), "类内 method + annotated field"),
    ))
    body += """
<p>本页由 AST 从当前源码构建，签名、行号与首段 docstring 不手抄。它是“去哪找”的全量地图；
算法物理含义仍以前面的主题页为准。内部下划线函数未列入公开 API，但会在对应源码页出现。</p>
"""
    for module in modules:
        symbols = []
        for symbol in module.symbols:
            members = ""
            if symbol.members:
                member_rows = [
                    (
                        f'<code>{esc(member.name)}</code>', esc(member.kind),
                        f'<code>{esc(member.signature)}</code>', esc(member.doc),
                        source_ref(module.rel, "", f"L{member.line}"),
                    )
                    for member in symbol.members
                ]
                members = "<h4>公开成员</h4>" + table(
                    ["名称", "类型", "签名/字段", "说明", "源码"], member_rows,
                    raw={0, 2, 3, 4},
                )
            symbols.append(
                f'<details class="api"><summary><code>{esc(symbol.name)}</code>'
                f'<span>{esc(symbol.kind)} · L{symbol.line} · {esc(symbol.doc)}</span></summary>'
                f'<div><pre class="signature">{esc(symbol.signature)}</pre>{members}</div></details>'
            )
        body += (
            f'<section class="module-card" data-module="{esc(module.name)}"><h2>'
            f'<code>{esc(module.name)}</code></h2><p>{esc(module.doc)}</p>'
            f'<p class="muted">{module.lines:,} 行 · {len(module.symbols)} 个公开顶层符号 · '
            f'{source_ref(module.rel, "", module.rel)}</p>{"".join(symbols) if symbols else "<p>无公开顶层符号。</p>"}</section>'
        )
    return Page(
        "api", "全量 Python API 图谱", "参考", "API ATLAS",
        "由当前源码 AST 生成的全部模块、公开符号、签名、成员与源码链接。", body,
        tuple(module.name for module in modules) + ("API", "signature"),
    )


def glossary_page() -> Page:
    terms = [
        ("AE", "Antenna Element，物理阵子。64T 基线 192 个；公司 256T 为 1536 个；都不是同数量的独立 RF 链。"),
        ("RF port", "基带/射频可独立加权的端口。64T 为 64 个、每端口驱动 3 AE；256T 为 256 个、每端口驱动 6 AE。"),
        ("F", "被动馈电/耦合矩阵；64T 为 192×64，256T 为 1536×256。列范数 1，表达固定馈电、相位和下倾。"),
        ("configured / effective profile", "configured 是用户请求的剖面入口；effective 是按逐链路 LOS/NLOS 自洽后真正用于生成的剖面。"),
        ("EBF / PEBF / NEBF", "总功率 SVD 权 / 全局缩放满足每天线 / 每天线逐行归一。"),
        ("PMI", "Precoder Matrix Indicator；当前是宽带 Type-I-style 候选列索引与权矩阵，并经过独立 report 周期保持。"),
        ("RI", "Rank Indicator。当前离线列数、特征值门限 rank 与体验系统 rank_gNB 是不同来源，不能只看 PMIResult.rank。"),
        ("TaskProfile", "确定性关键词分类得到的有限任务画像；决定设计问题、参数决策、推荐、sweep 与物理 guard，不是 LLM 自由语义标签。"),
        ("channel_generation_mode", "数据实际由哪种后端生成的身份字段，如 sionna_rt 或 tdl_fallback；比请求中的 channel_model 更接近结果真相。"),
        ("RB power profile", "q[cell,RB] 频域功率倍率；逐小区均值 1、范围 0.1…4x，与 EBF/PEBF/NEBF 空间约束正交。"),
        ("SRS 周期", "两次配置 SRS occasion 的周期；不要称 SRS 年龄。年龄是当前 CSI 距上次观测的时长。"),
        ("CQI", "长期/量化链路质量输入；当前发送 MCS 链不把当前 h_true 偷渡进 CQI。"),
        ("OLLA", "Outer Loop Link Adaptation；SU 与 MU 分开维护用户级 offset。"),
        ("RBG", "Resource Block Group。默认 16 RB，100 MHz/30 kHz 下 17 RBG=272 RB。"),
        ("TBS", "Transport Block Size，38.214 离散量化后的可发送字节/比特规模；对 RBG 单调但不线性。"),
        ("MIESM", "Mutual Information Effective SINR Mapping；逐局部 SINR→QAM 互信息→平均→反解 AWGN SINR。库已支持，体验链当前未接入。"),
        ("EESM", "Exponential Effective SINR Mapping；用 beta 控制低 SINR 样本权重，beta 必须按链路曲线标定。"),
        ("PF R_avg", "PF 的历史服务量。体验模式按实际 scheduled TBS credit 更新，不能记全带速率。"),
        ("useful bytes", "SU/MU 方案比较中真正属于队列的字节；超出业务包的 padding 不计。"),
        ("首包时延", "包到达/生成到第一次被调度的时长。"),
        ("掐头去尾速率", "busy period 去掉首包等待与尾端定义后的体验口径。"),
        ("含头速率", "与掐头去尾分子相同，但分母从首包到达开始，包含首包等待。"),
        ("PRB 利用率", "统计窗口内已用 PRB equivalent / 可用 PRB equivalent；也是话务校准目标，不是配置本身。"),
        ("MU 配对比例", "生效 MU 的 PRB equivalent / 已用 PRB equivalent。"),
        ("CRN", "Common Random Numbers；A/B 复用同一物理、话务、BLER、tie-break 事件流。"),
        ("ResultArtifact", "外部算法逐样本结果合同；绑定数据摘要、有序 sample_ids、指标/单位、values/code 摘要和预注册身份。"),
        ("Gate 1/2/3", "数据体检 / 结果统计可信 / 可发布性。上一门失败时不能跨门写强结论。"),
        ("capacity mode", "谱效评估型：持续可发或统一资源口径，回答承载能力。"),
        ("experience mode", "体验评估型：显式 packet/burst/FIFO/等待，回答有业务时用户多快。"),
    ]
    body = '<div class="glossary">' + "".join(
        f'<div><dt>{esc(term)}</dt><dd>{esc(definition)}</dd></div>'
        for term, definition in terms
    ) + "</div>"
    body += """
<h2>从问题反查源码</h2>
"""
    body += table(
        ["问题", "第一入口", "再追"],
        [
            ("64T/公司256T/阵子图/F", "hardware.py", "MSG-Platform effective_array.py"),
            ("H 如何生成/同站状态", "channelhub.py + generate.py", "MSG-Platform internal_sim.py / sionna_rt.py"),
            ("SRS/LMMSE/老化", "physical.py + csi_aging.py", "MSG-Platform ref_signals/channel_est"),
            ("PMI/Type-I/RI/CQI 参照", "measure.py + hardware.py", "linklevel.py / system.py"),
            ("EBF/PEBF/NEBF", "beamforming.py", "linklevel.py"),
            ("CQI/MCS/TBS/BLER", "linkadapt.py", "bler_data_20b.py"),
            ("QAM MI/MIESM/EESM/HARQ 边界", "linkadapt.py + bler_curves.py", "system.py / experience.py"),
            ("SU/MU", "mumimo.py", "system.py / experience.py"),
            ("话务/PF/KPI", "traffic.py + experience.py", "kpi_view.py"),
            ("RB 功控/逐流功率/IoT", "power_control.py + mumimo.py", "interference.py / system.py"),
            ("随机数/统计/Gate", "rng.py + gates.py", "validate.py / analysis.py"),
            ("Agent 决策/说明书回传", "decisions.py + plan.py", "algorithms.py / spec.py / bridge.py"),
            ("射线追踪资产/probe", "scenes.py + scenario.py", "channelhub.py"),
            ("外部算法/预注册/结果合同", "analysis.py + results.py", "deliver.py / gates.py"),
            ("MCP/Skill", "server.py", "skills/channel-sim/"),
        ],
    )
    body += callout(
        "note", "文档版本事实",
        "<p>页面页脚的构建清单来自当前工作树。源码链接指向 GitHub main；若本地改动尚未推送，"
        "本页签名比远端链接更新，应以本地文件和测试证据为准。</p>",
    )
    return Page(
        "glossary", "术语表与源码反查", "参考", "GLOSSARY",
        "无线、系统仿真与平台术语的项目内含义，以及问题到源码的最短路径。", body,
        tuple(term for term, _ in terms),
    )


DOC_CSS = r"""
:root{
  color-scheme:light;--bg:#f6f7f4;--paper:#fff;--ink:#18201d;--muted:#65716b;
  --line:#dce3de;--soft:#eef2ef;--brand:#0b6b5d;--brand2:#0d4f85;--warm:#b85f19;
  --danger:#aa342c;--ok:#1d7a4d;--shadow:0 12px 38px rgba(18,43,35,.08);
  --header-h:64px;--left-w:286px;--right-w:248px;--content-w:900px;
}
html[data-theme="dark"]{
  color-scheme:dark;--bg:#101614;--paper:#17201d;--ink:#e9f0ec;--muted:#9fada6;
  --line:#304039;--soft:#202c28;--brand:#5fd0bd;--brand2:#79b8ef;--warm:#f2a35f;
  --danger:#ff8178;--ok:#75d7a2;--shadow:0 15px 44px rgba(0,0,0,.28);
}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:84px}
body{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,"Segoe UI","PingFang SC","Microsoft YaHei",system-ui,sans-serif;line-height:1.76;text-rendering:optimizeLegibility}
.sr-only{position:absolute!important;width:1px!important;height:1px!important;padding:0!important;margin:-1px!important;overflow:hidden!important;clip:rect(0,0,0,0)!important;white-space:nowrap!important;border:0!important}
button,input{font:inherit}.skip{position:fixed;left:12px;top:-60px;z-index:99;padding:8px 14px;background:var(--paper);color:var(--ink);border:1px solid var(--brand);border-radius:8px}.skip:focus{top:10px}
.topbar{height:var(--header-h);position:fixed;inset:0 0 auto;z-index:30;background:color-mix(in srgb,var(--paper) 92%,transparent);backdrop-filter:blur(14px);border-bottom:1px solid var(--line);display:flex;align-items:center;padding:0 18px;gap:15px}
.brand{display:flex;align-items:center;gap:10px;width:calc(var(--left-w) - 18px);text-decoration:none;color:var(--ink);font-weight:780;letter-spacing:-.02em}.brand svg{width:35px;height:35px;flex:none}.brand small{display:block;color:var(--muted);font-size:10px;letter-spacing:.14em;font-weight:700}
.search-wrap{position:relative;flex:1;max-width:720px}.search-wrap input{width:100%;height:40px;border:1px solid var(--line);background:var(--soft);color:var(--ink);border-radius:10px;padding:0 90px 0 40px;outline:none}.search-wrap input:focus{border-color:var(--brand);box-shadow:0 0 0 3px color-mix(in srgb,var(--brand) 18%,transparent)}
.search-icon{position:absolute;left:13px;top:8px;color:var(--muted)}.kbd{position:absolute;right:10px;top:8px;color:var(--muted);border:1px solid var(--line);border-bottom-width:2px;background:var(--paper);border-radius:5px;padding:0 7px;font-size:12px}
.top-actions{margin-left:auto;display:flex;gap:7px}.icon-btn{height:38px;min-width:38px;padding:0 11px;border:1px solid var(--line);border-radius:9px;background:var(--paper);color:var(--ink);cursor:pointer}.icon-btn:hover{border-color:var(--brand);color:var(--brand)}.depth-top{display:inline-flex;align-items:center;gap:7px;font-size:12px;font-weight:750;white-space:nowrap}.depth-top:before{content:"";width:7px;height:7px;border-radius:50%;background:var(--brand)}html[data-reading-mode="detailed"] .depth-top{background:color-mix(in srgb,var(--brand) 12%,var(--paper));border-color:var(--brand);color:var(--brand)}
.menu-btn{display:none}.progress{position:absolute;left:0;bottom:-1px;height:2px;background:var(--brand);width:0}
.sidebar{position:fixed;top:var(--header-h);bottom:0;left:0;width:var(--left-w);padding:20px 15px 32px 18px;overflow:auto;border-right:1px solid var(--line);background:var(--paper);z-index:20}
.nav-group{margin:0 0 20px}.nav-group h2{margin:0 8px 6px;font-size:11px;letter-spacing:.13em;color:var(--muted);text-transform:uppercase}.nav-group a{display:flex;gap:9px;align-items:center;padding:7px 9px;margin:2px 0;border-radius:8px;color:var(--muted);text-decoration:none;font-size:14px;line-height:1.35}.nav-group a span{font-variant-numeric:tabular-nums;font-size:11px;opacity:.65;width:19px}.nav-group a:hover{background:var(--soft);color:var(--ink)}.nav-group a.active{background:color-mix(in srgb,var(--brand) 12%,var(--paper));color:var(--brand);font-weight:700}
.side-meta{border-top:1px solid var(--line);padding:16px 9px 0;color:var(--muted);font-size:12px}.side-meta b{color:var(--ink)}
.toc{position:fixed;top:var(--header-h);bottom:0;right:0;width:var(--right-w);padding:27px 22px;overflow:auto;border-left:1px solid var(--line);background:var(--bg)}.toc strong{font-size:12px;letter-spacing:.1em}.toc a{display:block;padding:5px 0;color:var(--muted);text-decoration:none;font-size:13px;line-height:1.4}.toc a.h3{padding-left:13px;font-size:12px}.toc a:hover,.toc a.active{color:var(--brand)}
.main{margin-left:var(--left-w);margin-right:var(--right-w);padding:calc(var(--header-h) + 42px) 44px 90px;min-height:100vh}.doc-page{max-width:var(--content-w);margin:0 auto}.doc-page[hidden]{display:none!important}
.page-hero{padding:0 0 28px;border-bottom:1px solid var(--line);margin-bottom:34px}.eyebrow{font-weight:800;color:var(--brand);font-size:12px;letter-spacing:.15em}.page-hero h1{font-size:clamp(32px,4vw,50px);line-height:1.12;letter-spacing:-.045em;margin:10px 0 14px}.lead{font-size:19px;line-height:1.72;color:var(--muted);max-width:780px;margin:0}.tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:18px}.tag,.badge{display:inline-flex;align-items:center;border:1px solid var(--line);background:var(--soft);color:var(--muted);border-radius:99px;padding:3px 9px;font-size:11px}.badge.ok{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 35%,var(--line))}
.reading-control{display:flex;align-items:center;flex-wrap:wrap;gap:10px 14px;margin-top:22px;padding:11px 13px;border:1px solid var(--line);border-radius:12px;background:var(--paper);width:max-content;max-width:100%}.reading-control>span{font-size:11px;font-weight:800;letter-spacing:.08em;color:var(--muted)}.depth-segment{display:inline-flex;padding:3px;border-radius:9px;background:var(--soft)}.depth-segment button{border:0;border-radius:7px;background:transparent;color:var(--muted);padding:5px 12px;cursor:pointer;font-size:12px;font-weight:750}.depth-segment button[aria-pressed="true"]{background:var(--paper);color:var(--brand);box-shadow:0 1px 5px rgba(0,0,0,.09)}.reading-status{color:var(--muted);font-size:11px}.reading-status b{color:var(--ink)}
h2{font-size:27px;line-height:1.3;letter-spacing:-.025em;margin:55px 0 16px;scroll-margin-top:84px}h3{font-size:20px;margin:34px 0 11px;scroll-margin-top:84px}h4{font-size:15px;margin:24px 0 9px}p{margin:10px 0 18px}a{color:var(--brand2);text-underline-offset:3px}strong{font-weight:750}code{font-family:"Cascadia Code",Consolas,monospace;font-size:.88em;background:var(--soft);border:1px solid color-mix(in srgb,var(--line) 70%,transparent);padding:.1em .32em;border-radius:5px;word-break:break-word}
.heading-link{border:0;background:transparent;color:var(--muted);font-size:.7em;opacity:0;margin-left:8px;cursor:pointer}.doc-page h2:hover .heading-link,.doc-page h3:hover .heading-link,.heading-link:focus{opacity:1}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:25px 0 34px}.metric{background:var(--paper);border:1px solid var(--line);border-radius:13px;padding:16px;box-shadow:0 4px 18px rgba(20,45,36,.035)}.metric span{display:block;color:var(--muted);font-size:12px}.metric b{display:block;font-size:25px;line-height:1.2;margin:5px 0;color:var(--brand)}.metric small{color:var(--muted)}
.product-showcase{margin:34px 0 48px;padding:28px;border:1px solid color-mix(in srgb,var(--brand) 28%,var(--line));border-radius:20px;background:linear-gradient(145deg,color-mix(in srgb,var(--brand) 8%,var(--paper)),color-mix(in srgb,var(--brand2) 6%,var(--paper)));box-shadow:var(--shadow)}.product-showcase-head>span,.hello-world>span{display:block;color:var(--brand);font-size:10px;font-weight:850;letter-spacing:.18em}.product-showcase-head h2{margin:7px 0 10px;font-size:31px}.product-showcase-head p{max-width:800px;color:var(--muted);margin:0 0 22px}.surface-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.surface-card{min-width:0;padding:18px;border:1px solid var(--line);border-radius:16px;background:var(--paper);box-shadow:0 10px 30px rgba(20,45,36,.07)}.surface-copy{min-height:192px}.surface-copy h3{margin:6px 0 8px;font-size:20px}.surface-copy p{font-size:13px;line-height:1.68;color:var(--muted);margin:0 0 10px}.surface-copy a{font-size:12px;font-weight:750;text-decoration:none}.surface-stage{font-size:9px;font-weight:850;letter-spacing:.14em;color:var(--warm)}.product-shot{margin:16px 0 24px;border:1px solid var(--line);border-radius:14px;overflow:hidden;background:var(--paper);box-shadow:0 13px 30px rgba(14,34,45,.12)}.product-shot img{display:block;width:100%;height:auto}.product-shot figcaption{padding:9px 12px;border-top:1px solid var(--line);color:var(--muted);font-size:11px;line-height:1.45}.hello-world{margin:0 0 24px;padding:27px 29px;border:1px solid color-mix(in srgb,var(--brand) 34%,var(--line));border-radius:17px;background:linear-gradient(135deg,color-mix(in srgb,var(--brand) 13%,var(--paper)),color-mix(in srgb,var(--brand2) 8%,var(--paper)));box-shadow:var(--shadow)}.hello-world h2{margin:7px 0 10px;font-size:31px}.hello-world p{margin:0;color:var(--muted);font-size:15px}.paths-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:18px 0 34px}.paths-grid a{display:flex;min-width:0;min-height:78px;flex-direction:column;justify-content:space-between;gap:7px;padding:14px 15px;border:1px solid var(--line);border-radius:12px;background:var(--paper);color:var(--ink);text-decoration:none;box-shadow:0 4px 16px rgba(20,45,36,.04);transition:border-color .2s ease,box-shadow .2s ease,background-color .2s ease}.paths-grid a b{font-size:14px;line-height:1.35;color:var(--brand)}.paths-grid a span{color:var(--muted);font-size:12px;line-height:1.5}.paths-grid a:hover{border-color:color-mix(in srgb,var(--brand) 55%,var(--line));background:color-mix(in srgb,var(--brand) 4%,var(--paper));box-shadow:0 9px 24px rgba(20,45,36,.09)}.paths-grid a:focus-visible{outline:3px solid color-mix(in srgb,var(--brand2) 55%,transparent);outline-offset:2px}.hello-actions{margin-bottom:0}.hello-actions a{background:color-mix(in srgb,var(--paper) 86%,transparent)}
.callout{display:grid;grid-template-columns:31px 1fr;gap:12px;margin:24px 0;padding:17px 18px;border:1px solid var(--line);border-left:4px solid var(--brand2);background:color-mix(in srgb,var(--brand2) 5%,var(--paper));border-radius:10px}.callout p{margin:5px 0 0}.callout-icon{width:27px;height:27px;display:grid;place-items:center;border-radius:50%;background:var(--brand2);color:#fff;font-weight:800}.callout.good{border-left-color:var(--ok);background:color-mix(in srgb,var(--ok) 6%,var(--paper))}.callout.good .callout-icon{background:var(--ok)}.callout.warn,.callout.decision{border-left-color:var(--warm);background:color-mix(in srgb,var(--warm) 7%,var(--paper))}.callout.warn .callout-icon,.callout.decision .callout-icon{background:var(--warm)}.callout.danger{border-left-color:var(--danger);background:color-mix(in srgb,var(--danger) 6%,var(--paper))}.callout.danger .callout-icon{background:var(--danger)}
.steps{list-style:none;padding:0;margin:25px 0}.steps li{display:grid;grid-template-columns:38px 1fr;gap:13px;position:relative;padding:0 0 25px}.steps li:not(:last-child):before{content:"";position:absolute;left:18px;top:36px;bottom:0;border-left:1px solid var(--line)}.step-no{width:37px;height:37px;border-radius:50%;background:var(--brand);color:#fff;display:grid;place-items:center;font-weight:800}.steps p{margin:4px 0}
.table-wrap{overflow:auto;margin:20px 0 28px;border:1px solid var(--line);border-radius:11px;background:var(--paper)}table{border-collapse:collapse;width:100%;font-size:13px;line-height:1.5}th{text-align:left;background:var(--soft);font-size:11px;letter-spacing:.04em;color:var(--muted);position:sticky;top:0}th,td{padding:11px 13px;border-bottom:1px solid var(--line);vertical-align:top}tr:last-child td{border-bottom:0}tbody tr:hover{background:color-mix(in srgb,var(--brand) 3%,transparent)}
.codebox{margin:21px 0 28px;border:1px solid var(--line);border-radius:11px;overflow:hidden;background:#101916;color:#dceae4;box-shadow:var(--shadow)}.codebar{height:38px;display:flex;align-items:center;justify-content:space-between;padding:0 12px;background:#192620;color:#9eb5ab;font-size:12px}.copy{border:1px solid #40544b;background:#22352d;color:#dceae4;border-radius:6px;padding:3px 9px;cursor:pointer}.codebox pre{margin:0;padding:17px 19px;overflow:auto;line-height:1.62}.codebox code{font-size:12.5px;background:none;border:0;padding:0;color:inherit;white-space:pre}.signature,.mini-json{overflow:auto;background:var(--soft);border:1px solid var(--line);padding:12px;border-radius:8px;font:12px/1.55 "Cascadia Code",Consolas,monospace;white-space:pre-wrap}.mini-json{max-height:310px;white-space:pre}
.diagram{margin:26px 0 34px;background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:15px;box-shadow:var(--shadow);overflow:auto}.diagram svg{display:block;width:100%;min-width:650px;height:auto}.diagram figcaption{text-align:center;color:var(--muted);font-size:12px;margin-top:7px}.diagram rect{fill:var(--soft);stroke:var(--line)}.diagram .accent rect{fill:color-mix(in srgb,var(--brand) 12%,var(--paper));stroke:var(--brand)}.diagram .good rect{fill:color-mix(in srgb,var(--ok) 10%,var(--paper));stroke:var(--ok)}.diagram .danger rect{fill:color-mix(in srgb,var(--danger) 9%,var(--paper));stroke:var(--danger)}.diagram .warn rect{fill:color-mix(in srgb,var(--warm) 11%,var(--paper));stroke:var(--warm)}.diagram text{font-family:inherit;fill:var(--ink)}.diagram .dt{font-size:14px;font-weight:750}.diagram .ds{font-size:11px;fill:var(--muted);text-anchor:middle}.diagram .b .ds,.diagram .accent .ds,.diagram .good .ds,.diagram .danger .ds,.diagram .warn .ds{text-anchor:start}.diagram .arr{stroke:var(--muted);stroke-width:1.5;fill:none}.diagram marker path{fill:var(--muted)}.diagram .al,.diagram .tiny{font-size:9px;fill:var(--muted);text-anchor:middle}.diagram .site{fill:color-mix(in srgb,var(--brand) 10%,var(--paper));stroke:var(--brand)}.diagram .site-t{font-size:12px;text-anchor:middle}.diagram .sector{stroke:var(--brand);stroke-width:3}.diagram .ae.polp{fill:#e45c5c;stroke:none}.diagram .ae.polm{fill:#357bd8;stroke:none}.diagram .feed{fill:none;stroke:var(--muted);stroke-dasharray:4 3}.diagram .slot{font-size:12px;fill:#fff;text-anchor:middle;font-weight:700}.diagram .brace,.diagram .axis,.diagram .cap{fill:none;stroke:var(--muted)}.diagram .bar{fill:var(--brand);stroke:none}.diagram .bar.bad{fill:var(--danger)}.diagram .hist{fill:var(--brand);stroke:none}.diagram .yes{font-size:11px;fill:var(--ok);text-anchor:middle}
.diagram .plot-panel{fill:color-mix(in srgb,var(--soft) 58%,var(--paper));stroke:var(--line)}.diagram .pattern-grid{fill:none;stroke:color-mix(in srgb,var(--muted) 38%,transparent);stroke-width:1}.diagram .pattern-axis{stroke:var(--line);stroke-width:1}.diagram .pattern-lobe{stroke-width:2.2}.diagram .pattern-lobe.horizontal{fill:color-mix(in srgb,var(--brand) 18%,transparent);stroke:var(--brand)}.diagram .pattern-lobe.element{fill:none;stroke:var(--muted);stroke-dasharray:6 4}.diagram .pattern-lobe.port{fill:color-mix(in srgb,var(--ok) 16%,transparent);stroke:var(--ok)}.diagram .pattern-tick{font-size:8px;fill:var(--muted);text-anchor:end}.diagram .pattern-note{font-size:10px;fill:var(--muted)}.diagram .hpbw{stroke:var(--warm);stroke-dasharray:4 3}.diagram .tilt-ray{stroke:var(--danger);stroke-width:1.5;stroke-dasharray:5 4}.diagram .legend{stroke-width:3}.diagram .legend.element{stroke:var(--muted);stroke-dasharray:6 4}.diagram .legend.port{stroke:var(--ok)}.diagram .physical-dot{fill:var(--ok);stroke:none}.diagram .index-cell.canonical{fill:color-mix(in srgb,var(--brand) 13%,var(--paper));stroke:var(--brand)}.diagram .index-cell.legacy{fill:color-mix(in srgb,var(--warm) 13%,var(--paper));stroke:var(--warm)}.diagram .index-text{font-size:12px;font-weight:700;text-anchor:middle}
.diagram .chart-band{stroke:none}.diagram .chart-band.band-0{fill:color-mix(in srgb,var(--brand) 6%,transparent)}.diagram .chart-band.band-1{fill:color-mix(in srgb,var(--warm) 5%,transparent)}.diagram .chart-band-label{font-size:10px;font-weight:750;fill:var(--muted);text-anchor:middle}.diagram .chart-grid{stroke:color-mix(in srgb,var(--line) 70%,transparent);stroke-width:.75}.diagram .chart-grid.vertical{stroke-dasharray:3 4}.diagram .chart-axis{stroke:var(--ink);stroke-width:1.15}.diagram .chart-tick{font-size:9px;fill:var(--muted)}.diagram .chart-tick.x{text-anchor:middle}.diagram .chart-tick.y{text-anchor:end}.diagram .chart-axis-label{font-size:10px;font-weight:650;fill:var(--muted);text-anchor:middle}.diagram .chart-panel-title{font-size:13px;font-weight:800;fill:var(--ink)}.diagram .chart-newtx,.diagram .chart-retx,.diagram .chart-curve{fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}.diagram .chart-newtx{stroke:var(--brand)}.diagram .chart-retx{stroke:var(--warm);stroke-dasharray:6 4}.diagram circle.chart-newtx,.diagram circle.chart-retx{fill:var(--paper);stroke-width:1.7;stroke-dasharray:none}.diagram .chart-curve{stroke-width:1.65}.diagram .legend-line{stroke-width:2.4}.diagram .chart-legend{font-size:10px;font-weight:700;fill:var(--muted)}.diagram .chart-legend.compact{font-size:8px}.bler-curve-atlas svg,.bler-threshold-chart svg{min-width:760px}
.kx[data-display="1"]{display:block;overflow:auto;text-align:center;padding:13px 4px;margin:18px 0}.kx math{font-size:1.12em}.source-row{color:var(--muted);font-size:12px;border-top:1px dashed var(--line);padding-top:12px}.src{font-family:"Cascadia Code",Consolas,monospace;font-size:11px}.muted{color:var(--muted)}
.formula-card{margin:24px 0 30px}.formula-expression{min-width:0}.formula-explain{display:none}.formula-card .kx[data-display="1"]{margin:0}.detail-content{display:none}.detail-opening{margin:70px 0 34px;padding:25px 28px;border-radius:16px;background:linear-gradient(135deg,color-mix(in srgb,var(--brand) 14%,var(--paper)),color-mix(in srgb,var(--brand2) 8%,var(--paper)));border:1px solid color-mix(in srgb,var(--brand) 35%,var(--line))}.detail-opening>span,.worked-example>span{font-size:10px;letter-spacing:.18em;font-weight:850;color:var(--brand)}.detail-opening h2{margin:7px 0 9px;font-size:31px}.detail-opening p{margin:0;color:var(--muted);font-size:16px}.detail-trace{margin-bottom:38px}.worked-example{margin:38px 0;padding:24px 27px;border:1px solid color-mix(in srgb,var(--brand2) 32%,var(--line));border-radius:14px;background:color-mix(in srgb,var(--brand2) 6%,var(--paper));box-shadow:var(--shadow)}.worked-example h2{margin:6px 0 13px;font-size:24px}.worked-example p:last-child{margin-bottom:0}.detail-checks{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;list-style:none;padding:0}.detail-checks li{display:grid;grid-template-columns:28px 1fr;gap:10px;padding:14px;border:1px solid var(--line);border-radius:11px;background:var(--paper)}.detail-checks li>span{width:25px;height:25px;border-radius:50%;display:grid;place-items:center;background:color-mix(in srgb,var(--ok) 13%,var(--paper));color:var(--ok);font-weight:850}.detail-checks p{margin:3px 0 0;color:var(--muted);font-size:13px}.detail-pitfalls ul{margin:8px 0 0;padding-left:19px}.detail-pitfalls li{margin:5px 0}.detail-sources{display:flex;align-items:center;flex-wrap:wrap;gap:7px;margin:28px 0;padding-top:17px;border-top:1px dashed var(--line);color:var(--muted);font-size:12px}.detail-sources strong{margin-right:6px;color:var(--ink)}
html[data-reading-mode="detailed"] .detail-content{display:block}html[data-reading-mode="detailed"] .formula-card{display:grid;grid-template-columns:minmax(300px,1.05fr) minmax(280px,.95fr);gap:18px;align-items:start;padding:18px;border:1px solid var(--line);border-radius:14px;background:var(--paper);box-shadow:0 6px 24px rgba(20,45,36,.05)}html[data-reading-mode="detailed"] .formula-expression{position:sticky;top:82px}html[data-reading-mode="detailed"] .formula-explain{display:block;border-left:3px solid var(--brand);padding-left:16px;color:var(--muted);font-size:13px}html[data-reading-mode="detailed"] .formula-explain>strong{display:block;color:var(--ink);font-size:15px;margin-bottom:5px}html[data-reading-mode="detailed"] .formula-explain>p{margin:4px 0 12px}.symbol-list{margin:0}.symbol-list>div{display:grid;grid-template-columns:minmax(92px,.42fr) 1fr;gap:8px;padding:7px 0;border-top:1px solid var(--line)}.symbol-list dt{font-family:"Cascadia Code",Consolas,monospace;color:var(--brand);font-weight:800;word-break:break-word}.symbol-list dd{margin:0;color:var(--muted)}
details.api,details.preset{border:1px solid var(--line);background:var(--paper);border-radius:10px;margin:8px 0;overflow:hidden}details.api>summary,details.preset>summary{cursor:pointer;display:flex;gap:12px;align-items:flex-start;justify-content:space-between;padding:13px 15px;list-style:none}details.api>summary::-webkit-details-marker,details.preset>summary::-webkit-details-marker{display:none}details.api>summary:before,details.preset>summary:before{content:"+";color:var(--brand);font-weight:800}details[open]>summary:before{content:"−"}details.api>summary code{flex:none}details.api>summary span{color:var(--muted);font-size:12px;flex:1}details.api>div,details.preset>div{padding:0 16px 16px;border-top:1px solid var(--line)}details.preset>summary span{display:flex;flex-direction:column;gap:3px}details.preset>summary strong{font-size:14px}details.preset>summary small{color:var(--muted)}.module-card{border-top:3px solid var(--brand);padding-top:1px;margin-top:58px}.module-card>h2{margin-top:22px}.module-card>h2 code{font-size:.8em}.check-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:8px;list-style:none;padding:0}.check-grid li{background:var(--paper);border:1px solid var(--line);border-radius:9px;padding:10px;font-size:13px}.check-grid li span{display:inline-grid;place-items:center;width:23px;height:23px;border-radius:50%;background:var(--soft);color:var(--brand);font-weight:800;margin-right:8px}.glossary>div{display:grid;grid-template-columns:185px 1fr;border-bottom:1px solid var(--line);padding:13px 0}.glossary dt{font-weight:800;color:var(--brand)}.glossary dd{margin:0;color:var(--muted)}
.page-nav{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:70px;border-top:1px solid var(--line);padding-top:23px}.page-nav a{display:block;padding:14px;border:1px solid var(--line);border-radius:10px;background:var(--paper);text-decoration:none}.page-nav a.next{text-align:right}.page-nav small{display:block;color:var(--muted)}
.search-panel{position:fixed;top:58px;left:calc(var(--left-w) + 20px);width:min(720px,calc(100vw - var(--left-w) - 300px));max-height:70vh;overflow:auto;z-index:50;background:var(--paper);border:1px solid var(--line);border-radius:12px;box-shadow:0 22px 70px rgba(0,0,0,.22);padding:8px}.search-panel[hidden]{display:none}.search-result{display:block;padding:10px 12px;border-radius:8px;text-decoration:none;color:var(--ink)}.search-result:hover,.search-result.active{background:var(--soft)}.search-result small{display:block;color:var(--muted)}.search-empty{padding:18px;color:var(--muted)}
.backdrop{display:none}.doc-footer{margin:55px auto 0;max-width:var(--content-w);color:var(--muted);font-size:12px;text-align:center}
@media(max-width:1180px){:root{--right-w:0px}.toc{display:none}.main{margin-right:0}}
@media(max-width:820px){.menu-btn{display:inline-block}.brand{width:auto;flex:1}.brand-text{display:none}.topbar{padding:0 10px}.search-wrap{position:absolute;left:57px;right:174px}.kbd{display:none}.sidebar{transform:translateX(-102%);transition:transform .2s ease;box-shadow:var(--shadow)}body.menu-open .sidebar{transform:none}.backdrop{display:block;position:fixed;inset:var(--header-h) 0 0;background:rgba(0,0,0,.38);z-index:19;opacity:0;pointer-events:none;transition:opacity .2s}body.menu-open .backdrop{opacity:1;pointer-events:auto}.main{margin-left:0;padding:calc(var(--header-h) + 30px) 22px 70px}.search-panel{left:12px;right:12px;width:auto}.page-hero h1{font-size:36px}.lead{font-size:17px}.surface-grid{grid-template-columns:1fr}.surface-copy{min-height:0;margin-bottom:14px}html[data-reading-mode="detailed"] .formula-card{grid-template-columns:1fr}html[data-reading-mode="detailed"] .formula-expression{position:static}html[data-reading-mode="detailed"] .formula-explain{border-left:0;border-top:3px solid var(--brand);padding:14px 0 0}.detail-checks{grid-template-columns:1fr}}
@media(max-width:820px){.paths-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:500px){.main{padding-left:15px;padding-right:15px}.top-actions .print-btn{display:none}.depth-top{padding:0;width:38px;font-size:0;gap:4px}.depth-top:after{content:"详";font-size:12px}.depth-top[aria-pressed="true"]:after{content:"简"}.search-wrap{right:100px}.page-hero h1{font-size:31px}.metrics{grid-template-columns:1fr 1fr}.metric b{font-size:20px}.product-showcase{padding:17px;margin-left:-4px;margin-right:-4px}.product-showcase-head h2,.hello-world h2{font-size:25px}.surface-card{padding:11px}.mock-spec-grid{grid-template-columns:1fr}.mock-topology{min-height:120px}.mock-priority{grid-template-columns:1fr}.mock-priority span{grid-column:1;grid-row:auto}.hello-world{padding:20px}.callout{grid-template-columns:27px 1fr;padding:14px}.page-nav{grid-template-columns:1fr}.glossary>div{grid-template-columns:1fr;gap:4px}.diagram{margin-left:-5px;margin-right:-5px;padding:8px}.diagram::before{content:"\2194  \5de6\53f3\6ed1\52a8\67e5\770b\5b8c\6574\56fe";display:block;position:sticky;left:0;width:max-content;margin:0 0 7px;padding:4px 8px;border:1px solid var(--line);border-radius:999px;background:var(--paper);color:var(--muted);font-size:11px;letter-spacing:.02em}.table-wrap{margin-left:-4px;margin-right:-4px}.page-nav a.next{text-align:left}.reading-control{width:100%;align-items:flex-start}.reading-status{flex-basis:100%}.detail-opening,.worked-example{padding:19px}.symbol-list>div{grid-template-columns:1fr;gap:2px}}
@media(max-width:500px){.paths-grid{grid-template-columns:1fr}.paths-grid a{min-height:0}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
@media print{.topbar,.sidebar,.toc,.search-panel,.backdrop,.page-nav,.heading-link,.reading-control{display:none!important}.main{margin:0;padding:0}.doc-page[hidden]{display:block!important;page-break-before:always}.doc-page:first-child{page-break-before:auto}.diagram,.metric,.callout,details,.formula-card,.surface-card,.hello-world{break-inside:avoid;box-shadow:none}details>div{display:block!important}body{background:#fff;color:#111}.page-hero{padding-top:15mm}html[data-reading-mode="detailed"] .formula-card{display:block}html[data-reading-mode="detailed"] .formula-explain{margin-top:8px}}
"""


DOC_JS = r"""
(function(){
  'use strict';
  const pages=window.__DOC_PAGES__||[];
  const $=(s,r=document)=>r.querySelector(s), $$=(s,r=document)=>Array.from(r.querySelectorAll(s));
  const articles=new Map($$('.doc-page').map(a=>[a.dataset.page,a]));
  const navLinks=$$('.sidebar a[data-page]');
  const toc=$('#toc-links'), titleNode=$('#doc-title'), progress=$('#progress');
  const depthTop=$('#reading-toggle');
  let current=pages[0]&&pages[0].key, readingMode='compact';

  function cleanSlug(s){return s.trim().toLowerCase().replace(/[\s/]+/g,'-').replace(/[^\w\u3400-\u9fff-]/g,'').replace(/-+/g,'-')||'section'}
  articles.forEach((article,pageKey)=>{
    const used=new Set();
    $$('h2,h3',article).forEach(h=>{
      let id=cleanSlug(h.textContent), base=id, i=2; while(used.has(id))id=base+'-'+i++;
      used.add(id); h.id=id;
      const b=document.createElement('button'); b.className='heading-link'; b.type='button'; b.title='复制本节地址'; b.textContent='#';
      b.addEventListener('click',()=>copyText(location.href.split('#')[0]+'#/'+pageKey+'/'+id,b)); h.appendChild(b);
    });
  });

  function storedDepth(){try{return localStorage.getItem('superran-doc-depth-v1')}catch(_){return null}}
  function persistDepth(value){try{localStorage.setItem('superran-doc-depth-v1',value)}catch(_){/* file/private contexts may deny storage */}}
  function readingMinutes(chars){return Math.max(1,Math.round(Number(chars||0)/430))}
  function updateReadingStatus(page){
    if(!page)return;
    const chars=readingMode==='detailed'?page.detailed_chars:page.compact_chars;
    const status=$('.reading-status',articles.get(page.key));
    if(status){
      const ratio=Number(page.detail_ratio||1).toFixed(1);
      const suffix=page.reading_kind==='reference'?' · 全量参考附录':' · 详细约 '+ratio+'×';
      status.innerHTML='<b>'+(readingMode==='detailed'?'详细版':'精简版')+'</b> · 约 '+readingMinutes(chars)+' 分钟'+suffix;
    }
  }
  function setReadingMode(value,persist=true){
    readingMode=value==='detailed'?'detailed':'compact';
    document.documentElement.dataset.readingMode=readingMode;
    $$('[data-reading-choice]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.readingChoice===readingMode)));
    if(depthTop){depthTop.textContent=readingMode==='detailed'?'切到精简':'切到详细';depthTop.setAttribute('aria-pressed',String(readingMode==='detailed'))}
    if(persist)persistDepth(readingMode);
    updateReadingStatus(pages.find(p=>p.key===current));
    buildToc(articles.get(current));
  }
  $$('[data-reading-choice]').forEach(b=>b.addEventListener('click',()=>setReadingMode(b.dataset.readingChoice)));
  if(depthTop)depthTop.addEventListener('click',()=>setReadingMode(readingMode==='detailed'?'compact':'detailed'));
  setReadingMode(storedDepth()==='detailed'?'detailed':'compact',false);

  function route(){
    const raw=(location.hash||'#/overview').replace(/^#\/?/,'');
    const parts=raw.split('/').filter(Boolean), key=articles.has(parts[0])?parts[0]:(pages[0]&&pages[0].key);
    const section=parts.slice(1).join('/');
    if(!key)return;
    const changed=current!==key; current=key;
    articles.forEach((a,k)=>a.hidden=k!==key);
    navLinks.forEach(a=>{const on=a.dataset.page===key;a.classList.toggle('active',on);if(on)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current')});
    const page=pages.find(p=>p.key===key); if(page){document.title=page.title+' · SuperRAN';titleNode.textContent=page.title;updateReadingStatus(page)}
    const target=section&&articles.get(key).querySelector('#'+CSS.escape(section));
    if(target&&target.closest('.detail-content')&&readingMode!=='detailed')setReadingMode('detailed');
    buildToc(articles.get(key)); document.body.classList.remove('menu-open');
    requestAnimationFrame(()=>{if(target)target.scrollIntoView();else if(changed)window.scrollTo(0,0)});
  }
  function buildToc(article){
    toc.innerHTML=''; if(!article)return;
    $$('h2,h3',article).filter(h=>readingMode==='detailed'||!h.closest('.detail-content')).forEach(h=>{const a=document.createElement('a');a.href='#/'+current+'/'+h.id;a.textContent=h.childNodes[0].textContent.trim();if(h.tagName==='H3')a.className='h3';toc.appendChild(a)});
  }
  function copyText(textValue,button){
    const done=()=>{const old=button.textContent;button.textContent='已复制';setTimeout(()=>button.textContent=old,1000)};
    if(navigator.clipboard&&window.isSecureContext)navigator.clipboard.writeText(textValue).then(done);else{const t=document.createElement('textarea');t.value=textValue;document.body.appendChild(t);t.select();document.execCommand('copy');t.remove();done()}
  }
  $$('.copy').forEach(b=>b.addEventListener('click',()=>copyText($('code',b.closest('.codebox')).textContent,b)));

  const themeBtn=$('#theme');
  // 主题偏好也走 try/catch：这一页的首要使用方式是 file:// 双击打开，而某些浏览器
  // 设置下对 file:// 的 localStorage 访问会直接抛 SecurityError。上面的阅读深度
  // 已经防过一次；主题这里若不防，整段初始化脚本会在第一行就中断，
  // 搜索、导航、目录、深度切换**全部失效**——而页面看起来只是"没变暗"。
  function storedTheme(){try{return localStorage.getItem('sw-doc-theme')}catch(_){return null}}
  function persistTheme(value){try{localStorage.setItem('sw-doc-theme',value)}catch(_){/* file/private contexts may deny storage */}}
  function setTheme(value){document.documentElement.dataset.theme=value;themeBtn.textContent=value==='dark'?'☀':'◐';persistTheme(value)}
  const saved=storedTheme();setTheme(saved||(matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light'));
  themeBtn.addEventListener('click',()=>setTheme(document.documentElement.dataset.theme==='dark'?'light':'dark'));
  $('#menu').addEventListener('click',()=>document.body.classList.toggle('menu-open'));$('#backdrop').addEventListener('click',()=>document.body.classList.remove('menu-open'));
  $('#print').addEventListener('click',()=>window.print());

  const search=$('#search'), panel=$('#search-panel'); let searchItems=[];
  articles.forEach((a,key)=>{const p=pages.find(x=>x.key===key);searchItems.push({key,title:p.title,summary:p.summary,text:(a.textContent+' '+(p.tags||[]).join(' ')).toLowerCase()})});
  function doSearch(){const q=search.value.trim().toLowerCase();if(!q){panel.hidden=true;panel.innerHTML='';return}const tokens=q.split(/\s+/);const hits=searchItems.filter(x=>tokens.every(t=>x.text.includes(t))).slice(0,14);panel.innerHTML=hits.length?hits.map(x=>'<a class="search-result" href="#/'+x.key+'"><strong>'+escapeHtml(x.title)+'</strong><small>'+escapeHtml(x.summary)+'</small></a>').join(''):'<div class="search-empty">没有匹配页面；可尝试模块名、函数名或无线术语。</div>';panel.hidden=false}
  function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
  search.addEventListener('input',doSearch);search.addEventListener('keydown',e=>{if(e.key==='Escape'){search.value='';panel.hidden=true;search.blur()}});
  document.addEventListener('click',e=>{if(!e.target.closest('.search-wrap')&&!e.target.closest('.search-panel'))panel.hidden=true});
  document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();search.focus();search.select()}else if(e.key==='/'&&!/input|textarea/i.test(document.activeElement.tagName)){e.preventDefault();search.focus()}else if(e.altKey&&e.key.toLowerCase()==='d'){e.preventDefault();setReadingMode(readingMode==='detailed'?'compact':'detailed')}});
  window.addEventListener('scroll',()=>{const d=document.documentElement;const max=d.scrollHeight-d.clientHeight;progress.style.width=(max?100*d.scrollTop/max:0)+'%'});
  window.addEventListener('hashchange',route);route();
})();
"""


REFERENCE_PAGES = frozenset({"tests", "tools", "presets", "api", "glossary"})


def text_chars(fragment: str, *, compact: bool = False) -> int:
    """Approximate visible Chinese/English reading length for edition metadata."""
    text = fragment
    # Generated curve atlases and numeric audit tables are reference data, not prose.
    # Keep them visible in the detailed edition without letting 28 rows of numbers
    # manufacture an artificially high compact/detailed reading-depth ratio.
    text = re.sub(
        r'<section class="detail-data-atlas"[^>]*>.*?</section>',
        "", text, flags=re.S,
    )
    if compact:
        text = re.sub(
            r'<figcaption class="formula-explain">.*?</figcaption>',
            "", text, flags=re.S,
        )
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return len(re.sub(r"\s+", "", text))


def page_reading_stats(page: Page) -> tuple[int, int, float, str]:
    compact_chars = text_chars(page.body, compact=True)
    detailed_chars = text_chars(page.body + page.detail)
    ratio = detailed_chars / max(compact_chars, 1)
    kind = "reference" if page.key in REFERENCE_PAGES else "chapter"
    return compact_chars, detailed_chars, ratio, kind


def render_page(page: Page, index: int, pages: list[Page]) -> str:
    prev_page = pages[index - 1] if index else None
    next_page = pages[index + 1] if index + 1 < len(pages) else None
    tags = "".join(f'<span class="tag">{esc(tag)}</span>' for tag in page.tags[:10])
    prev_html = (
        f'<a href="#/{prev_page.key}"><small>← 上一页</small>{esc(prev_page.title)}</a>'
        if prev_page else "<span></span>"
    )
    next_html = (
        f'<a class="next" href="#/{next_page.key}"><small>下一页 →</small>{esc(next_page.title)}</a>'
        if next_page else "<span></span>"
    )
    compact_chars, detailed_chars, ratio, kind = page_reading_stats(page)
    reading_note = (
        "全量参考附录" if kind == "reference" else f"详细约 {ratio:.1f}×"
    )
    reading_control = (
        '<div class="reading-control"><span>阅读深度</span>'
        '<div class="depth-segment" role="group" aria-label="切换本手册阅读深度">'
        '<button type="button" data-reading-choice="compact" aria-pressed="true">精简</button>'
        '<button type="button" data-reading-choice="detailed" aria-pressed="false">详细</button>'
        '</div><small class="reading-status" aria-live="polite">'
        f'<b>精简版</b> · 约 {max(1, round(compact_chars / 430))} 分钟 · {reading_note}'
        '</small></div>'
    )
    return (
        f'<article class="doc-page" data-page="{esc(page.key)}" '
        f'data-compact-chars="{compact_chars}" data-detailed-chars="{detailed_chars}" hidden>'
        f'<header class="page-hero"><div class="eyebrow">{index + 1:02d} · {esc(page.eyebrow)}</div>'
        f'<h1>{esc(page.title)}</h1><p class="lead">{esc(page.summary)}</p>'
        f'<div class="tags">{tags}</div>{reading_control}</header>{page.body}{page.detail}'
        f'<nav class="page-nav" aria-label="前后章节">{prev_html}{next_html}</nav></article>'
    )


def build() -> str:
    modules = scan_modules()
    missing_detail_sources = [
        (key, path)
        for key, spec in DETAIL_SPECS.items()
        for path in spec.source_paths
        if not (ROOT / path).exists()
    ]
    if missing_detail_sources:
        raise RuntimeError(
            "developer-guide source path drift: "
            + ", ".join(f"{key}:{path}" for key, path in missing_detail_sources)
        )
    covered_modules, exempt_modules, missing_modules = detailed_module_coverage(modules)
    if missing_modules:
        raise RuntimeError(
            "developer-guide module coverage drift: missing detailed chapter for "
            + ", ".join(sorted(missing_modules))
        )
    tools = scan_tools(modules)
    tests = scan_tests()
    skills = scan_skills()
    presets = scan_presets()

    pages = [
        overview_page(modules, tools, tests, skills), quickstart_page(), architecture_page(),
        agentloop_page(), hardware_page(), channel_page(), raytracing_page(), antenna_page(),
        pdp_page(), reference_signals_page(), srs_page(), csi_page(), pmi_page(),
        measurements_page(modules), beamforming_page(), powercontrol_page(), robust_page(),
        sinr_page(), bfgain_page(), linkadapt_page(), bler_page(), mu_page(),
        modes_page(), experience_page(), traffic_page(), kpi_page(),
        calibration_page(), interference_page(), rng_page(), gates_page(),
        external_results_page(), tests_page(tests, modules),
        tools_page(tools), skill_page(skills), presets_page(presets), extension_page(),
        api_page(modules), limitations_page(), glossary_page(),
    ]
    page_keys = {page.key for page in pages}
    if page_keys != set(DETAIL_SPECS):
        missing = sorted(page_keys - set(DETAIL_SPECS))
        stale = sorted(set(DETAIL_SPECS) - page_keys)
        raise RuntimeError(
            f"developer-guide detailed chapter drift: missing={missing}, stale={stale}"
        )
    for page in pages:
        page.detail = render_detail(page.key, page.title)
        if page.detail_extra:
            closing = "</section>"
            if not page.detail.endswith(closing):
                raise RuntimeError(f"detail section for {page.key!r} has no closing tag")
            page.detail = page.detail[:-len(closing)] + page.detail_extra + closing
    groups: list[tuple[str, list[Page]]] = []
    for page in pages:
        if not groups or groups[-1][0] != page.group:
            groups.append((page.group, []))
        groups[-1][1].append(page)
    nav = []
    number = 1
    for group, members in groups:
        links = []
        for page in members:
            links.append(
                f'<a href="#/{page.key}" data-page="{page.key}"><span>{number:02d}</span>{esc(page.title)}</a>'
            )
            number += 1
        nav.append(f'<section class="nav-group"><h2>{esc(group)}</h2>{"".join(links)}</section>')

    page_json_rows = []
    for p in pages:
        compact_chars, detailed_chars, ratio, kind = page_reading_stats(p)
        page_json_rows.append({
            "key": p.key, "title": p.title, "summary": p.summary,
            "tags": list(p.tags), "compact_chars": compact_chars,
            "detailed_chars": detailed_chars,
            "detail_ratio": round(ratio, 3), "reading_kind": kind,
        })
    page_json = json.dumps(
        page_json_rows, ensure_ascii=False, separators=(",", ":")
    ).replace("</", "<\\/")
    meta = {
        "modules": len(modules),
        "source_lines": sum(m.lines for m in modules),
        "public_symbols": sum(len(m.symbols) for m in modules),
        "mcp_tools": len(tools),
        "test_files": len(tests),
        "test_lines": sum(t["lines"] for t in tests),
        "skill_files": len(skills),
        "logical_pages": len(pages),
        "detailed_pages": len(DETAIL_SPECS),
        "detailed_module_coverage": len(covered_modules),
        "detailed_module_exemptions": len(exempt_modules),
        "annotated_formulas": len(FORMULA_SPECS),
        "katex_inline": kx.available(),
    }
    meta_json = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
    articles = "".join(render_page(page, index, pages) for index, page in enumerate(pages))
    logo = (
        '<svg viewBox="0 0 40 40" aria-hidden="true"><rect width="40" height="40" rx="11" fill="#0b6b5d"/>'
        '<path d="M8 25c5-8 8-8 12 0s7 8 12 0M8 17c5-8 8-8 12 0s7 8 12 0" fill="none" stroke="#fff" stroke-width="2.5" stroke-linecap="round"/></svg>'
    )
    return (
        '<!doctype html>\n<html lang="zh-CN" data-theme="light" data-reading-mode="compact"><head><meta charset="utf-8">'
        '<link rel="icon" href="data:,">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="description" content="SuperRAN 开发者文档：无线物理、链路算法、系统仿真、MCP、Skill、API 与验证。">'
        '<title>SuperRAN 开发者文档</title>' + kx.head_assets()
        + '<style>' + DOC_CSS + '</style></head><body>'
        + '<a class="skip" href="#main">跳到正文</a><header class="topbar">'
        + '<button id="menu" class="icon-btn menu-btn" type="button" aria-label="打开目录">☰</button>'
        + '<a class="brand" href="#/overview">' + logo + '<span class="brand-text">SuperRAN<small>DEVELOPER GUIDE</small></span></a>'
        + '<div class="search-wrap"><span class="search-icon">⌕</span><input id="search" type="search" autocomplete="off" placeholder="搜索算法、公式、函数、模块…" aria-label="全文搜索"><span class="kbd">Ctrl K</span></div>'
        + '<div class="top-actions"><button id="reading-toggle" class="icon-btn depth-top" type="button" title="一键切换精简/详细版" aria-pressed="false">切到详细</button><button id="print" class="icon-btn print-btn" type="button" title="打印全部章节">⎙</button><button id="theme" class="icon-btn" type="button" title="切换主题">◐</button></div>'
        + '<div id="progress" class="progress"></div></header>'
        + '<aside class="sidebar" aria-label="章节目录">' + "".join(nav)
        + f'<div class="side-meta"><b>{len(pages)} 页 · {len(modules)} 模块 · {len(tools)} 工具</b><br>精简/详细双层 · 公式逐符号解释 · 源码可追溯</div></aside>'
        + '<div id="backdrop" class="backdrop"></div><div id="search-panel" class="search-panel" hidden></div>'
        + '<main id="main" class="main"><h1 id="doc-title" class="sr-only">SuperRAN</h1>' + articles
        + '<footer class="doc-footer">本页由 <code>scripts/make_developer_guide.py</code> 从当前源码、测试、预设与 Skill 自动构建。测试通过不等于现场标定完成。</footer></main>'
        + '<aside class="toc" aria-label="页内目录"><strong>本页目录</strong><nav id="toc-links"></nav></aside>'
        + '<script>window.__DOC_PAGES__=' + page_json + ';window.__DOC_META__=' + meta_json + ';</script>'
        + '<script>' + DOC_JS + '</script>' + kx.upgrade_script() + '</body></html>\n'
    )


def main() -> int:
    check = "--check" in sys.argv[1:]
    output = build()
    if check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != output:
            print(f"developer guide is stale: run {Path(__file__).name}", file=sys.stderr)
            return 1
        print(f"OK: {OUT} ({len(output):,} chars)")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(output, encoding="utf-8", newline="\n")
    meta_match = re.search(r"window\.__DOC_META__=(\{.*?\});", output)
    print(f"Wrote {OUT} ({len(output):,} chars) {meta_match.group(1) if meta_match else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
