import os, re, sys, json, queue, ctypes, ctypes.wintypes, threading, shutil
from pathlib import Path
from datetime import datetime
import tkinter as tk
from tkinter import messagebox, filedialog
from PIL import Image, ImageTk, ImageGrab, ImageOps, ImageEnhance, ImageFilter
import pytesseract

APP='PROMAD Capturador + Nota'
VER='1.0.0'
BG='#0b1327'; PANEL='#141f39'; CARD='#192641'; TEXT='#edf5ff'; MUTED='#8797b2'; CYAN='#00c8e8'; GREEN='#24bb6a'; YELLOW='#f0c933'; RED='#ff3b3b'
ROOT=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
DATA=Path(os.getenv('LOCALAPPDATA',Path.home()))/'PROMAD_Capturador'
CAP=DATA/'capturas'; DB=DATA/'incidentes.json'
for d in (DATA,CAP): d.mkdir(parents=True,exist_ok=True)


def now(): return datetime.now().isoformat(timespec='seconds')
def clean(v): return re.sub(r'\s+',' ',v or '').strip()
def tesseract_exe():
    for p in [ROOT/'tesseract'/'tesseract.exe', Path(r'C:\Program Files\Tesseract-OCR\tesseract.exe')]:
        if p.exists(): return str(p)
    return shutil.which('tesseract') or ''

def parse(text):
    raw=text.replace('\\','/').replace('—','-').replace('–','-')
    lines=[clean(x) for x in raw.splitlines() if clean(x)]
    joined='\n'.join(lines)
    folio=''; municipio=''; tip=''; hora=''
    fi=-1
    for i,line in enumerate(lines):
        u=line.upper().replace('O','0')
        m=re.search(r'(?:C\s*[S5]|CS|C5|G5|S5)\s*[/I1-]?\s*([0-9]{8})\s*[/I1-]?\s*([0-9]{4,7})',u)
        if m:
            folio=f"C5/{m.group(1)}/{m.group(2)}"; fi=i
            after=line[m.end():].strip(' -_:')
            municipio=re.split(r'\b(?:ACCIDENTE|ROBO|VIOLENCIA|INCENDIO|PERSONA|VEH[IÍ]CULO|DETONACI[ÓO]N)\b',after.upper())[0].strip(' -')
            break
    mt=re.search(r'(?<!\d)([0-2]?\d:[0-5]\d(?::[0-5]\d)?)(?!\d)',joined)
    if mt: hora=mt.group(1)
    if fi>=0:
        cand=[]
        for line in lines[fi+1:fi+4]:
            if hora and hora in line: break
            if not re.search(r'\d{2}:\d{2}',line): cand.append(line)
        tip=clean(' '.join(cand)).upper()
    if not tip:
        keys=('ACCIDENTE','ROBO','VIOLENCIA','INCENDIO','PERSONA','VEHICULO','VEHÍCULO','DETONACION','DETONACIÓN','LESION','LESIÓN','HOMICIDIO','AMENAZA')
        for line in lines:
            u=line.upper()
            if any(k in u for k in keys): tip=u; break
    def labeled(*names):
        for n in names:
            m=re.search(rf'(?im)^\s*{n}\s*[:\-]\s*(.+)$',joined)
            if m:return clean(m.group(1))
        return ''
    return {
      'folio':folio,'municipio':municipio,'tipificacion':tip,'hora':hora,
      'direccion':labeled('DIRECCI[ÓO]N','DOMICILIO','UBICACI[ÓO]N'),
      'colonia':labeled('COLONIA','BARRIO','FRACC(?:IONAMIENTO)?'),
      'corporacion':labeled('CORPORACI[ÓO]N','INSTITUCI[ÓO]N'),
      'unidad':labeled('UNIDAD','M[ÓO]VIL'),
      'narrativa':labeled('NARRATIVA','HECHOS','DESCRIPCI[ÓO]N'),
      'capturado_en':now(),'ocr':joined}

def note(d):
    rows=['NOTA INFORMATIVA','',f"FECHA: {datetime.now().strftime('%d/%m/%Y')}"]
    labels=[('folio','FOLIO PROMAD'),('municipio','MUNICIPIO'),('tipificacion','TIPIFICACIÓN'),('hora','HORA PROMAD'),('direccion','DIRECCIÓN'),('colonia','COLONIA'),('corporacion','CORPORACIÓN'),('unidad','UNIDAD')]
    for k,l in labels:
        if d.get(k): rows.append(f'{l}: {d[k]}')
    if d.get('narrativa'): rows += ['','HECHOS:',d['narrativa']]
    rows += ['','FUENTE: PROMAD (captura OCR local).',f"CAPTURADO: {d.get('capturado_en') or now()}"]
    return '\n'.join(rows)

def save_db(d):
    try: db=json.loads(DB.read_text(encoding='utf-8')) if DB.exists() else {}
    except: db={}
    key=d.get('folio') or 'SIN_FOLIO_'+datetime.now().strftime('%Y%m%d_%H%M%S')
    old=db.get(key,{})
    for k,v in d.items():
        if v: old[k]=v
    old['actualizado_en']=now(); db[key]=old
    DB.write_text(json.dumps(db,ensure_ascii=False,indent=2),encoding='utf-8')
    return key

class Hotkeys(threading.Thread):
    def __init__(self,q): super().__init__(daemon=True); self.q=q
    def run(self):
        if os.name!='nt': return
        u=ctypes.windll.user32; MOD_NOREPEAT=0x4000; WM_HOTKEY=0x0312
        u.RegisterHotKey(None,1,MOD_NOREPEAT,0x77); u.RegisterHotKey(None,2,MOD_NOREPEAT,0x78)
        msg=ctypes.wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg),None,0,0)!=0:
            if msg.message==WM_HOTKEY:self.q.put('card' if msg.wParam==1 else 'window')

class App(tk.Tk):
    def __init__(self):
        super().__init__(); self.title(f'{APP} {VER}'); self.geometry('1320x820'); self.minsize(1080,680); self.configure(bg=BG)
        self.img=None; self.imgtk=None; self.capture_path=''; self.q=queue.Queue(); self.vars={k:tk.StringVar() for k in ['folio','municipio','tipificacion','hora','direccion','colonia','corporacion','unidad','capturado_en']}
        self.build(); self.after(120,self.poll); Hotkeys(self.q).start(); self.refresh_ocr()
    def btn(self,p,t,cmd,c=CYAN): return tk.Button(p,text=t,command=cmd,bg=c,fg='white',activebackground=c,activeforeground='white',relief='flat',bd=0,padx=12,pady=8,font=('Segoe UI',9,'bold'),cursor='hand2')
    def build(self):
        top=tk.Frame(self,bg=PANEL,height=58); top.pack(fill='x'); top.pack_propagate(False)
        tk.Label(top,text='PROMAD  •  CAPTURADOR + NOTA INFORMATIVA',bg=PANEL,fg=TEXT,font=('Segoe UI',14,'bold')).pack(side='left',padx=16,pady=16)
        self.ocr=tk.Label(top,text='OCR',bg=PANEL,fg=MUTED,font=('Segoe UI',9,'bold')); self.ocr.pack(side='right',padx=16)
        bar=tk.Frame(self,bg=BG); bar.pack(fill='x',padx=14,pady=10)
        self.btn(bar,'F8  CAPTURAR TARJETA',self.cap_card).pack(side='left',padx=4); self.btn(bar,'F9  CAPTURAR VENTANA',self.cap_window).pack(side='left',padx=4); self.btn(bar,'ABRIR IMAGEN',self.open_img,'#394866').pack(side='left',padx=4)
        self.status=tk.Label(bar,text='Pon el cursor sobre un incidente de PROMAD y presiona F8.',bg=BG,fg=MUTED,font=('Segoe UI',9)); self.status.pack(side='left',padx=14)
        body=tk.Frame(self,bg=BG); body.pack(fill='both',expand=True,padx=14,pady=(0,10)); body.grid_columnconfigure(0,weight=6); body.grid_columnconfigure(1,weight=5); body.grid_rowconfigure(0,weight=1)
        left=tk.Frame(body,bg=PANEL); left.grid(row=0,column=0,sticky='nsew',padx=(0,6)); right=tk.Frame(body,bg=PANEL); right.grid(row=0,column=1,sticky='nsew',padx=(6,0))
        tk.Label(left,text='CAPTURA DE PROMAD',bg=PANEL,fg=TEXT,font=('Segoe UI',10,'bold')).pack(anchor='w',padx=12,pady=(12,6))
        self.preview=tk.Label(left,text='Sin captura',bg='#07101f',fg=MUTED); self.preview.pack(fill='both',expand=True,padx=12,pady=(0,8))
        self.conf=tk.Label(left,text='',bg=PANEL,fg=MUTED); self.conf.pack(anchor='w',padx=12)
        self.raw=tk.Text(left,height=7,bg='#0d172c',fg=TEXT,insertbackground=TEXT,relief='flat',font=('Consolas',8),wrap='word'); self.raw.pack(fill='x',padx=12,pady=(6,12))
        tk.Label(right,text='DATOS DETECTADOS / EDITABLES',bg=PANEL,fg=TEXT,font=('Segoe UI',10,'bold')).pack(anchor='w',padx=12,pady=(12,6))
        form=tk.Frame(right,bg=PANEL); form.pack(fill='x',padx=12)
        fields=[('folio','Folio PROMAD'),('municipio','Municipio'),('tipificacion','Tipificación'),('hora','Hora'),('direccion','Dirección'),('colonia','Colonia'),('corporacion','Corporación'),('unidad','Unidad')]
        for i,(k,l) in enumerate(fields):
            tk.Label(form,text=l,bg=PANEL,fg=MUTED,font=('Segoe UI',8,'bold')).grid(row=i,column=0,sticky='w',pady=3)
            e=tk.Entry(form,textvariable=self.vars[k],bg=CARD,fg=TEXT,insertbackground=TEXT,relief='flat',font=('Segoe UI',9)); e.grid(row=i,column=1,sticky='ew',padx=(10,0),pady=3,ipady=5)
        form.grid_columnconfigure(1,weight=1)
        tk.Label(right,text='Narrativa / hechos',bg=PANEL,fg=MUTED,font=('Segoe UI',8,'bold')).pack(anchor='w',padx=12,pady=(8,3))
        self.narr=tk.Text(right,height=5,bg=CARD,fg=TEXT,insertbackground=TEXT,relief='flat',wrap='word',font=('Segoe UI',9)); self.narr.pack(fill='x',padx=12)
        tk.Label(right,text='NOTA INFORMATIVA',bg=PANEL,fg=TEXT,font=('Segoe UI',10,'bold')).pack(anchor='w',padx=12,pady=(10,4))
        self.nt=tk.Text(right,bg='#0d172c',fg=TEXT,insertbackground=TEXT,relief='flat',wrap='word',font=('Segoe UI',9)); self.nt.pack(fill='both',expand=True,padx=12)
        acts=tk.Frame(right,bg=PANEL); acts.pack(fill='x',padx=12,pady=12)
        self.btn(acts,'GENERAR NOTA',self.gen).pack(side='left',padx=3); self.btn(acts,'COPIAR NOTA',self.copy,'#2f82ff').pack(side='left',padx=3); self.btn(acts,'GUARDAR FOLIO',self.save,GREEN).pack(side='left',padx=3)
    def refresh_ocr(self):
        ex=tesseract_exe();
        if ex: pytesseract.pytesseract.tesseract_cmd=ex; self.ocr.config(text='● OCR INTEGRADO',fg=GREEN)
        else:self.ocr.config(text='● OCR NO DISPONIBLE',fg=RED)
    def poll(self):
        try:
            while True:
                ev=self.q.get_nowait(); self.cap_card() if ev=='card' else self.cap_window()
        except queue.Empty: pass
        self.after(120,self.poll)
    def cursor(self):
        pt=ctypes.wintypes.POINT(); ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)); return pt.x,pt.y
    def fgrect(self):
        u=ctypes.windll.user32; h=u.GetForegroundWindow(); r=ctypes.wintypes.RECT();
        return (r.left,r.top,r.right,r.bottom) if h and u.GetWindowRect(h,ctypes.byref(r)) else None
    def cap_card(self):
        if os.name!='nt':return
        x,y=self.cursor(); self.capture((x-180,y-90,x+180,y+90),'Tarjeta')
    def cap_window(self):
        if os.name!='nt':return
        r=self.fgrect();
        if r:self.capture(r,'Ventana')
    def capture(self,bbox,name):
        try: im=ImageGrab.grab(bbox=bbox,all_screens=True)
        except TypeError: im=ImageGrab.grab(bbox=bbox)
        self.img=im.convert('RGB'); p=CAP/f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"; self.img.save(p); self.capture_path=str(p); self.show(); self.status.config(text=f'{name} capturada • leyendo...',fg=YELLOW); self.after(80,self.run_ocr); self.deiconify(); self.lift()
    def open_img(self):
        p=filedialog.askopenfilename(filetypes=[('Imagen','*.png *.jpg *.jpeg *.bmp')]);
        if p:self.img=Image.open(p).convert('RGB'); self.capture_path=p; self.show(); self.run_ocr()
    def show(self):
        im=self.img.copy(); im.thumbnail((700,390),Image.Resampling.LANCZOS); self.imgtk=ImageTk.PhotoImage(im); self.preview.config(image=self.imgtk,text='')
    def run_ocr(self):
        if self.img is None:return
        ex=tesseract_exe()
        if not ex: messagebox.showerror('OCR','Esta copia no contiene el OCR integrado.'); return
        pytesseract.pytesseract.tesseract_cmd=ex
        def work():
            try:
                im=ImageOps.grayscale(self.img); im=im.resize((im.width*2,im.height*2),Image.Resampling.LANCZOS); im=ImageOps.autocontrast(im); im=ImageEnhance.Contrast(im).enhance(1.5); im=im.filter(ImageFilter.SHARPEN)
                try: txt=pytesseract.image_to_string(im,lang='spa+eng',config='--psm 6'); dat=pytesseract.image_to_data(im,lang='spa+eng',config='--psm 6',output_type=pytesseract.Output.DICT)
                except: txt=pytesseract.image_to_string(im,lang='eng',config='--psm 6'); dat=pytesseract.image_to_data(im,lang='eng',config='--psm 6',output_type=pytesseract.Output.DICT)
                cs=[]
                for c in dat.get('conf',[]):
                    try:
                        f=float(c)
                        if f>=0:cs.append(f)
                    except:pass
                avg=sum(cs)/len(cs) if cs else 0; d=parse(txt); self.after(0,lambda:self.apply(d,txt,avg))
            except Exception as e:self.after(0,lambda:messagebox.showerror('OCR',str(e)))
        threading.Thread(target=work,daemon=True).start()
    def apply(self,d,txt,avg):
        self.raw.delete('1.0','end'); self.raw.insert('1.0',txt)
        for k in self.vars:
            if d.get(k):self.vars[k].set(d[k])
        if d.get('narrativa'):self.narr.delete('1.0','end');self.narr.insert('1.0',d['narrativa'])
        self.vars['capturado_en'].set(now()); self.conf.config(text=f'OCR {avg:.0f}%'+(' • REVISAR' if avg<65 else ''),fg=GREEN if avg>=65 else YELLOW); self.status.config(text=f"Detectado: {d.get('folio') or 'sin folio'} • {d.get('tipificacion') or 'sin tipificación'}",fg=GREEN); self.gen()
    def collect(self):
        d={k:v.get().strip() for k,v in self.vars.items()}; d['narrativa']=self.narr.get('1.0','end').strip(); d['ocr']=self.raw.get('1.0','end').strip(); d['captura']=self.capture_path; d['capturado_en']=d.get('capturado_en') or now(); return d
    def gen(self):
        t=note(self.collect()); self.nt.delete('1.0','end'); self.nt.insert('1.0',t); return t
    def copy(self):
        t=self.gen(); self.clipboard_clear(); self.clipboard_append(t); self.update(); self.status.config(text='Nota copiada.',fg=GREEN)
    def save(self):
        d=self.collect(); key=save_db(d); self.status.config(text=f'Guardado: {key}',fg=GREEN)

if __name__=='__main__': App().mainloop()
