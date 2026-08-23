import os, re, sys, json, queue, ctypes, ctypes.wintypes, threading, shutil, time
from pathlib import Path
from datetime import datetime
import tkinter as tk
from tkinter import messagebox, filedialog
from PIL import Image, ImageTk, ImageGrab, ImageOps, ImageEnhance, ImageFilter
import pytesseract

APP = 'PROMAD Capturador en Vivo + Nota'
VER = '2.0.0'
BG='#08111f'; PANEL='#111d33'; CARD='#182741'; TEXT='#edf6ff'; MUTED='#8ea2bd'; CYAN='#00c8e8'; GREEN='#25c06d'; YELLOW='#f3c83b'; RED='#ff4a4a'; BLUE='#2f82ff'
ROOT=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
DATA=Path(os.getenv('LOCALAPPDATA',Path.home()))/'PROMAD_Capturador'
CAP=DATA/'capturas'; DB=DATA/'incidentes.json'; CFG=DATA/'config.json'
for d in (DATA,CAP): d.mkdir(parents=True,exist_ok=True)

if os.name == 'nt':
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try: ctypes.windll.user32.SetProcessDPIAware()
        except Exception: pass

def now(): return datetime.now().isoformat(timespec='seconds')
def clean(v): return re.sub(r'\s+',' ',v or '').strip()
def tesseract_exe():
    for p in [ROOT/'tesseract'/'tesseract.exe', Path(r'C:\Program Files\Tesseract-OCR\tesseract.exe')]:
        if p.exists(): return str(p)
    return shutil.which('tesseract') or ''

def grab_bbox(bbox):
    try: return ImageGrab.grab(bbox=bbox, all_screens=True).convert('RGB')
    except TypeError: return ImageGrab.grab(bbox=bbox).convert('RGB')

def virtual_screen():
    if os.name != 'nt': return (0,0,1920,1080)
    u=ctypes.windll.user32
    x=u.GetSystemMetrics(76); y=u.GetSystemMetrics(77); w=u.GetSystemMetrics(78); h=u.GetSystemMetrics(79)
    if w <= 0 or h <= 0:
        w=u.GetSystemMetrics(0); h=u.GetSystemMetrics(1); x=y=0
    return x,y,w,h

def parse(text):
    raw=text.replace('\\','/').replace('—','-').replace('–','-')
    lines=[clean(x) for x in raw.splitlines() if clean(x)]
    joined='\n'.join(lines)
    folio=''; municipio=''; tip=''; hora=''; fi=-1
    for i,line in enumerate(lines):
        u=line.upper().replace('O','0')
        m=re.search(r'(?:C\s*[S5]|CS|C5|G5|S5)\s*[/I1-]?\s*([0-9]{8})\s*[/I1-]?\s*([0-9]{4,7})',u)
        if m:
            folio=f"C5/{m.group(1)}/{m.group(2)}"; fi=i
            after=line[m.end():].strip(' -_:')
            municipio=re.split(r'\b(?:ACCIDENTE|ROBO|VIOLENCIA|INCENDIO|PERSONA|VEH[IÍ]CULO|DETONACI[ÓO]N|LESI[ÓO]N|HOMICIDIO|AMENAZA)\b',after.upper())[0].strip(' -')
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
    return {'folio':folio,'municipio':municipio,'tipificacion':tip,'hora':hora,'direccion':labeled('DIRECCI[ÓO]N','DOMICILIO','UBICACI[ÓO]N'),'colonia':labeled('COLONIA','BARRIO','FRACC(?:IONAMIENTO)?'),'corporacion':labeled('CORPORACI[ÓO]N','INSTITUCI[ÓO]N'),'unidad':labeled('UNIDAD','M[ÓO]VIL'),'narrativa':labeled('NARRATIVA','HECHOS','DESCRIPCI[ÓO]N'),'capturado_en':now(),'ocr':joined}

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
    except Exception: db={}
    key=d.get('folio') or 'SIN_FOLIO_'+datetime.now().strftime('%Y%m%d_%H%M%S')
    old=db.get(key,{})
    for k,v in d.items():
        if v: old[k]=v
    old['actualizado_en']=now(); db[key]=old
    DB.write_text(json.dumps(db,ensure_ascii=False,indent=2),encoding='utf-8')
    return key

class RegionSelector(tk.Toplevel):
    def __init__(self, master, callback, initial=None):
        super().__init__(master); self.callback=callback; self.start=None; self.rect=None; self.label=None
        vx,vy,vw,vh=virtual_screen(); self.vx=vx; self.vy=vy
        self.overrideredirect(True); self.attributes('-topmost',True)
        try: self.attributes('-alpha',0.35)
        except Exception: pass
        self.geometry(f'{vw}x{vh}{vx:+d}{vy:+d}'); self.configure(bg='black')
        self.canvas=tk.Canvas(self,bg='black',highlightthickness=0,cursor='crosshair'); self.canvas.pack(fill='both',expand=True)
        self.canvas.create_text(24,24,anchor='nw',fill='white',font=('Segoe UI',16,'bold'),text='ARRASTRA PARA DELIMITAR EL ÁREA DE PROMAD   •   ESC = CANCELAR')
        self.canvas.bind('<ButtonPress-1>',self.down); self.canvas.bind('<B1-Motion>',self.move); self.canvas.bind('<ButtonRelease-1>',self.up); self.bind('<Escape>',lambda e:self.cancel()); self.focus_force()
        if initial:
            x1,y1,x2,y2=initial; self.start=(x1-vx,y1-vy); self.draw(x2-vx,y2-vy)
    def down(self,e): self.start=(e.x,e.y); self.draw(e.x,e.y)
    def draw(self,x,y):
        if not self.start:return
        x0,y0=self.start
        if self.rect:self.canvas.delete(self.rect)
        if self.label:self.canvas.delete(self.label)
        self.rect=self.canvas.create_rectangle(x0,y0,x,y,outline=CYAN,width=4)
        self.label=self.canvas.create_text(min(x0,x)+8,min(y0,y)+8,anchor='nw',fill='white',font=('Segoe UI',12,'bold'),text=f'{abs(x-x0)} × {abs(y-y0)} px')
    def move(self,e): self.draw(e.x,e.y)
    def up(self,e):
        if not self.start:return
        x0,y0=self.start; x1,y1=e.x,e.y
        if abs(x1-x0)<20 or abs(y1-y0)<20:return
        box=(min(x0,x1)+self.vx,min(y0,y1)+self.vy,max(x0,x1)+self.vx,max(y0,y1)+self.vy)
        self.destroy(); self.callback(box)
    def cancel(self): self.destroy(); self.callback(None)

class Hotkeys(threading.Thread):
    def __init__(self,q): super().__init__(daemon=True); self.q=q
    def run(self):
        if os.name!='nt': return
        u=ctypes.windll.user32; MOD_NOREPEAT=0x4000; WM_HOTKEY=0x0312
        keys=[(1,0x76,'select'),(2,0x77,'read'),(3,0x78,'live')]
        for i,vk,_ in keys: u.RegisterHotKey(None,i,MOD_NOREPEAT,vk)
        msg=ctypes.wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg),None,0,0)!=0:
            if msg.message==WM_HOTKEY:
                for i,_,name in keys:
                    if msg.wParam==i:self.q.put(name); break

class App(tk.Tk):
    def __init__(self):
        super().__init__(); self.title(f'{APP} {VER}'); self.geometry('1380x860'); self.minsize(1120,700); self.configure(bg=BG)
        self.frame=None; self.imgtk=None; self.capture_path=''; self.region=None; self.live=False; self.ocr_busy=False; self.last_auto=0; self.q=queue.Queue(); self.auto_ocr=tk.BooleanVar(value=False)
        self.vars={k:tk.StringVar() for k in ['folio','municipio','tipificacion','hora','direccion','colonia','corporacion','unidad','capturado_en']}
        self.load_cfg(); self.build(); self.after(100,self.poll); self.after(120,self.live_tick); Hotkeys(self.q).start(); self.refresh_ocr()
        if self.region:self.update_region_label()
    def btn(self,p,t,cmd,c=CYAN): return tk.Button(p,text=t,command=cmd,bg=c,fg='white',activebackground=c,activeforeground='white',relief='flat',bd=0,padx=12,pady=8,font=('Segoe UI',9,'bold'),cursor='hand2')
    def load_cfg(self):
        try:
            c=json.loads(CFG.read_text(encoding='utf-8')); r=c.get('region')
            if isinstance(r,list) and len(r)==4:self.region=tuple(int(x) for x in r)
        except Exception: pass
    def save_cfg(self):
        try: CFG.write_text(json.dumps({'region':list(self.region) if self.region else None},indent=2),encoding='utf-8')
        except Exception: pass
    def build(self):
        top=tk.Frame(self,bg=PANEL,height=58); top.pack(fill='x'); top.pack_propagate(False)
        tk.Label(top,text='PROMAD  •  CAPTURA EN VIVO + NOTA INFORMATIVA',bg=PANEL,fg=TEXT,font=('Segoe UI',14,'bold')).pack(side='left',padx=16,pady=16)
        self.ocr=tk.Label(top,text='OCR',bg=PANEL,fg=MUTED,font=('Segoe UI',9,'bold')); self.ocr.pack(side='right',padx=16)
        bar=tk.Frame(self,bg=BG); bar.pack(fill='x',padx=14,pady=10)
        self.btn(bar,'F7  SELECCIONAR ÁREA',self.select_region).pack(side='left',padx=4)
        self.live_btn=self.btn(bar,'F9  INICIAR EN VIVO',self.toggle_live,GREEN); self.live_btn.pack(side='left',padx=4)
        self.btn(bar,'F8  LEER PROMAD',self.read_region,BLUE).pack(side='left',padx=4)
        self.btn(bar,'ABRIR IMAGEN',self.open_img,'#394866').pack(side='left',padx=4)
        tk.Checkbutton(bar,text='OCR automático (cada 3 s)',variable=self.auto_ocr,bg=BG,fg=TEXT,selectcolor=CARD,activebackground=BG,activeforeground=TEXT,font=('Segoe UI',9),cursor='hand2').pack(side='left',padx=10)
        self.status=tk.Label(bar,text='1) F7 delimita PROMAD  •  2) F9 inicia la vista en vivo  •  3) F8 lee los datos',bg=BG,fg=MUTED,font=('Segoe UI',9)); self.status.pack(side='left',padx=10)
        self.region_lbl=tk.Label(self,bg=BG,fg=CYAN,font=('Segoe UI',9,'bold')); self.region_lbl.pack(fill='x',padx=18,pady=(0,6),anchor='w')
        body=tk.Frame(self,bg=BG); body.pack(fill='both',expand=True,padx=14,pady=(0,10)); body.grid_columnconfigure(0,weight=6); body.grid_columnconfigure(1,weight=5); body.grid_rowconfigure(0,weight=1)
        left=tk.Frame(body,bg=PANEL); left.grid(row=0,column=0,sticky='nsew',padx=(0,6)); right=tk.Frame(body,bg=PANEL); right.grid(row=0,column=1,sticky='nsew',padx=(6,0))
        head=tk.Frame(left,bg=PANEL); head.pack(fill='x',padx=12,pady=(12,6))
        tk.Label(head,text='VISTA EN VIVO DEL ÁREA DELIMITADA',bg=PANEL,fg=TEXT,font=('Segoe UI',10,'bold')).pack(side='left')
        self.live_dot=tk.Label(head,text='● DETENIDA',bg=PANEL,fg=MUTED,font=('Segoe UI',9,'bold')); self.live_dot.pack(side='right')
        self.preview=tk.Label(left,text='Presiona F7 para dibujar el área de PROMAD que quieres capturar.',bg='#050b15',fg=MUTED,font=('Segoe UI',11)); self.preview.pack(fill='both',expand=True,padx=12,pady=(0,8))
        self.conf=tk.Label(left,text='',bg=PANEL,fg=MUTED); self.conf.pack(anchor='w',padx=12)
        self.raw=tk.Text(left,height=7,bg='#0d172c',fg=TEXT,insertbackground=TEXT,relief='flat',font=('Consolas',8),wrap='word'); self.raw.pack(fill='x',padx=12,pady=(6,12))
        tk.Label(right,text='DATOS DETECTADOS / EDITABLES',bg=PANEL,fg=TEXT,font=('Segoe UI',10,'bold')).pack(anchor='w',padx=12,pady=(12,6))
        form=tk.Frame(right,bg=PANEL); form.pack(fill='x',padx=12)
        fields=[('folio','Folio PROMAD'),('municipio','Municipio'),('tipificacion','Tipificación'),('hora','Hora'),('direccion','Dirección'),('colonia','Colonia'),('corporacion','Corporación'),('unidad','Unidad')]
        for i,(k,l) in enumerate(fields):
            tk.Label(form,text=l,bg=PANEL,fg=MUTED,font=('Segoe UI',8,'bold')).grid(row=i,column=0,sticky='w',pady=3)
            tk.Entry(form,textvariable=self.vars[k],bg=CARD,fg=TEXT,insertbackground=TEXT,relief='flat',font=('Segoe UI',9)).grid(row=i,column=1,sticky='ew',padx=(10,0),pady=3,ipady=5)
        form.grid_columnconfigure(1,weight=1)
        tk.Label(right,text='Narrativa / hechos',bg=PANEL,fg=MUTED,font=('Segoe UI',8,'bold')).pack(anchor='w',padx=12,pady=(8,3))
        self.narr=tk.Text(right,height=5,bg=CARD,fg=TEXT,insertbackground=TEXT,relief='flat',wrap='word',font=('Segoe UI',9)); self.narr.pack(fill='x',padx=12)
        tk.Label(right,text='NOTA INFORMATIVA',bg=PANEL,fg=TEXT,font=('Segoe UI',10,'bold')).pack(anchor='w',padx=12,pady=(10,4))
        self.nt=tk.Text(right,bg='#0d172c',fg=TEXT,insertbackground=TEXT,relief='flat',wrap='word',font=('Segoe UI',9)); self.nt.pack(fill='both',expand=True,padx=12)
        acts=tk.Frame(right,bg=PANEL); acts.pack(fill='x',padx=12,pady=12)
        self.btn(acts,'GENERAR NOTA',self.gen).pack(side='left',padx=3); self.btn(acts,'COPIAR NOTA',self.copy,BLUE).pack(side='left',padx=3); self.btn(acts,'GUARDAR FOLIO',self.save,GREEN).pack(side='left',padx=3)
    def refresh_ocr(self):
        ex=tesseract_exe()
        if ex: pytesseract.pytesseract.tesseract_cmd=ex; self.ocr.config(text='● OCR INTEGRADO',fg=GREEN)
        else:self.ocr.config(text='● OCR NO DISPONIBLE',fg=RED)
    def poll(self):
        try:
            while True:
                ev=self.q.get_nowait()
                if ev=='select':self.select_region()
                elif ev=='read':self.read_region()
                elif ev=='live':self.toggle_live()
        except queue.Empty: pass
        self.after(100,self.poll)
    def select_region(self):
        was=self.live; self.live=False; self.withdraw(); self.update_idletasks()
        def done(box):
            self.deiconify(); self.lift()
            if box:
                self.region=box; self.save_cfg(); self.update_region_label(); self.status.config(text='Área guardada. Vista en vivo iniciada.',fg=GREEN); self.start_live()
            elif was:self.start_live()
        RegionSelector(self,done,self.region)
    def update_region_label(self):
        if not self.region:self.region_lbl.config(text='ÁREA: sin seleccionar'); return
        x1,y1,x2,y2=self.region; self.region_lbl.config(text=f'ÁREA PROMAD: X {x1}  Y {y1}  •  {x2-x1} × {y2-y1} px   |   F7 para volver a delimitar')
    def start_live(self):
        if not self.region:self.select_region(); return
        self.live=True; self.live_btn.config(text='F9  DETENER EN VIVO',bg=RED,activebackground=RED); self.live_dot.config(text='● EN VIVO',fg=GREEN); self.status.config(text='Captura en vivo activa. F8 lee el área actual.',fg=GREEN)
    def stop_live(self):
        self.live=False; self.live_btn.config(text='F9  INICIAR EN VIVO',bg=GREEN,activebackground=GREEN); self.live_dot.config(text='● DETENIDA',fg=MUTED)
    def toggle_live(self): self.stop_live() if self.live else self.start_live()
    def live_tick(self):
        if self.live and self.region:
            try:
                self.frame=grab_bbox(self.region); self.show_frame(self.frame)
                if self.auto_ocr.get() and time.monotonic()-self.last_auto>=3.0 and not self.ocr_busy:
                    self.last_auto=time.monotonic(); self.run_ocr(self.frame.copy(),save=False)
            except Exception as e:
                self.status.config(text=f'Error de captura: {e}',fg=RED); self.stop_live()
        self.after(120,self.live_tick)
    def show_frame(self,im):
        cp=im.copy(); cp.thumbnail((760,430),Image.Resampling.LANCZOS); self.imgtk=ImageTk.PhotoImage(cp); self.preview.config(image=self.imgtk,text='')
    def read_region(self):
        if not self.region:self.select_region(); return
        try:
            im=grab_bbox(self.region); self.frame=im; p=CAP/f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"; im.save(p); self.capture_path=str(p); self.show_frame(im); self.status.config(text='Captura tomada • leyendo PROMAD...',fg=YELLOW); self.run_ocr(im.copy(),save=True)
        except Exception as e: messagebox.showerror('Captura',str(e))
    def open_img(self):
        p=filedialog.askopenfilename(filetypes=[('Imagen','*.png *.jpg *.jpeg *.bmp')])
        if p:self.stop_live(); self.frame=Image.open(p).convert('RGB'); self.capture_path=p; self.show_frame(self.frame); self.run_ocr(self.frame.copy(),save=False)
    def run_ocr(self,img,save=False):
        if self.ocr_busy:return
        ex=tesseract_exe()
        if not ex: messagebox.showerror('OCR','Esta copia no contiene el OCR integrado.'); return
        self.ocr_busy=True; pytesseract.pytesseract.tesseract_cmd=ex
        def work():
            try:
                im=ImageOps.grayscale(img); im=im.resize((max(1,im.width*2),max(1,im.height*2)),Image.Resampling.LANCZOS); im=ImageOps.autocontrast(im); im=ImageEnhance.Contrast(im).enhance(1.5); im=im.filter(ImageFilter.SHARPEN)
                try: txt=pytesseract.image_to_string(im,lang='spa+eng',config='--psm 6'); dat=pytesseract.image_to_data(im,lang='spa+eng',config='--psm 6',output_type=pytesseract.Output.DICT)
                except Exception: txt=pytesseract.image_to_string(im,lang='eng',config='--psm 6'); dat=pytesseract.image_to_data(im,lang='eng',config='--psm 6',output_type=pytesseract.Output.DICT)
                cs=[]
                for c in dat.get('conf',[]):
                    try:
                        f=float(c)
                        if f>=0:cs.append(f)
                    except Exception:pass
                avg=sum(cs)/len(cs) if cs else 0; d=parse(txt); self.after(0,lambda:self.apply(d,txt,avg))
            except Exception as e:self.after(0,lambda:messagebox.showerror('OCR',str(e)))
            finally:self.ocr_busy=False
        threading.Thread(target=work,daemon=True).start()
    def apply(self,d,txt,avg):
        self.raw.delete('1.0','end'); self.raw.insert('1.0',txt)
        for k in self.vars:
            if d.get(k):self.vars[k].set(d[k])
        if d.get('narrativa'):self.narr.delete('1.0','end');self.narr.insert('1.0',d['narrativa'])
        self.vars['capturado_en'].set(now()); self.conf.config(text=f'OCR {avg:.0f}%'+(' • REVISAR' if avg<65 else ''),fg=GREEN if avg>=65 else YELLOW); self.status.config(text=f"Detectado: {d.get('folio') or 'sin folio'} • {d.get('tipificacion') or 'sin tipificación'}",fg=GREEN if d.get('folio') else YELLOW); self.gen()
    def collect(self):
        d={k:v.get().strip() for k,v in self.vars.items()}; d['narrativa']=self.narr.get('1.0','end').strip(); d['ocr']=self.raw.get('1.0','end').strip(); d['captura']=self.capture_path; d['region']=list(self.region) if self.region else None; d['capturado_en']=d.get('capturado_en') or now(); return d
    def gen(self):
        t=note(self.collect()); self.nt.delete('1.0','end'); self.nt.insert('1.0',t); return t
    def copy(self):
        t=self.gen(); self.clipboard_clear(); self.clipboard_append(t); self.update(); self.status.config(text='Nota copiada.',fg=GREEN)
    def save(self):
        d=self.collect(); key=save_db(d); self.status.config(text=f'Guardado: {key}',fg=GREEN)

if __name__=='__main__': App().mainloop()
