import streamlit as st
from groq import Groq
import base64
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import json
import re
import pandas as pd
from PIL import Image
import io

# ── Configuración de página ──────────────────────────────────────────────────
st.set_page_config(
    page_title="MiCompra",
    page_icon="🛒",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# ── CSS ──────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  .block-container { 
    padding-top: 0.5rem; 
    padding-bottom: 7rem; 
    max-width: 1200px;  /* Más ancho en desktop */
  }

  /* Responsive */
  @media (max-width: 768px) {
    .block-container { max-width: 480px; }
  }

  /* Mejorar tabla */
  .stTable, .dataframe {
    width: 100% !important;
  }
  .stTable td, .stTable th {
    padding: 12px 8px !important;
  }

  /* Tarjetas */
  .item-row {
    background: #1a1a1a; 
    border: 1px solid #2a2a2a;
    border-radius: 10px; 
    padding: 12px 14px; 
    margin-bottom: 8px;
  }
</style>
""", unsafe_allow_html=True)

# ── Categorías por defecto ───────────────────────────────────────────────────
DEFAULT_CATS = {
    "Lácteos":           ["Leche", "Yogur", "Queso", "Manteca", "Crema", "Dulce de leche"],
    "Carnes":            ["Vacuno", "Pollo", "Cerdo", "Fiambres y embutidos", "Pescado"],
    "Verduras y Frutas": ["Verduras", "Frutas", "Legumbres secas"],
    "Panificados":       ["Pan", "Galletitas", "Cereales", "Pastas secas", "Arroz y harinas"],
    "Bebidas":           ["Agua", "Gaseosas", "Jugos", "Infusiones", "Vinos y cervezas"],
    "Limpieza":          ["Jabones", "Detergentes", "Desinfectantes", "Papel y descartables"],
    "Higiene personal":  ["Shampoo", "Cremas", "Higiene dental", "Desodorantes"],
    "Congelados":        ["Helados", "Pizzas y empanadas", "Verduras congeladas"],
    "Almacén":           ["Aceite y vinagre", "Conservas", "Condimentos", "Azúcar y dulces"],
    "Otros":             ["General", "Mascotas", "Bebé"],
}

# ── Estado de sesión ─────────────────────────────────────────────────────────
if "ai_data"      not in st.session_state: st.session_state["ai_data"] = {}
if "last_photo"   not in st.session_state: st.session_state["last_photo"] = None
if "page"         not in st.session_state: st.session_state["page"] = "cargar"
if "editing_item" not in st.session_state: st.session_state["editing_item"] = None

# ── Helpers ──────────────────────────────────────────────────────────────────
def fmt_price(n):
    try:
        return f"${float(n):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "$0,00"

def total(items=None):
    try:
        if items is None:
            items = load_list_from_sheet()
        if not items: return 0.0
        return sum(float(i.get("price", 0)) * float(i.get("qty", 1)) for i in items)
    except Exception:
        return 0.0

@st.cache_resource
def get_gspread_client():
    """Crea el cliente de gspread una sola vez (cacheado)."""
    creds_data = json.loads(st.secrets["GOOGLE_CREDS"])
    scopes = ["https://www.googleapis.com/auth/spreadsheets",
              "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(creds_data, scopes=scopes)
    return gspread.authorize(creds)

def get_workbook():
    gc = get_gspread_client()
    return gc.open_by_key(st.secrets["SHEET_ID"])

def get_sheet():
    """Hoja principal de compras (historial)."""
    try:
        return get_workbook().sheet1
    except Exception as e:
        st.error(f"Error conectando con Google Sheets: {e}")
        return None

def get_list_sheet():
    """Hoja Lista - compra actual compartida."""
    try:
        wb = get_workbook()
        sheets = [s.title for s in wb.worksheets()]
        if "Lista" in sheets:
            return wb.worksheet("Lista")
        else:
            sh = wb.add_worksheet(title="Lista", rows=200, cols=11)
            sh.append_row(["id","Fecha","Hora","Categoria","Subcategoria",
                           "Descripcion","Cantidad","Unidad","Precio Unitario","Total","Tildado"])
            return sh
    except Exception as e:
        st.error(f"Error con hoja Lista: {e}")
        return None

@st.cache_data(ttl=10)
def load_list_from_sheet():
    """Lee productos de la hoja Lista. Cache 10s."""
    try:
        sh = get_list_sheet()
        if not sh:
            return []
        rows = sh.get_all_values()
        if len(rows) <= 1:
            return []
        items = []
        for i, row in enumerate(rows[1:], start=2):
            if len(row) >= 6 and row[0].strip():
                price = 0.0
                try: price = float(row[8]) if len(row) > 8 and row[8] else 0.0
                except: pass
                items.append({
                    "id": row[0].strip(),
                    "desc": row[5].strip() if len(row) > 5 else "",
                    "cat": row[3].strip() if len(row) > 3 else "",
                    "sub": row[4].strip() if len(row) > 4 else "",
                    "qty": float(row[6]) if len(row) > 6 and row[6] else 1,
                    "unit": row[7].strip() if len(row) > 7 else "unidad",
                    "price": price,
                    "total": round(price * (float(row[6]) if len(row) > 6 and row[6] else 1), 2),
                    "tildado": row[10].strip().upper() == "SI" if len(row) > 10 else False,
                    "actualizado": row[11].strip().upper() == "SI" if len(row) > 11 else False,
                    "row": i
                })
        return items
    except Exception as e:
        return []

def add_to_list_sheet(item):
    """Agrega un producto a la hoja Lista."""
    sh = get_list_sheet()
    if not sh: return False
    try:
        now = datetime.now()
        sh.append_row([
            item["id"],
            now.strftime("%d/%m/%Y"),
            now.strftime("%H:%M"),
            item["cat"], item["sub"], item["desc"],
            item["qty"], item["unit"], item["price"],
            round(float(item["price"]) * float(item["qty"]), 2),
            "NO",
            "SI" if float(item.get("price", 0)) > 0 else "NO"
        ])
        load_list_from_sheet.clear()
        return True
    except Exception as e:
        st.error(f"Error agregando a Lista: {e}")
        return False

def toggle_tildado(item_id, tildado):
    """Cambia el estado tildado de un item en la hoja Lista."""
    try:
        sh = get_list_sheet()
        if not sh: return False
        rows = sh.get_all_values()
        for i, row in enumerate(rows[1:], start=2):
            if row[0].strip() == str(item_id):
                sh.update_cell(i, 11, "SI" if tildado else "NO")
                load_list_from_sheet.clear()
                return True
        return False
    except Exception as e:
        st.error(f"Error actualizando estado: {e}")
        return False

def delete_from_list(item_id):
    """Elimina un item de la hoja Lista."""
    try:
        sh = get_list_sheet()
        if not sh: return False
        rows = sh.get_all_values()
        for i, row in enumerate(rows[1:], start=2):
            if row[0].strip() == str(item_id):
                sh.delete_rows(i)
                load_list_from_sheet.clear()
                return True
        return False
    except Exception as e:
        st.error(f"Error eliminando item: {e}")
        return False

def get_last_price(desc):
    """Busca el último precio conocido de un producto en el historial."""
    try:
        sh = get_sheet()
        if not sh: return None
        rows = sh.get_all_values()
        last_price = None
        for row in rows[1:]:
            if len(row) >= 9 and row[4].strip().lower() == desc.strip().lower():
                try:
                    last_price = float(row[7])
                except: pass
        return last_price
    except:
        return None

def update_price_in_list(item_id, new_price, new_qty, new_unit):
    """Actualiza precio, cantidad y unidad de un item en la hoja Lista."""
    try:
        sh = get_list_sheet()
        if not sh: return False
        rows = sh.get_all_values()
        for i, row in enumerate(rows[1:], start=2):
            if row[0].strip() == str(item_id):
                qty = float(new_qty)
                price = float(new_price)
                sh.update_cell(i, 7, qty)
                sh.update_cell(i, 8, new_unit)
                sh.update_cell(i, 9, price)
                sh.update_cell(i, 10, round(price * qty, 2))
                sh.update_cell(i, 12, "SI")  # Actualizado = SI
                load_list_from_sheet.clear()
                return True
        return False
    except Exception as e:
        st.error(f"Error actualizando precio: {e}")
        return False

def finish_shopping():
    """Pasa todos los items de Lista a Compras y limpia Lista."""
    try:
        list_sh = get_list_sheet()
        main_sh = get_sheet()
        if not list_sh or not main_sh: return False

        # Verificar encabezados en hoja principal
        if main_sh.row_count == 0 or not main_sh.row_values(1):
            main_sh.append_row(["Fecha","Hora","Categoría","Subcategoría",
                                 "Descripción","Cantidad","Unidad","Precio Unitario","Total"])

        # Copiar todos los items a hoja principal
        rows = list_sh.get_all_values()
        for row in rows[1:]:
            if len(row) >= 10 and row[0].strip():
                main_sh.append_row([
                    row[1], row[2], row[3], row[4],
                    row[5], row[6], row[7], row[8], row[9]
                ])

        # Limpiar hoja Lista (dejar solo encabezado)
        list_sh.clear()
        list_sh.append_row(["id","Fecha","Hora","Categoria","Subcategoria",
                            "Descripcion","Cantidad","Unidad","Precio Unitario","Total","Tildado","Actualizado"])
        load_list_from_sheet.clear()
        return True
    except Exception as e:
        st.error(f"Error finalizando compra: {e}")
        return False

def get_cat_sheet():
    """Hoja de categorias. La crea si no existe."""
    try:
        wb = get_workbook()
        sheets = [s.title for s in wb.worksheets()]
        if "Categorias" in sheets:
            return wb.worksheet("Categorias")
        else:
            sh = wb.add_worksheet(title="Categorias", rows=200, cols=2)
            sh.append_row(["Categoria", "Subcategoria"])
            return sh
    except Exception as e:
        st.error(f"Error con hoja Categorias: {e}")
        return None

@st.cache_data(ttl=30)
def load_categories_from_sheet():
    """Lee categorías del Sheet y las devuelve como dict. Cache 30s."""
    try:
        sh = get_cat_sheet()
        if not sh:
            return DEFAULT_CATS.copy()
        rows = sh.get_all_values()
        if len(rows) <= 1:
            # Hoja vacía o solo encabezado → cargar defaults
            save_categories_to_sheet(DEFAULT_CATS)
            return DEFAULT_CATS.copy()
        cats = {}
        for row in rows[1:]:  # saltar encabezado
            if len(row) >= 2 and row[0].strip():
                cat = row[0].strip()
                sub = row[1].strip()
                if cat not in cats:
                    cats[cat] = []
                if sub and sub not in cats[cat]:
                    cats[cat].append(sub)
        return cats if cats else DEFAULT_CATS.copy()
    except Exception:
        return DEFAULT_CATS.copy()

def save_categories_to_sheet(cats_dict):
    """Reescribe toda la hoja Categorias con el dict actual."""
    try:
        sh = get_cat_sheet()
        if not sh:
            return False
        sh.clear()
        rows = [["Categoria", "Subcategoria"]]
        for cat, subs in cats_dict.items():
            if subs:
                for sub in subs:
                    rows.append([cat, sub])
            else:
                rows.append([cat, ""])
        sh.update("A1", rows)
        load_categories_from_sheet.clear()  # limpiar cache
        return True
    except Exception as e:
        st.error(f"Error guardando categorías: {e}")
        return False

def write_to_sheet(item):
    """Escribe en hoja Lista (compra actual)."""
    return add_to_list_sheet(item)

def analyze_image_with_gemini(image_bytes):
    try:
        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        img = Image.open(io.BytesIO(image_bytes))
        img.thumbnail((1024, 1024))
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=85)
        img_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
        cats_str = ", ".join(load_categories_from_sheet().keys())
        prompt = f"""Analizá esta imagen de una etiqueta o cartel de precio de supermercado argentino.
Extraé la información visible y respondé SOLO con un JSON válido, sin texto adicional ni markdown.
Formato exacto:
{{"descripcion": "nombre del producto", "precio": 0.00, "categoria": "una de: {cats_str}", "subcategoria": "subcategoría apropiada", "unidad": "unidad|kg|100g|L"}}
Si un campo no se puede determinar con certeza, usá cadena vacía o 0. Respondé SOLO el JSON."""
        response = client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
            ]}],
            max_tokens=300
        )
        text = response.choices[0].message.content.strip()
        text = re.sub(r"```json|```", "", text).strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception as e:
        st.error(f"Error analizando imagen: {e}")
    return {}

# ── Navegación ────────────────────────────────────────────────────────────────
page = st.session_state["page"]
_list_items = load_list_from_sheet()
n_items = len(_list_items)
n_checked = sum(1 for i in _list_items if i.get("tildado", False))

st.markdown("---")

# ════════════════════════════════════════════════════════════
# PÁGINA: CARGAR
# ════════════════════════════════════════════════════════════
if page == "cargar":

    _items_now = load_list_from_sheet()
    _total_now = total(_items_now)
    _checked_now = sum(1 for i in _items_now if i.get("tildado", False))
    st.markdown(f"""
    <div class="total-box">
      <div>
        <div class="total-label">Total estimado</div>
        <div class="total-amount">{fmt_price(_total_now)}</div>
      </div>
      <div style="color:#555;font-size:13px">{len(_items_now)} productos · {_checked_now} ✓</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("#### 📷 Fotografiar etiqueta")
    photo = st.camera_input("Foto", label_visibility="collapsed")

    if photo and photo != st.session_state.get("last_photo"):
        st.session_state["last_photo"] = photo
        with st.spinner("Analizando con IA..."):
            data = analyze_image_with_gemini(photo.getvalue())
            if data:
                st.session_state["ai_data"] = data
                st.success("✓ IA completó los campos. Revisá y corregí si hace falta.")
            else:
                st.warning("No se pudo extraer info. Completá a mano.")

    ai = st.session_state.get("ai_data", {})

    st.markdown("#### Datos del producto")
    cats_dict = load_categories_from_sheet()
    cats = list(cats_dict.keys())
    if not cats:
        st.warning("No hay categorías. Agregá una en Config.")
        st.stop()

    default_cat = ai.get("categoria") if ai.get("categoria") in cats else cats[0]
    cat = st.selectbox("Categoría", cats, index=cats.index(default_cat))

    subs = cats_dict.get(cat, ["General"])
    if not subs: subs = ["General"]
    ai_sub = ai.get("subcategoria", "")
    default_sub = ai_sub if ai_sub in subs else subs[0]
    sub = st.selectbox("Subcategoría", subs, index=subs.index(default_sub))

    desc = st.text_input("Descripción del producto", value=ai.get("descripcion", ""),
                         placeholder="Ej: Leche La Serenísima entera 1L")

    col1, col2 = st.columns(2)
    with col1:
        qty = st.number_input("Cantidad", min_value=1, value=1, step=1)
    with col2:
        price_val = float(ai.get("precio", 0.0)) if ai.get("precio") else 0.0
        price = st.number_input("Precio unitario ($)", min_value=0.0, value=price_val,
                                step=10.0, format="%.2f")

    units = ["unidad", "kg", "100g", "L"]
    ai_unit = ai.get("unidad", "unidad")
    unit_idx = units.index(ai_unit) if ai_unit in units else 0
    unit = st.radio("Unidad", units, index=unit_idx, horizontal=True)

    if st.button("+ Agregar al carrito", type="primary", use_container_width=True):
        if not desc:
            st.error("Ingresá una descripción.")
        elif price <= 0:
            st.error("Ingresá un precio válido.")
        else:
            item = {
                "id": str(int(datetime.now().timestamp() * 1000)),
                "desc": desc, "price": price, "qty": qty,
                "unit": unit, "cat": cat, "sub": sub,
            }
            ok = write_to_sheet(item)
            st.session_state["ai_data"] = {}
            st.session_state["last_photo"] = None
            subtotal = price * qty
            msg = f"✓ {desc} — {fmt_price(subtotal)}"
            if ok: msg += " · Guardado en Lista ✓"
            st.success(msg)
            st.rerun()

# ════════════════════════════════════════════════════════════
# PÁGINA: LISTA (MEJORADA)
# ════════════════════════════════════════════════════════════
elif page == "lista":

    items = load_list_from_sheet()

    st.markdown("### 📋 Lista de compras")

    # Filtros
    col1, col2 = st.columns([2, 1])
    with col1:
        search_text = st.text_input("🔍 Buscar producto...", "", placeholder="Ej: leche, coca, pan...")
    with col2:
        view_mode = st.radio("Vista", ["Cards", "Tabla"], horizontal=True, label_visibility="collapsed")

    # Filtro por categoría
    cats_dict = load_categories_from_sheet()
    all_cats = ["Todas"] + list(cats_dict.keys())
    selected_cat = st.selectbox("Filtrar por categoría", all_cats, index=0)

    # Aplicar filtros
    filtered_items = items
    if search_text:
        search_lower = search_text.lower()
        filtered_items = [i for i in filtered_items if search_lower in i["desc"].lower()]
    
    if selected_cat != "Todas":
        filtered_items = [i for i in filtered_items if i.get("cat") == selected_cat]

    # Estadísticas
    total_val = total(filtered_items)
    total_check = sum(float(i.get("price",0)) * float(i.get("qty",1)) 
                     for i in filtered_items if i.get("tildado", False))
    total_pend = total_val - total_check
    sin_precio = sum(1 for i in filtered_items if float(i.get("price", 0)) == 0)

    col_t1, col_t2, col_t3 = st.columns(3)
    with col_t1: st.metric("Total", fmt_price(total_val))
    with col_t2: st.metric("✓ En changuito", fmt_price(total_check))
    with col_t3: st.metric("⏳ Pendiente", fmt_price(total_pend))

    if sin_precio > 0:
        st.warning(f"⚠️ {sin_precio} producto{'s' if sin_precio>1 else ''} sin precio")

    st.caption(f"{sum(1 for i in filtered_items if i.get('tildado'))} de {len(filtered_items)} tildados • {len(items)} en total")
    st.markdown("---")

    # === EDICIÓN ===
    if st.session_state.get("editing_item"):
        edit = st.session_state["editing_item"]
        st.markdown(f"### ✏️ Actualizar: **{edit['desc']}**")
        
        last_price = get_last_price(edit['desc'])
        if last_price and float(edit.get('price',0)) == 0:
            st.info(f"💡 Último precio: {fmt_price(last_price)}")

        e_price = st.number_input("Precio unitario ($)", min_value=0.0, value=float(edit.get('price',0)), step=10.0, format="%.2f")
        e_qty = st.number_input("Cantidad", min_value=1, value=int(edit.get('qty',1)), step=1)
        e_unit = st.radio("Unidad", ["unidad", "kg", "100g", "L"], horizontal=True)

        col_ok, col_can = st.columns(2)
        with col_ok:
            if st.button("✓ Guardar cambios", type="primary"):
                update_price_in_list(edit["id"], e_price, e_qty, e_unit)
                st.session_state["editing_item"] = None
                st.rerun()
        with col_can:
            if st.button("Cancelar"):
                st.session_state["editing_item"] = None
                st.rerun()
        st.markdown("---")

    # === VISTA CARDS ===
    elif view_mode == "Cards":
        if not filtered_items:
            st.info("No hay productos")
        for item in filtered_items:
            item_id = item["id"]
            is_checked = item.get("tildado", False)
            sin_precio_item = float(item.get("price", 0)) == 0

            col_chk, col_info, col_edit, col_del = st.columns([0.8, 5, 1, 1])

            with col_chk:
                if st.checkbox("", value=is_checked, key=f"chk_{item_id}"):
                    if not is_checked: toggle_tildado(item_id, True); st.rerun()
                elif is_checked:
                    toggle_tildado(item_id, False); st.rerun()

            with col_info:
                st.markdown(f"""
                <div class="item-row">
                  <div style="font-weight:500;">{item['desc']}</div>
                  <div style="color:#888;font-size:13px;">
                    {item.get('cat','')} → {item.get('sub','')}
                  </div>
                  <div style="color:#aaa;font-size:13px;">{item['qty']} {item['unit']}</div>
                </div>
                """, unsafe_allow_html=True)

            with col_edit:
                if st.button("💲" if sin_precio_item else "✏️", key=f"edit_{item_id}"):
                    st.session_state["editing_item"] = item
                    st.rerun()
            with col_del:
                if st.button("✕", key=f"del_{item_id}"):
                    delete_from_list(item_id)
                    st.rerun()

    # === VISTA TABLA (Mejorada) ===
    else:
        if not filtered_items:
            st.info("No hay productos con los filtros actuales.")
        else:
            # Usamos st.dataframe con configuración para que se vea bien
            data_for_table = []
            for item in filtered_items:
                data_for_table.append({
                    "✅": "☑️" if item.get("tildado") else "⬜",
                    "Producto": item["desc"],
                    "Categoría": f"{item.get('cat','')} → {item.get('sub','')}",
                    "Cantidad": f"{item['qty']} {item['unit']}",
                    "Precio Total": fmt_price(float(item.get("price",0)) * float(item.get("qty",1))) if float(item.get("price",0)) > 0 else "sin precio",
                    "id": item["id"]
                })

            df = pd.DataFrame(data_for_table)  # Necesitamos importar pandas

            # Mostrar dataframe configurable
            st.dataframe(
                df.drop(columns=["id"]),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "✅": st.column_config.TextColumn("Tildado", width="small"),
                    "Producto": st.column_config.TextColumn("Producto", width="medium"),
                    "Categoría": st.column_config.TextColumn("Categoría", width="medium"),
                    "Cantidad": st.column_config.TextColumn("Cantidad", width="small"),
                    "Precio Total": st.column_config.TextColumn("Precio Total", width="small")
                }
            )

            # Botones de acción (por ahora separados)
            st.caption("Para editar o eliminar, usa la vista **Cards** por el momento.")

# ════════════════════════════════════════════════════════════
# PÁGINA: CONFIG
# ════════════════════════════════════════════════════════════
elif page == "config":
    st.markdown("### ⚙️ Categorías y subcategorías")
    st.caption("Los cambios aplican en el formulario de carga inmediatamente.")

    cats_dict = load_categories_from_sheet()

    # ── Agregar nueva categoría ──────────────────────────────
    with st.expander("➕ Agregar nueva categoría", expanded=False):
        new_cat = st.text_input("Nombre", placeholder="Ej: Dietética", key="new_cat_input")
        if st.button("Crear categoría", use_container_width=True):
            nc = new_cat.strip()
            if not nc:
                st.warning("Escribí un nombre.")
            elif nc in cats_dict:
                st.warning(f"Ya existe '{nc}'.")
            else:
                cats_dict[nc] = []
                if save_categories_to_sheet(cats_dict):
                    st.success(f"✓ Categoría '{nc}' creada y guardada.")
                    st.rerun()

    st.markdown("---")

    if not cats_dict:
        st.info("No hay categorías. Agregá una arriba.")
    else:
        for cat_name in list(cats_dict.keys()):
            subs = cats_dict.get(cat_name, [])
            with st.expander(f"📁 {cat_name}  ({len(subs)} subcategorías)"):

                # Subcategorías existentes
                if subs:
                    st.markdown("**Subcategorías:**")
                    for sub in list(subs):
                        c1, c2 = st.columns([5, 1])
                        with c1:
                            st.markdown(f"• {sub}")
                        with c2:
                            if st.button("✕", key=f"delsub_{cat_name}_{sub}"):
                                cats_dict[cat_name].remove(sub)
                                save_categories_to_sheet(cats_dict)
                                st.rerun()
                else:
                    st.caption("Sin subcategorías todavía.")

                st.markdown("")

                # Agregar subcategoría
                st.markdown("**Agregar subcategoría:**")
                c1, c2 = st.columns([3, 1])
                with c1:
                    new_sub = st.text_input("Sub", placeholder="Nueva subcategoría...",
                                            label_visibility="collapsed", key=f"ns_{cat_name}")
                with c2:
                    if st.button("+ Agregar", key=f"as_{cat_name}", use_container_width=True):
                        ns = new_sub.strip()
                        if not ns:
                            st.warning("Escribí un nombre.")
                        elif ns in subs:
                            st.warning("Ya existe.")
                        else:
                            cats_dict[cat_name].append(ns)
                            if save_categories_to_sheet(cats_dict):
                                st.rerun()

                st.markdown("")

                # Renombrar categoría
                st.markdown("**Renombrar categoría:**")
                c1, c2 = st.columns([3, 1])
                with c1:
                    rename_val = st.text_input("Nombre", value=cat_name,
                                               label_visibility="collapsed", key=f"rn_{cat_name}")
                with c2:
                    if st.button("OK", key=f"btn_rn_{cat_name}", use_container_width=True):
                        rv = rename_val.strip()
                        if rv and rv != cat_name and rv not in cats_dict:
                            new_cats = {(rv if k == cat_name else k): v for k, v in cats_dict.items()}
                            if save_categories_to_sheet(new_cats):
                                st.rerun()

                st.markdown("")

                # Eliminar categoría
                if st.button(f"🗑️ Eliminar '{cat_name}'", key=f"dc_{cat_name}",
                             use_container_width=True):
                    del cats_dict[cat_name]
                    if save_categories_to_sheet(cats_dict):
                        st.rerun()

# ════════════════════════════════════════════════════════════
# BARRA DE NAVEGACIÓN FIJA (siempre al final del DOM)
# ════════════════════════════════════════════════════════════
st.markdown('<div id="nav-bar-wrapper">', unsafe_allow_html=True)
nav_col1, nav_col2, nav_col3 = st.columns(3)
with nav_col1:
    if st.button("🛒 Cargar", use_container_width=True, key="nav_cargar",
                 type="primary" if page=="cargar" else "secondary"):
        st.session_state["page"] = "cargar"
        st.rerun()
with nav_col2:
    if st.button(f"📋 Lista ({n_items})", use_container_width=True, key="nav_lista",
                 type="primary" if page=="lista" else "secondary"):
        st.session_state["page"] = "lista"
        st.rerun()
with nav_col3:
    if st.button("⚙️ Config", use_container_width=True, key="nav_config",
                 type="primary" if page=="config" else "secondary"):
        st.session_state["page"] = "config"
        st.rerun()
st.markdown('</div>', unsafe_allow_html=True)

st.markdown("""
<style>
  /* La barra de nav ocupa el ancho completo fijada abajo */
  #nav-bar-wrapper {
    position: fixed !important;
    bottom: 0 !important;
    left: 0 !important;
    right: 0 !important;
    z-index: 99999 !important;
    background: #111111 !important;
    border-top: 1px solid #2a2a2a !important;
    padding: 8px 12px 14px 12px !important;
  }
  /* El div de columnas que Streamlit genera dentro del wrapper */
  #nav-bar-wrapper > div[data-testid="stHorizontalBlock"] {
    max-width: 480px !important;
    margin: 0 auto !important;
  }
  /* Ajuste de botones dentro de la nav */
  #nav-bar-wrapper button {
    font-size: 0.85rem !important;
    padding: 0.4rem 0.2rem !important;
  }
</style>
""", unsafe_allow_html=True)
