"""Generate synthetic CARDINAL episode records for SLM clinical-context / etiology evaluation.
Each episode: synthetic PHI + 6-lead ECG (waveform + deterministic measurements) + PPG + SpO2 + BP + Temp
+ RR + a targeted lab panel + brief clinical context. Every episode is engineered around ONE data-supported
etiology (with deliberate distractors) so the SLM's output can be objectively graded.

Outputs:
  episodes/<id>.json   -> SLM INPUT (observed data only; NO answer)
  answer_key.json      -> ground-truth etiology + expected documentation + grading rubric (NOT shown to SLM)
  index.json           -> manifest
All identifiers/PHI are fictional. Waveforms are for the display widget; the SLM reasons on `ecg.measurements`.
"""
import numpy as np, json, os
OUT="/Users/kardiogenics/Documents/CardinalAI/SLM_Eval"
FS=250; DUR=8.0; PPG_FS=125
LEADS=["I","II","III","aVR","aVL","aVF"]
LEADF={"I":0.55,"II":1.0,"III":0.5,"aVR":-0.55,"aVL":0.35,"aVF":0.72}
rng=np.random.default_rng(7)

def g(t,c,a,w): return a*np.exp(-((t-c)**2)/(2*w*w))
def baseline(t): return 0.03*np.sin(2*np.pi*0.15*t)+rng.normal(0,0.012,len(t))

def beat(t,c,*,p=True,qrs_w=0.045,r=1.1,q=-0.12,s=-0.25,tw=0.30,ta=0.30,pr=0.16,twist=1.0):
    sig=np.zeros_like(t)
    if p: sig+=g(t,c-pr,0.14,0.020)                       # P
    sig+=g(t,c-qrs_w*1.2,q,0.012)                          # Q
    sig+=g(t,c,r*twist,qrs_w*0.5)                          # R
    sig+=g(t,c+qrs_w*1.1,s*twist,qrs_w*0.7)                # S
    sig+=g(t,c+0.20+tw*0.0,ta,tw)                          # T (tw=T width for QT)
    return sig

def rhythm_ii(kind):
    t=np.arange(0,DUR,1/FS); s=np.zeros_like(t)
    if kind=="sinus_pvc":                                  # sinus w/ frequent multifocal PVCs + a short run
        rr=60/78; beats=np.arange(0.4,DUR,rr).tolist()
        pvc_at={2.0,3.9,5.0,5.35,5.7}                      # couplet + triplet (NSVT run)
        for i,c in enumerate(beats):
            if any(abs(c-p)<0.05 for p in pvc_at):
                s+=beat(t,c,p=False,qrs_w=0.075,r=1.5,s=-0.6,tw=0.22,ta=-0.4)
            else: s+=beat(t,c)
        for c in pvc_at: s+=beat(t,c,p=False,qrs_w=0.08,r=1.6,s=-0.7,tw=0.22,ta=-0.45)
    elif kind=="vt_mono":                                  # monomorphic VT ~180, AV dissociation
        rr=60/180
        for c in np.arange(0.2,DUR,rr): s+=beat(t,c,p=False,qrs_w=0.085,r=1.3,q=0,s=-0.9,tw=0.20,ta=-0.5)
        for c in np.arange(0.1,DUR,60/95): s+=g(t,c,0.06,0.02)   # dissociated P
    elif kind=="torsades":                                 # polymorphic VT, twisting axis ~230
        rr=60/230; env=np.sin(2*np.pi*0.45*t)
        for i,c in enumerate(np.arange(0.15,DUR,rr)):
            tw=np.sin(2*np.pi*0.45*c)
            s+=beat(t,c,p=False,qrs_w=0.075,r=1.2*tw,q=0,s=-0.6*tw,tw=0.18,ta=-0.3*tw)
    elif kind=="vfib":                                     # chaotic, no organized complexes
        for f,a,ph in [(5.2,0.30,0.0),(6.4,0.22,1.1),(4.3,0.25,2.0),(7.1,0.15,0.5)]:
            s+=a*np.sin(2*np.pi*(f+0.6*np.sin(2*np.pi*0.3*t))*t+ph)
        s+=rng.normal(0,0.05,len(t))
    elif kind=="afib_rvr":                                 # irregularly irregular ~150, no P, fib baseline
        c=0.3; s+=0.05*np.sin(2*np.pi*7*t)*rng.uniform(0.5,1.5,len(t))
        while c<DUR:
            s+=beat(t,c,p=False,qrs_w=0.045,r=1.0,tw=0.28,ta=0.28); c+=(60/150)*rng.uniform(0.6,1.5)
    elif kind=="chb":                                      # complete heart block: atrial 84, escape 34, peaked T
        for c in np.arange(0.1,DUR,60/84): s+=g(t,c,0.13,0.02)          # independent P
        for c in np.arange(0.5,DUR,60/34): s+=beat(t,c,p=False,qrs_w=0.075,r=0.9,s=-0.4,tw=0.16,ta=0.75)  # wide QRS, tall peaked T
    elif kind=="junctional_brady":                         # junctional ~38, dig effect (scooped ST), no P
        for c in np.arange(0.3,DUR,60/38):
            s+=beat(t,c,p=False,qrs_w=0.05,r=0.9,tw=0.24,ta=0.12); s-=g(t,c+0.12,0.10,0.05)  # scooped ST
    elif kind=="svt":                                      # regular narrow ~190
        for c in np.arange(0.2,DUR,60/190): s+=beat(t,c,p=False,qrs_w=0.044,r=1.0,tw=0.22,ta=0.22)
    return t,s

def make_ecg(kind):
    t,ii=rhythm_ii(kind); wf={}
    for L in LEADS: wf[L]=[round(float(v),4) for v in (LEADF[L]*ii+baseline(t))]
    return {"leadNames":LEADS,"sampleRate":FS,"durationSeconds":DUR,"gridPaperSpeedMmPerSec":25,
            "gainMmPerMv":10,"waveform":wf}

def ppg(spo2,hr,perf=1.4,quality="good"):
    t=np.arange(0,DUR,1/PPG_FS); s=np.zeros_like(t); rr=60/max(hr,30); c=0.2
    while c<DUR:
        s+=g(t,c,1.0,0.05)+g(t,c+0.18,0.35,0.06)          # systolic peak + dicrotic notch
        c+=rr*(rng.uniform(0.9,1.1) if quality=="good" else rng.uniform(0.5,1.6))
    if quality!="good": s+=rng.normal(0,0.3,len(t))         # motion/low-perfusion artifact
    return {"sampleRate":PPG_FS,"durationSeconds":DUR,"unit":"a.u.","perfusionIndexPct":perf,
            "signalQuality":quality,"spo2Pct":spo2,"waveform":[round(float(v),4) for v in s]}

def lab(v,unit,ref,flag=""): return {"value":v,"unit":unit,"reference":ref,"flag":flag}

# ---------------- episode specifications (input + ground truth) ----------------
E=[]
def add(**k): E.append(k)

add(id="VFIB-STEMI-001", kind="vfib", type="ventricular_fibrillation",
 display="Ventricular fibrillation", severity="critical", trigHR=None,
 patient=dict(name="Robert D. Hale",mrn="KG-004417",dob="1957-03-11",age=68,sex="M",heightCm=178,weightKg=91,
   room="CCU-3",admissionDate="2026-07-19",codeStatus="Full code",
   primaryDiagnosis="Acute chest pain, rule-out ACS",
   history=["Coronary artery disease","Prior anterior MI (2019, DES to LAD)","Hyperlipidemia","Type 2 diabetes","Former smoker"],
   homeMedications=["Aspirin 81 mg","Atorvastatin 80 mg","Metoprolol 50 mg","Metformin 1000 mg"],
   allergies=["None known"], infusions=["Nitroglycerin IV (started for chest pain)"]),
 meas=dict(rhythm="Ventricular fibrillation",ventricularRateBpm=None,atrialRateBpm=None,regularity="chaotic/irregular",
   qrsDurationMs=None,qtcMs=None,prMs=None,axisDeg=None,pWavePresent=False,
   stDeviationMm="ST elevation 4 mm V2–V4 on rhythm strip 90 s pre-arrest",
   morphology="No organized QRS; coarse fibrillatory waves ~5 Hz",ectopyPer10s=None,
   preEventNote="Runs of PVCs then R-on-T preceded onset; 12-lead 8 min prior showed anterior ST elevation"),
 vit=dict(spo2=None,heartRate=None,rr=None,tempC=36.9,bp=dict(systolic=None,diastolic=None,map=None,note="No pulse; arterial line flat")),
 ppg=dict(spo2=None,hr=0,perf=0.0,quality="no_pulse"),
 labs=dict(troponinT=lab(1.85,"ng/mL","<0.014","CRITICAL HIGH"),ck_mb=lab(48,"ng/mL","0–5","HIGH"),
   potassium=lab(4.1,"mmol/L","3.5–5.1"),magnesium=lab(2.0,"mg/dL","1.7–2.2"),
   glucose=lab(212,"mg/dL","70–140","HIGH"),creatinine=lab(1.1,"mg/dL","0.7–1.3"),
   lactate=lab(4.8,"mmol/L","0.5–2.2","HIGH"),ph=lab(7.21,"","7.35–7.45","LOW")),
 context=["Sudden loss of consciousness on monitor","Preceded by crescendo chest pain and ST elevation","BLS/ACLS in progress"],
 gt=dict(primaryEtiology="Acute ST-elevation myocardial infarction (anterior/LAD) precipitating ventricular fibrillation arrest",
   mechanism="Ischemia-driven electrical instability (R-on-T) degenerating to VF",
   contributing=["Known CAD with prior LAD stent","Markedly elevated troponin T / CK-MB","Anterior ST elevation pre-arrest","Metabolic acidosis/elevated lactate from arrest"],
   mustIdentify=["Ventricular fibrillation","Shockable / pulseless arrest"],
   mustRecommend=["Immediate defibrillation","ACLS","Emergent coronary angiography / PCI","Antiplatelet/anticoagulation per ACS"],
   distractors=["Do NOT attribute to hyperkalemia (K normal 4.1)","Do NOT call it a benign arrhythmia"]))

add(id="TORSADES-LQT-002", kind="torsades", type="polymorphic_vt",
 display="Polymorphic VT (Torsades de pointes)", severity="critical", trigHR=228,
 patient=dict(name="Margaret A. Sullivan",mrn="KG-004420",dob="1951-09-02",age=74,sex="F",heightCm=161,weightKg=58,
   room="TELE-12",admissionDate="2026-07-18",codeStatus="Full code",
   primaryDiagnosis="Palpitations and syncope",
   history=["Atrial fibrillation (on sotalol)","Hypertension","Recent gastroenteritis with vomiting/diarrhea"],
   homeMedications=["Sotalol 120 mg BID","Lisinopril 20 mg"],
   allergies=["Penicillin"], infusions=[]),
 meas=dict(rhythm="Polymorphic ventricular tachycardia (twisting axis)",ventricularRateBpm=228,atrialRateBpm=None,regularity="irregular",
   qrsDurationMs=150,qtcMs=None,prMs=None,axisDeg=None,pWavePresent=False,
   stDeviationMm="N/A during run",morphology="Sinusoidal amplitude modulation with axis twisting",ectopyPer10s=None,
   preEventNote="Baseline ECG 6 min prior: sinus 62, marked QT prolongation QTc 618 ms, prominent U waves"),
 vit=dict(spo2=88,heartRate=228,rr=24,tempC=36.7,bp=dict(systolic=68,diastolic=40,map=49,note="Hypotensive during run")),
 ppg=dict(spo2=88,hr=228,perf=0.3,quality="poor"),
 labs=dict(potassium=lab(2.9,"mmol/L","3.5–5.1","LOW"),magnesium=lab(1.2,"mg/dL","1.7–2.2","LOW"),
   calcium=lab(7.9,"mg/dL","8.5–10.2","LOW"),troponinT=lab(0.02,"ng/mL","<0.014","mildly high (rate-related)"),
   creatinine=lab(1.4,"mg/dL","0.6–1.1","HIGH"),tsh=lab(2.1,"mIU/L","0.4–4.0")),
 context=["Recent added azithromycin (5 d) and ondansetron for gastroenteritis","Vomiting/diarrhea x3 days","Syncopal episode witnessed"],
 gt=dict(primaryEtiology="Acquired long-QT syndrome causing Torsades de pointes",
   mechanism="QT prolongation from combined QT-prolonging drugs (sotalol + azithromycin + ondansetron) and hypokalemia/hypomagnesemia",
   contributing=["QTc 618 ms on baseline ECG","Hypokalemia 2.9 and hypomagnesemia 1.2","GI losses from gastroenteritis","Multiple QT-prolonging medications"],
   mustIdentify=["Polymorphic VT / Torsades de pointes","Preceding prolonged QT"],
   mustRecommend=["IV magnesium sulfate","Aggressive K+/Mg repletion","Stop all QT-prolonging drugs (sotalol, azithromycin, ondansetron)","Consider overdrive pacing/isoproterenol; defibrillate if sustained/unstable"],
   distractors=["Not ischemic monomorphic VT (troponin only mildly up, rate-related)","Not primarily electrolyte-independent"]))

add(id="VT-ISCHEMIC-003", kind="vt_mono", type="monomorphic_vt",
 display="Monomorphic ventricular tachycardia", severity="critical", trigHR=182,
 patient=dict(name="James P. Whitfield",mrn="KG-004432",dob="1964-12-20",age=61,sex="M",heightCm=175,weightKg=84,
   room="CVICU-6",admissionDate="2026-07-15",codeStatus="Full code",
   primaryDiagnosis="Decompensated ischemic cardiomyopathy",
   history=["Ischemic cardiomyopathy, LVEF 25%","Prior inferior MI","ICD evaluation pending","CKD stage 3"],
   homeMedications=["Carvedilol 25 mg","Sacubitril-valsartan","Spironolactone 25 mg","Furosemide 40 mg"],
   allergies=["None known"], infusions=["Furosemide drip"]),
 meas=dict(rhythm="Monomorphic ventricular tachycardia",ventricularRateBpm=182,atrialRateBpm=96,regularity="regular",
   qrsDurationMs=168,qtcMs=None,prMs=None,axisDeg=-120,pWavePresent=True,
   stDeviationMm="Discordant (rate-related)",morphology="Wide monomorphic QRS, AV dissociation, occasional capture/fusion beats",ectopyPer10s=None,
   preEventNote="Baseline: sinus with LBBB-like conduction, LVEF 25%, prior QTc 460 ms (normal — argues against Torsades)"),
 vit=dict(spo2=94,heartRate=182,rr=22,tempC=36.8,bp=dict(systolic=88,diastolic=58,map=68,note="Borderline; symptomatic but conscious")),
 ppg=dict(spo2=94,hr=182,perf=0.8,quality="fair"),
 labs=dict(potassium=lab(4.3,"mmol/L","3.5–5.1"),magnesium=lab(2.1,"mg/dL","1.7–2.2"),
   troponinT=lab(0.09,"ng/mL","<0.014","mildly HIGH (demand)"),bnp=lab(1840,"pg/mL","<100","HIGH"),
   creatinine=lab(1.9,"mg/dL","0.7–1.3","HIGH")),
 context=["Progressive dyspnea and volume overload","No acute chest pain","AV dissociation with fusion beats seen"],
 gt=dict(primaryEtiology="Scar-related reentrant monomorphic VT in ischemic cardiomyopathy",
   mechanism="Reentry around myocardial scar from prior infarct; not acute ischemia",
   contributing=["Ischemic cardiomyopathy LVEF 25% with prior infarct","AV dissociation/fusion beats confirming VT","Only mild demand troponin (no STEMI)","Electrolytes normal"],
   mustIdentify=["Monomorphic VT (wide, regular, AV dissociation)","Hemodynamically borderline but conscious"],
   mustRecommend=["Amiodarone or lidocaine","Synchronized cardioversion if it becomes unstable","Electrophysiology / ICD","Optimize heart failure"],
   distractors=["Not SVT with aberrancy (AV dissociation + fusion beats present)","Not acute STEMI (troponin only mildly elevated, no ST elevation)","Not Torsades (monomorphic, normal QT/K/Mg)"]))

add(id="AFIB-RVR-SEPSIS-004", kind="afib_rvr", type="atrial_fibrillation_rvr",
 display="Atrial fibrillation with rapid ventricular response", severity="warning", trigHR=150,
 patient=dict(name="Dorothy E. Chen",mrn="KG-004440",dob="1946-06-30",age=79,sex="F",heightCm=157,weightKg=62,
   room="MICU-2",admissionDate="2026-07-20",codeStatus="Full code",
   primaryDiagnosis="Urosepsis",
   history=["Hypertension","Chronic kidney disease","No prior atrial fibrillation"],
   homeMedications=["Amlodipine 5 mg"], allergies=["Sulfa"], infusions=["Norepinephrine (low dose)","Piperacillin-tazobactam","IV crystalloid"]),
 meas=dict(rhythm="Atrial fibrillation with RVR (new onset)",ventricularRateBpm=150,atrialRateBpm=None,regularity="irregularly irregular",
   qrsDurationMs=92,qtcMs=430,prMs=None,axisDeg=40,pWavePresent=False,
   stDeviationMm="Rate-related ST depression II/V5",morphology="Narrow QRS, absent P waves, fibrillatory baseline",ectopyPer10s=None,
   preEventNote="Sinus tachycardia earlier in shift; converted to AFib RVR as fever spiked"),
 vit=dict(spo2=93,heartRate=150,rr=28,tempC=38.9,bp=dict(systolic=92,diastolic=54,map=67,note="On low-dose norepinephrine")),
 ppg=dict(spo2=93,hr=150,perf=0.9,quality="fair"),
 labs=dict(wbc=lab(18.4,"10^3/uL","4–11","HIGH"),lactate=lab(3.1,"mmol/L","0.5–2.2","HIGH"),
   potassium=lab(3.6,"mmol/L","3.5–5.1"),magnesium=lab(1.8,"mg/dL","1.7–2.2"),
   creatinine=lab(1.7,"mg/dL","0.6–1.1","HIGH"),tsh=lab(1.8,"mIU/L","0.4–4.0"),
   procalcitonin=lab(6.2,"ng/mL","<0.5","HIGH"),troponinT=lab(0.03,"ng/mL","<0.014","mild")),
 context=["Fever to 38.9, rigors","Urinalysis positive, blood cultures pending","New irregular tachycardia coincident with fever spike"],
 gt=dict(primaryEtiology="New-onset atrial fibrillation with RVR triggered by sepsis (urosepsis)",
   mechanism="Systemic inflammation, catecholamine surge, fever and volume shifts precipitating AFib in a susceptible atrium",
   contributing=["Fever 38.9, WBC 18.4, procalcitonin 6.2, lactate 3.1 (sepsis)","No prior AFib","On vasopressor for septic shock"],
   mustIdentify=["Atrial fibrillation with rapid ventricular response","Sepsis as the trigger"],
   mustRecommend=["Treat the underlying sepsis (antibiotics, source control, fluids)","Rate control cautiously given hypotension (avoid worsening shock)","Correct Mg/K","Anticoagulation risk/benefit assessment"],
   distractors=["Not primary cardiac arrhythmia to cardiovert first-line (treat sepsis)","Not thyrotoxicosis (TSH normal)","Not ACS (troponin only mildly up)"]))

add(id="CHB-HYPERK-005", kind="chb", type="complete_heart_block",
 display="Complete heart block with bradycardia", severity="critical", trigHR=34,
 patient=dict(name="Walter J. Osei",mrn="KG-004451",dob="1955-01-14",age=70,sex="M",heightCm=172,weightKg=80,
   room="ED-Resus-1",admissionDate="2026-07-21",codeStatus="Full code",
   primaryDiagnosis="Missed hemodialysis, weakness",
   history=["End-stage renal disease on hemodialysis (MWF)","Hypertension","Heart failure"],
   homeMedications=["Lisinopril 20 mg","Spironolactone 25 mg","Sevelamer"], allergies=["None known"],
   infusions=[]),
 meas=dict(rhythm="Complete (third-degree) AV block with slow ventricular escape",ventricularRateBpm=34,atrialRateBpm=84,regularity="regular (dissociated)",
   qrsDurationMs=150,qtcMs=None,prMs=None,axisDeg=None,pWavePresent=True,
   stDeviationMm="N/A",morphology="AV dissociation; wide QRS escape; tall peaked symmetric T waves; flattened P waves",ectopyPer10s=None,
   preEventNote="Progressive PR prolongation then AV dissociation; QRS widening over 20 min"),
 vit=dict(spo2=95,heartRate=34,rr=18,tempC=36.5,bp=dict(systolic=78,diastolic=50,map=59,note="Symptomatic bradycardia")),
 ppg=dict(spo2=95,hr=34,perf=1.0,quality="fair"),
 labs=dict(potassium=lab(7.4,"mmol/L","3.5–5.1","CRITICAL HIGH"),bicarbonate=lab(15,"mmol/L","22–29","LOW"),
   creatinine=lab(9.8,"mg/dL","0.7–1.3","CRITICAL HIGH"),bun=lab(88,"mg/dL","7–20","HIGH"),
   calcium=lab(7.6,"mg/dL","8.5–10.2","LOW"),ph=lab(7.24,"","7.35–7.45","LOW"),glucose=lab(118,"mg/dL","70–140")),
 context=["Missed last 2 dialysis sessions","Generalized weakness","Peaked T waves and QRS widening on monitor"],
 gt=dict(primaryEtiology="Severe hyperkalemia (K 7.4) from missed dialysis causing high-grade AV block and bradycardia",
   mechanism="Hyperkalemia depresses conduction — peaked T waves, QRS widening, AV block, bradycardia",
   contributing=["K 7.4 with peaked T waves and wide QRS","Missed hemodialysis, ESRD, Cr 9.8","Potassium-sparing meds (lisinopril, spironolactone)","Metabolic acidosis"],
   mustIdentify=["High-grade/complete AV block with bradycardia","Hyperkalemic ECG changes (peaked T, wide QRS)"],
   mustRecommend=["IV calcium (membrane stabilization) immediately","Insulin+glucose, albuterol, bicarbonate for shift","Emergent hemodialysis","Hold lisinopril/spironolactone; transcutaneous pacing if needed"],
   distractors=["Do NOT treat as isolated conduction disease needing only a pacemaker — reverse hyperkalemia first","Not primarily ischemic"]))

add(id="BRADY-DIGTOX-006", kind="junctional_brady", type="junctional_bradycardia",
 display="Junctional bradycardia (digoxin effect)", severity="warning", trigHR=38,
 patient=dict(name="Eleanor R. Vance",mrn="KG-004466",dob="1943-08-08",age=82,sex="F",heightCm=155,weightKg=54,
   room="TELE-4",admissionDate="2026-07-19",codeStatus="DNR/DNI",
   primaryDiagnosis="Nausea, vomiting, confusion",
   history=["Atrial fibrillation (rate-controlled on digoxin)","Heart failure","CKD stage 3"],
   homeMedications=["Digoxin 0.25 mg daily","Furosemide 40 mg","Warfarin"], allergies=["None known"], infusions=[]),
 meas=dict(rhythm="Regularized junctional bradycardia (AF with slow regular ventricular response)",ventricularRateBpm=38,atrialRateBpm=None,regularity="regular",
   qrsDurationMs=96,qtcMs=None,prMs=None,axisDeg=None,pWavePresent=False,
   stDeviationMm="Scooped/'sagging' ST segments (digoxin effect)",morphology="Narrow QRS, scooped ST, slow regular rhythm despite AFib",ectopyPer10s=None,
   preEventNote="Known AFib now with slow REGULAR response — suggests AV junctional takeover"),
 vit=dict(spo2=96,heartRate=38,rr=16,tempC=36.6,bp=dict(systolic=104,diastolic=62,map=76,note="Mildly symptomatic")),
 ppg=dict(spo2=96,hr=38,perf=1.3,quality="good"),
 labs=dict(digoxinLevel=lab(3.8,"ng/mL","0.8–2.0","TOXIC"),potassium=lab(5.6,"mmol/L","3.5–5.1","HIGH"),
   creatinine=lab(2.1,"mg/dL","0.6–1.1","HIGH (acute on CKD)"),magnesium=lab(2.0,"mg/dL","1.7–2.2"),
   inr=lab(2.4,"","2.0–3.0")),
 context=["Nausea, vomiting, visual disturbance (yellow-green halos)","Recent decreased urine output / dehydration","AFib now slow and regular"],
 gt=dict(primaryEtiology="Digoxin toxicity (level 3.8) precipitated by acute kidney injury",
   mechanism="Digoxin accumulation with AKI causing increased vagal tone/AV block — regularized AF, junctional bradycardia, GI and visual symptoms",
   contributing=["Digoxin level 3.8 (toxic)","AKI (Cr 2.1) reducing clearance","Hyperkalemia 5.6 (marker of significant toxicity)","Classic symptoms: nausea/vomiting, yellow-green vision","Regularized AFib + scooped ST (dig effect)"],
   mustIdentify=["Digoxin toxicity","Junctional/regularized bradycardia with dig effect"],
   mustRecommend=["Hold digoxin","Digoxin-specific antibody fragments (DigiFab) for significant toxicity/hyperkalemia","Correct AKI/hydration","Avoid calcium for the hyperkalemia if possible; treat toxicity","Monitor; atropine/pacing if symptomatic"],
   distractors=["Do NOT treat hyperkalemia 5.6 with IV calcium reflexively (relative caution in dig toxicity)","Not primary sinus node disease"]))

add(id="SVT-PSVT-007", kind="svt", type="svt",
 display="Supraventricular tachycardia (regular narrow-complex)", severity="warning", trigHR=190,
 patient=dict(name="Aisha N. Rahman",mrn="KG-004470",dob="1992-04-25",age=34,sex="F",heightCm=166,weightKg=63,
   room="ED-8",admissionDate="2026-07-21",codeStatus="Full code",
   primaryDiagnosis="Palpitations",
   history=["Prior similar episodes self-terminating","Otherwise healthy"],
   homeMedications=["None"], allergies=["None known"], infusions=[]),
 meas=dict(rhythm="Regular narrow-complex tachycardia (SVT, likely AVNRT)",ventricularRateBpm=190,atrialRateBpm=190,regularity="regular",
   qrsDurationMs=88,qtcMs=400,prMs=None,axisDeg=60,pWavePresent=False,
   stDeviationMm="Minimal rate-related",morphology="Narrow QRS, no clear P waves, abrupt onset",ectopyPer10s=None,
   preEventNote="Abrupt onset after exertion/caffeine; hemodynamically stable"),
 vit=dict(spo2=98,heartRate=190,rr=18,tempC=36.8,bp=dict(systolic=118,diastolic=76,map=90,note="Stable")),
 ppg=dict(spo2=98,hr=190,perf=1.6,quality="good"),
 labs=dict(potassium=lab(4.0,"mmol/L","3.5–5.1"),magnesium=lab(2.0,"mg/dL","1.7–2.2"),
   tsh=lab(2.2,"mIU/L","0.4–4.0"),hemoglobin=lab(13.4,"g/dL","12–16"),troponinT=lab(0.01,"ng/mL","<0.014")),
 context=["Two cups of coffee, then sudden palpitations","No chest pain or syncope","History of self-terminating SVT"],
 gt=dict(primaryEtiology="Paroxysmal supraventricular tachycardia (AVNRT)",
   mechanism="AV-nodal reentry; benign in a young healthy patient, caffeine as trigger",
   contributing=["Young healthy patient with recurrent self-terminating episodes","Caffeine trigger","Normal electrolytes, thyroid, troponin"],
   mustIdentify=["Regular narrow-complex SVT","Hemodynamically stable"],
   mustRecommend=["Vagal maneuvers first","IV adenosine if vagal fails","Rate-limiting agents / EP referral for recurrence"],
   distractors=["Not VT (narrow QRS)","Not sinus tachycardia from sepsis/hypovolemia (abrupt onset, no fever, normal labs)","Not thyrotoxicosis (TSH normal)"]))

add(id="NSVT-ECTOPY-008", kind="sinus_pvc", type="ventricular_ectopy",
 display="Frequent PVCs with non-sustained VT run", severity="warning", trigHR=78,
 patient=dict(name="Thomas B. Nguyen",mrn="KG-004488",dob="1970-11-03",age=55,sex="M",heightCm=180,weightKg=88,
   room="TELE-9",admissionDate="2026-07-20",codeStatus="Full code",
   primaryDiagnosis="Post-op monitoring (day 1 after abdominal surgery)",
   history=["Hypertension","Mild diastolic dysfunction"],
   homeMedications=["Hydrochlorothiazide 25 mg","Lisinopril 10 mg"], allergies=["None known"], infusions=["Maintenance IV fluids"]),
 meas=dict(rhythm="Sinus rhythm with frequent multifocal PVCs and one 4-beat NSVT run",ventricularRateBpm=78,atrialRateBpm=78,regularity="occasionally irregular",
   qrsDurationMs=94,qtcMs=445,prMs=160,axisDeg=30,pWavePresent=True,
   stDeviationMm="None significant",morphology="Sinus beats with multifocal wide PVCs, couplets, one non-sustained VT run of 4 beats",ectopyPer10s=6,
   preEventNote="Post-op, on thiazide; PVC burden increasing over last hour"),
 vit=dict(spo2=96,heartRate=78,rr=16,tempC=37.0,bp=dict(systolic=128,diastolic=80,map=96,note="Stable")),
 ppg=dict(spo2=96,hr=78,perf=1.5,quality="good"),
 labs=dict(potassium=lab(3.1,"mmol/L","3.5–5.1","LOW"),magnesium=lab(1.5,"mg/dL","1.7–2.2","LOW"),
   troponinT=lab(0.02,"ng/mL","<0.014","mild post-op"),creatinine=lab(1.0,"mg/dL","0.7–1.3"),
   calcium=lab(8.8,"mg/dL","8.5–10.2")),
 context=["Post-operative day 1","On thiazide diuretic (K/Mg wasting)","PVC burden rising; brief NSVT run, asymptomatic"],
 gt=dict(primaryEtiology="Electrolyte-provoked ventricular ectopy / NSVT (hypokalemia + hypomagnesemia)",
   mechanism="Hypokalemia and hypomagnesemia (thiazide-induced, post-op) increasing ventricular irritability",
   contributing=["K 3.1 and Mg 1.5 (thiazide + post-op)","Rising PVC burden with a short NSVT run","Hemodynamically stable, asymptomatic"],
   mustIdentify=["Frequent PVCs with non-sustained VT","Currently non-sustained / stable"],
   mustRecommend=["Replete potassium and magnesium","Continue telemetry","Reassess PVC burden after correction; avoid unnecessary antiarrhythmics"],
   distractors=["Not sustained VT requiring cardioversion (non-sustained, stable)","Not acute ischemia (troponin minimal, no ST changes)"]))

# ---------------- emit ----------------
os.makedirs(os.path.join(OUT,"episodes"),exist_ok=True)
manifest=[]; answers={}
for i,e in enumerate(E):
    ecg=make_ecg(e["kind"]); p=e["ppg"]
    rec={"schemaVersion":"episode-slm-eval-v1","episodeId":e["id"],
         "incidentId":f"inc-{e['id']}","capturedAt":"2026-07-21T03:05:00+00:00",
         "detectionSource":"CARDINAL continuous monitor (8-in-1 wearable)","mode":"demo",
         "patient":{"disclaimer":"SYNTHETIC / FICTIONAL PHI — demo only",**e["patient"]},
         "episode":{"type":e["type"],"display":e["display"],"severity":e["severity"],
                    "state":"CAPTURED","analysisStatus":"pending","autoTriggered":True,
                    "triggerHeartRate":e["trigHR"],"durationSeconds":e.get("durationSec",DUR)},
         "ecg":{**ecg,"measurements":e["meas"]},
         "ppg":ppg(p["spo2"],p["hr"],p["perf"],p["quality"]),
         "vitals":{"spo2Pct":e["vit"]["spo2"],"heartRateBpm":e["vit"]["heartRate"],
                   "respiratoryRateBpm":e["vit"]["rr"],"temperatureC":e["vit"]["tempC"],
                   "bloodPressure":e["vit"]["bp"]},
         "labs":e["labs"],
         "clinicalContext":{"recentEvents":e["context"]}}
    json.dump(rec,open(os.path.join(OUT,"episodes",e["id"]+".json"),"w"),indent=2)
    manifest.append({"episodeId":e["id"],"display":e["display"],"severity":e["severity"],
                     "file":f"episodes/{e['id']}.json","patient":e["patient"]["name"]})
    answers[e["id"]]={"display":e["display"],**e["gt"]}

json.dump({"schemaVersion":"slm-eval-manifest-v1","count":len(E),
           "purpose":"Evaluate SLM clinical documentation, clinical context and etiology reasoning",
           "episodes":manifest},open(os.path.join(OUT,"index.json"),"w"),indent=2)
json.dump({"schemaVersion":"slm-eval-answerkey-v1",
           "note":"Ground truth + grading rubric. DO NOT feed to the SLM. Score each episode on the rubric below.",
           "scoring":{"rhythm_identification":25,"primary_etiology":30,"contributing_factors":20,
                      "recommended_actions":20,"avoids_distractors":5,"total":100},
           "episodes":answers},open(os.path.join(OUT,"answer_key.json"),"w"),indent=2)
print(f"generated {len(E)} episodes")
for m in manifest: print(" ",m["episodeId"],"—",m["display"],f"({m['patient']})")
