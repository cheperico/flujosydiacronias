#!/usr/bin/env python3
"""
gui_fluir.py — Interfaz gráfica Python para selección de medios
y envío via OSC a TouchDesigner (puerto 9002).

Reemplaza la UI de chips de TouchDesigner y el listener del "Fluir":
la GUI muestra la lista completa de items seleccionables (horas, municipios,
colores, tags), el usuario marca los que quiere y al enviar se genera el loop
con loop_db y se emiten por 9002 los mensajes idénticos a
`puente_td.py modo fluir` (delegando en `_procesar_rafaga`).

TD solo necesita escuchar el puerto 9002 y poblar sus tablas de medios.

Uso:
    python gui_fluir.py
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
import sqlite3
import logging
import os
import sys
import json
import threading
import unicodedata
from collections import defaultdict

from pythonosc import udp_client

# ---------------------------------------------------------------------------
# Configuración del proyecto (rutas) — gui_fluir.py vive en la RAÍZ del proyecto
# ---------------------------------------------------------------------------
RAIZ_PROYECTO = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(RAIZ_PROYECTO, "scripts")
SCRIPTS_TD_DIR = os.path.join(SCRIPTS_DIR, "td")
SCRIPTS_AI_MEDIA_DIR = os.path.join(SCRIPTS_DIR, "ai_media")

for _dir in (SCRIPTS_TD_DIR, SCRIPTS_AI_MEDIA_DIR, RAIZ_PROYECTO):
    if _dir not in sys.path:
        sys.path.insert(0, _dir)

from db.util import resolver_db
from puente_td import _procesar_rafaga  # noqa: E402

# Configuración OSC
OSC_HOST = "127.0.0.1"
OSC_PUERTO_9002 = 9002  # Python → TD (resultado loop)

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ordenación alfabética (case-insensitive, sin acentos) — igual que elecciones.py
# ---------------------------------------------------------------------------

def _clave_alfabetica(valor: str) -> str:
    """Normaliza un valor para ordenación alfabética: sin acentos, minúsculas."""
    nfkd = unicodedata.normalize("NFD", valor or "")
    return "".join(c for c in nfkd if unicodedata.combining(c) == 0).lower().strip()


def _ordenar_alfabetico(items: list[str]) -> list[str]:
    """Ordena una lista de strings alfabéticamente (normalizada)."""
    return sorted(items, key=_clave_alfabetica)


# ---------------------------------------------------------------------------
# Helper: obtener datos desde la BD para poblar la GUI
# ---------------------------------------------------------------------------

def obtener_datos_desde_db(db_path: str):
    """
    Consulta la BD y devuelve los datos para poblar la GUI.

    - horas: 24 fijas, orden numérico.
    - municipios: pares (nombre, frecuencia), orden alfabético.
    - colores: pares (nombre, frecuencia), orden alfabético.
    - tags: las 200 con MÁS apariciones, ordenadas alfabéticamente.
    """
    conn = sqlite3.connect(db_path)

    # --- Horas (24 fijas, orden numérico) ---
    horas = [f"{h:02d}:00" for h in range(24)]

    # --- Municipios (con frecuencia) ---
    filas = conn.execute("""
        SELECT municipio, COUNT(*) FROM media
        WHERE municipio IS NOT NULL AND municipio != ''
        GROUP BY municipio
    """).fetchall()
    municipios = [(str(fila[0]), int(fila[1])) for fila in filas]
    municipios = sorted(municipios, key=lambda vf: _clave_alfabetica(vf[0]))

    # --- Colores básicos (con frecuencia) ---
    filas = conn.execute("""
        SELECT color_1_name_basic, COUNT(*) FROM media
        WHERE color_1_name_basic IS NOT NULL
        GROUP BY color_1_name_basic
    """).fetchall()
    colores = [(str(fila[0]), int(fila[1])) for fila in filas]
    colores = sorted(colores, key=lambda vf: _clave_alfabetica(vf[0]))

    # --- Tags (ia_keywords): TOP 200 por frecuencia, luego alfabético ---
    filas = conn.execute("""
        SELECT value FROM media_metadata WHERE key='ia_keywords'
        AND value IS NOT NULL AND value != ''
    """).fetchall()

    contador: dict[str, int] = {}
    KEYWORDS_A_IGNORAR = [
        'elige una', 'genero', 'fotografico', 'es un(a)', 'la imagen',
        'una de las siguientes', 'deben describir', 'ejemplo:', 'separas con comas',
        'el aguacate', "esponja ribiosa", "sa_20001", "roberto", "federico",
        "el aguaje", "elante", "ella", "documento", "objetivo", "objeto",
        "otras)", "otras.", "gushing river",
    ]
    KEYWORDS_SENSIBLES = {"cadáver", "perro muerto", "cuerpo muerto", "muerto"}

    for fila in filas:
        texto = str(fila[0]).strip()
        if not texto:
            continue
        texto_limpio = texto.strip()
        if texto_limpio.startswith("["):
            try:
                datos = json.loads(texto_limpio)
                if isinstance(datos, list):
                    partes = [str(p).strip().strip("'\"") for p in datos]
                else:
                    partes = [p.strip() for p in texto_limpio.split(",") if p.strip()]
            except (json.JSONDecodeError, TypeError):
                partes = [p.strip() for p in texto_limpio.split(",") if p.strip()]
        else:
            partes = [p.strip() for p in texto_limpio.split(",") if p.strip()]

        for p in partes:
            p = p.strip().lower()
            if len(p) <= 2:
                continue
            if any(ign in p for ign in KEYWORDS_A_IGNORAR):
                continue
            if p in KEYWORDS_SENSIBLES:
                continue
            contador[p] = contador.get(p, 0) + 1

    # Top 200 por frecuencia (descendente), luego alfabético
    top_frecuencia = sorted(contador.items(), key=lambda x: -x[1])[:200]
    tags = [(str(v), int(f)) for v, f in top_frecuencia]
    tags = sorted(tags, key=lambda vf: _clave_alfabetica(vf[0]))

    conn.close()
    return {
        "horas": horas,
        "municipios": municipios,
        "colores": colores,
        "tags": tags,
    }


# ---------------------------------------------------------------------------
# Cliente: orquesta la consulta/loop y el envío por 9002
# ---------------------------------------------------------------------------

class FluirClient:
    """Cliente OSC de salida (9002) y orquestación del envío de medios."""

    def __init__(self, puerto_9002=OSC_PUERTO_9002, host=OSC_HOST):
        self.host = host
        self.puerto_9002 = puerto_9002
        self.cliente_9002 = udp_client.SimpleUDPClient(host, puerto_9002)

    def enviar_9002(self, address, *args):
        """Envía un mensaje OSC por puerto 9002."""
        self.cliente_9002.send_message(address, list(args))

    def procesar_y_enviar(self, selecciones, db_path, loop_secs=300.0,
                          enviar_medios=True):
        """
        Genera el loop con loop_db y lo envía por 9002, delegando en
        `puente_td._procesar_rafaga` (mismo contrato que `puente_td.py modo
        fluir`). Devuelve la ruta del spec escrita o None si falló.
        """
        spec_salida = os.path.join(RAIZ_PROYECTO, "td", "spec_fluir.json")
        ruta_spec = _procesar_rafaga(
            db_path=db_path,
            selecciones=selecciones,
            loop_secs=loop_secs,
            spec_salida=spec_salida,
            host=self.host,
            enviar_medios=enviar_medios,
        )
        return ruta_spec


# ---------------------------------------------------------------------------
# GUI principal (Tkinter)
# ---------------------------------------------------------------------------

class GUIFluirApp:
    """Interfaz gráfica principal para selección y envío de medios."""

    # Grupos de la GUI → clave OSC/filtro
    GRUPOS = ("horas", "municipios", "colores", "tags")

    def __init__(self, root):
        self.root = root
        self.root.title("🎛️ Panel Flujos - Selección OSC → TD (9002)")
        self.root.geometry("900x680")
        self.root.minsize(720, 520)

        self.db_path = resolver_db()
        self.datos_db = obtener_datos_desde_db(self.db_path)

        self.client = FluirClient()

        # items_por_grupo: {grupo: [(valor_puro, texto_mostrar)]}
        # vars_por_grupo:  {grupo: {valor_puro: BooleanVar}}
        # widgets_por_grupo: {grupo: {valor_puro: Checkbutton}}
        # frames_interior: {grupo: ttk.Frame} (frame scrollable con el grid)
        self.items_por_grupo: dict[str, list[tuple[str, str]]] = {}
        self.vars_por_grupo: dict[str, dict[str, tk.BooleanVar]] = {}
        self.widgets_por_grupo: dict[str, dict[str, ttk.Checkbutton]] = {}
        self.frames_interior: dict[str, ttk.Frame] = {}
        self.labels_contador: dict[str, ttk.Label] = {}
        self.entries_filtro: dict[str, ttk.Entry] = {}
        self.canvas_por_grupo: dict[str, tk.Canvas] = {}

        self._construir_interfaz()

    # -----------------------------------------------------------------
    # Construcción de la interfaz
    # -----------------------------------------------------------------
    def _construir_interfaz(self):
        main_frame = ttk.Frame(self.root, padding=8)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- Barra superior: título + total seleccionado ---
        barra_titulo = ttk.Frame(main_frame)
        barra_titulo.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(barra_titulo,
                  text="Panel Flujos - Selección OSC → TD (9002)",
                  font=("TkDefaultFont", 11, "bold")).pack(side=tk.LEFT)

        self.label_total = ttk.Label(barra_titulo, text="0 seleccionados",
                                     font=("TkDefaultFont", 10, "bold"),
                                     foreground="#1a6a3c")
        self.label_total.pack(side=tk.RIGHT)

        # --- Barra de parámetros compacta ---
        barra_params = ttk.LabelFrame(main_frame, text="Parámetros", padding=4)
        barra_params.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(barra_params, text="Loop secs:").pack(side=tk.LEFT, padx=(4, 2))
        self.entry_loop_secs = ttk.Entry(barra_params, width=8)
        self.entry_loop_secs.insert(0, "300.0")
        self.entry_loop_secs.pack(side=tk.LEFT, padx=(0, 12))

        self.var_enviar_medios = tk.BooleanVar(value=True)
        ttk.Checkbutton(barra_params, text="Enviar medios (tabla/medio/chiche)",
                         variable=self.var_enviar_medios).pack(side=tk.LEFT, padx=(0, 12))

        self.btn_enviar = ttk.Button(barra_params, text="Consultar y enviar por 9002",
                                     command=self._on_enviar)
        self.btn_enviar.pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(barra_params, text="Limpiar selección",
                    command=self._on_limpiar).pack(side=tk.LEFT, padx=6)

        # --- Notebook con pestañas ---
        self.notebook = ttk.Notebook(main_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        self._construir_pestana("horas", "🕐 Horas (24 h)", con_filtro=False)
        self._construir_pestana("municipios", "🏘️ Municipios", con_filtro=True)
        self._construir_pestana("colores", "🎨 Colores", con_filtro=False)
        self._construir_pestana("tags", "🏷️ Tags (200 más usadas)", con_filtro=True)

        # --- Log ---
        label_log = ttk.Label(main_frame, text="Log:",
                               font=("TkDefaultFont", 9, "bold"))
        label_log.pack(anchor=tk.W, pady=(0, 2))

        self.text_log = scrolledtext.ScrolledText(main_frame, height=5, width=80,
                                                   state=tk.DISABLED,
                                                   font=("Consolas", 9))
        self.text_log.pack(fill=tk.X)

    def _construir_pestana(self, grupo, titulo, con_filtro):
        """Construye una pestaña con header + canvas scrollable + grid de checkboxes."""
        pestana = ttk.Frame(self.notebook, padding=4)
        self.notebook.add(pestana, text=titulo)

        # --- Header de la pestaña ---
        header = ttk.Frame(pestana)
        header.pack(fill=tk.X, pady=(0, 4))

        self.labels_contador[grupo] = ttk.Label(header, text="")
        self.labels_contador[grupo].pack(side=tk.LEFT)

        ttk.Button(header, text="Todo", width=5,
                   command=lambda g=grupo: self._seleccionar_grupo(g, True)
                   ).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(header, text="Nada", width=5,
                   command=lambda g=grupo: self._seleccionar_grupo(g, False)
                   ).pack(side=tk.RIGHT)

        if con_filtro:
            entry = ttk.Entry(header)
            entry.pack(side=tk.RIGHT, padx=(4, 8))
            entry.insert(0, "🔍")
            entry.bind("<KeyRelease>", lambda e, g=grupo: self._repoblar_pestana(g))
            entry.bind("<FocusIn>", lambda e: (e.widget.delete(0, tk.END)
                                                if e.widget.get() == "🔍" else None))
            self.entries_filtro[grupo] = entry

        # --- Canvas scrollable ---
        canvas = tk.Canvas(pestana, highlightthickness=0, bg="#f2f2f2")
        scroll = ttk.Scrollbar(pestana, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)

        interior = ttk.Frame(canvas)
        win_id = canvas.create_window((0, 0), window=interior, anchor="nw")

        interior.bind("<Configure>",
                      lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win_id, width=e.width))

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Rueda del mouse sobre canvas/interior
        canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
        interior.bind("<MouseWheel>",
                      lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

        self.canvas_por_grupo[grupo] = canvas
        self.frames_interior[grupo] = interior

        # --- Items ---
        if grupo == "horas":
            items = [(h, h) for h in self.datos_db["horas"]]
        else:
            items = [(str(v), f"{_truncar(v)} ({f})")
                     for v, f in self.datos_db[grupo]]
        self.items_por_grupo[grupo] = items
        self.vars_por_grupo[grupo] = {
            valor: tk.BooleanVar(value=False) for valor, _ in items
        }
        for var in self.vars_por_grupo[grupo].values():
            var.trace_add("write", lambda *_, g=grupo: self._actualizar_contadores())

        self._repoblar_pestana(grupo)
        self._actualizar_contadores()

    # -----------------------------------------------------------------
    # Poblado / filtrado del grid
    # -----------------------------------------------------------------
    def _repoblar_pestana(self, grupo):
        """Re-crea los checkbuttons del grupo aplicando el filtro de texto."""
        interior = self.frames_interior[grupo]
        for child in interior.winfo_children():
            child.destroy()

        # Determinar items visibles según filtro
        filtro = ""
        if grupo in self.entries_filtro:
            raw = self.entries_filtro[grupo].get()
            if raw and raw != "🔍":
                filtro = raw.strip().lower()

        if filtro:
            visibles = [(v, t) for v, t in self.items_por_grupo[grupo]
                        if filtro in _clave_alfabetica(v)]
        else:
            visibles = list(self.items_por_grupo[grupo])

        n_items = len(visibles)
        n_cols = min(self._columnas_para(grupo, n_items), n_items)
        n_cols = max(1, n_cols)
        n_rows = (n_items + n_cols - 1) // n_cols

        self.widgets_por_grupo[grupo] = {}
        for i, (valor, texto) in enumerate(visibles):
            var = self.vars_por_grupo[grupo][valor]
            cb = ttk.Checkbutton(interior, text=texto, variable=var)
            cb.grid(row=i // n_cols, column=i % n_cols, sticky=tk.W, padx=4, pady=2)
            self.widgets_por_grupo[grupo][valor] = cb

        # Centrar contenido si sobra espacio horizontal
        for col in range(n_cols):
            interior.grid_columnconfigure(col, weight=0, uniform="cols")
        if n_cols > 0:
            interior.grid_columnconfigure(n_cols, weight=1)

    @staticmethod
    def _columnas_para(grupo, n_items):
        """Cantidad de columnas según el grupo y el número de items visibles."""
        if grupo == "horas":
            return 12
        if grupo == "colores":
            return min(6, max(3, int(n_items ** 0.5)))
        if grupo == "municipios":
            return min(10, max(3, int(n_items ** 0.5)))
        # tags
        return min(10, max(4, (n_items + 19) // 20))

    # -----------------------------------------------------------------
    # Contadores y selección masiva
    # -----------------------------------------------------------------
    def _actualizar_contadores(self):
        """Actualiza los labels de contador por grupo y el total global."""
        total_global = 0
        for grupo in self.GRUPOS:
            vars_grupo = self.vars_por_grupo.get(grupo, {})
            n_marcados = sum(1 for v in vars_grupo.values() if v.get())
            n_total = len(vars_grupo)
            if grupo in self.labels_contador:
                self.labels_contador[grupo].config(
                    text=f"{n_marcados}/{n_total} seleccionados")
            total_global += n_marcados
        self.label_total.config(text=f"{total_global} seleccionados")

    def _seleccionar_grupo(self, grupo, estado):
        """Marca o desmarca todos los items de un grupo."""
        for var in self.vars_por_grupo[grupo].values():
            var.set(estado)
        self._actualizar_contadores()

    # -----------------------------------------------------------------
    # Obtención de selección y envío
    # -----------------------------------------------------------------
    def _obtener_seleccionados(self):
        """Obtiene los valores puros seleccionados de todos los grupos."""
        seleccionados = defaultdict(list)
        for grupo in self.GRUPOS:
            for valor, var in self.vars_por_grupo[grupo].items():
                if var.get():
                    seleccionados[grupo].append(valor)
        return dict(seleccionados)

    def _on_enviar(self):
        """Evento: obtiene selecciones, genera el loop y envía por 9002 (en hilo)."""
        seleccionados = self._obtener_seleccionados()

        if not any(seleccionados.values()):
            self._log("⚠️ No hay selección alguna. Marque al menos un item.")
            return

        try:
            loop_secs = float(self.entry_loop_secs.get())
            if not (0 < loop_secs <= 86400):
                raise ValueError
        except ValueError:
            self._log(f"⚠️ Loop secs inválido ('{self.entry_loop_secs.get()}'); "
                      "se usa 300.0.")
            loop_secs = 300.0
            self.entry_loop_secs.delete(0, tk.END)
            self.entry_loop_secs.insert(0, "300.0")

        resumen = {g: len(v) for g, v in seleccionados.items()}
        self._log(f"🔍 Selección: {resumen}")
        self._log("⏳ Generando loop y enviando por 9002...")

        self.btn_enviar.config(state=tk.DISABLED)
        hilo = threading.Thread(
            target=self._trabajo_envio,
            args=(seleccionados, loop_secs, self.var_enviar_medios.get()),
            daemon=True,
        )
        hilo.start()

    def _trabajo_envio(self, seleccionados, loop_secs, enviar_medios):
        """Corre en un hilo: genera el loop, envía por 9002 y reporta el resumen."""
        try:
            ruta_spec = self.client.procesar_y_enviar(
                selecciones=seleccionados,
                db_path=self.db_path,
                loop_secs=loop_secs,
                enviar_medios=enviar_medios,
            )
            self.root.after(0, self._fin_envio, ruta_spec)
        except Exception as e:  # noqa: BLE001
            import traceback
            log.error(traceback.format_exc())
            self.root.after(0, self._log, f"❌ Error durante el envío: {e}")
            self.root.after(0, lambda: self.btn_enviar.config(state=tk.NORMAL))

    def _fin_envio(self, ruta_spec):
        """Reporta el resultado del envío y rehabilita el botón."""
        self.btn_enviar.config(state=tk.NORMAL)

        if not ruta_spec:
            self._log("❌ No se pudo generar el loop (revisa los filtros).")
            return

        self._log(f"✅ Spec escrito: {ruta_spec}")

        try:
            with open(ruta_spec, "r", encoding="utf-8") as f:
                spec = json.load(f)
            resumen = spec.get("resumen") or {}
            por_tipo = spec.get("por_tipo") or {}
            videos = por_tipo.get("video", []) or []
            videos_360 = [m for m in videos if m.get("es_360")]
            videos_norm = [m for m in videos if not m.get("es_360")]
            n_chiches = len(spec.get("chiches", []) or [])

            detalle = (
                f"✅ Enviado por 9002: {resumen.get('total', 0)} medios · "
                f"{resumen.get('image', 0)} img · {len(videos_norm)} vid · "
                f"{len(videos_360)} video360 · {resumen.get('audio', 0)} aud · "
                f"{resumen.get('text', 0)} txt · {n_chiches} chiches"
            )
            self._log(detalle)
        except Exception as e:  # noqa: BLE001
            self._log(f"✅ Enviado por 9002 (no se pudo leer spec: {e})")

        self._log("→ Revisa TouchDesigner (puerto 9002).")

    # -----------------------------------------------------------------
    # Utilidades
    # -----------------------------------------------------------------
    def _on_limpiar(self):
        """Limpia todas las selecciones."""
        for grupo in self.GRUPOS:
            for var in self.vars_por_grupo[grupo].values():
                var.set(False)
        self._actualizar_contadores()
        self._log("♻️ Selecciones limpiadas.")

    def _log(self, mensaje: str):
        self.text_log.config(state=tk.NORMAL)
        self.text_log.insert(tk.END, mensaje + "\n")
        self.text_log.see(tk.END)
        self.text_log.config(state=tk.DISABLED)


def _truncar(texto: str, max_len: int = 26) -> str:
    """Recorta un texto largo con '…' para no desalinear columnas."""
    if len(texto) <= max_len:
        return texto
    return texto[: max_len - 1] + "…"


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

def main():
    root = tk.Tk()
    app = GUIFluirApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()