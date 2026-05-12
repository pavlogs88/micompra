import streamlit as st
from groq import Groq
import base64
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import json
import re
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
  .block-container { padding-top: 1rem; padding-bottom: 5rem; max-width: 480px; }

  /* Botón primario verde */
  div[data-testid="stButton"] > button[kind="primary"] {
    background-color: #3ddc84 !important; color: #000 !important;
    border: none !important; border-radius: 10px !important;
    font-weight: 600 !important; font-size: 1rem !important;
  }
  div[data-testid="stButton"] > button[kind="secondary"] {
    border-radius: 10px !important;
  }

  /* Barra de navegación inferior */
  .nav-bar {
    position: fixed; bottom: 0; left: 0; right: 0;
    background: #111; border-top: 1px solid #2a2a2a;
    display: flex; z-index: 999; max-width: 480px; margin: 0 auto;
  }
  .nav-btn {
    flex: 1; padding: 10px 4px 8px; border: none; background: transparent;
    color: #555; font-size: 10px; cursor: pointer;
    display: flex; flex-direction: column; align-items: center; gap: 3px;
    font-family: inherit;
  }
  .nav-btn.active { color: #3ddc84; }
  .nav-btn .nav-icon { font-size: 20px; line-height: 1; }

  /* Tarjeta total */
  .total-box {
    background: #1a1a1a; border: 1px solid #3ddc84;
    border-radius: 12px; padding: 14px 18px;
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 1rem;
  }
  .total-label { color: #888; font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; }
  .total-amount { color: #3ddc84; font-size: 26px; font-weight: 700; font-family: monospace; }

  /* Tarjeta de item */
  .item-row {
    background: #1a1a1a; border: 1px solid #2a2a2a;
    border-radius: 10px; padding: 10px 14px; margin-bottom: 6px;
  }
  .item-name { font-weight: 500; font-size: 14px; }
  .item-meta { color: #888; font-size: 12px; margin-top: 3px; }
  .item-price { color: #3ddc84; font-weight: 700; font-family: monospace; font-size: 15px; }
  .item-done { opacity: 0.45; text-decoration: line-through; }
  .tag {
    display: inline-block; background: #2a2a2a; color: #888;
    border-radius: 20px; padding: 2px 8px; font-size: 11px; margin-right: 4px;
  }

  div[data-testid="stNumberInput"] input { font-size: 16px; }
  div[data-testid="stTextInput"] input { font-size: 15px; }
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
if "items"      not in st.session_state: st.session_state["items"] = []
if "checked"    not in st.session_state: st.session_state["checked"] = {}
if "categories" not in st.session_state: st.session_state["categories"] = DEFAULT_CATS.copy()
if "ai_data"    not in st.session_state: st.session_state["ai_data"] = {}
if "last_photo" not in st.session_state: st.session_state["last_photo"] = None
if "page"       not in st.session_state: st.session_state["page"] = "cargar"

# ── Helpers ──────────────────────────────────────────────────────────────────
def fmt_price(n):
    try:
        return f"${float(n):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "$0,00"

def total():
    try:
        items = st.session_state.get("items", [])
        if not items: return 0.0
        return sum(float(i.get("price", 0)) * float(i.get("qty", 1)) for i in items)
    except Exception:
        return 0.0

def get_sheet():
    try:
        creds_data = json.loads(st.secrets["GOOGLE_CREDS"])
        scopes = ["https://www.googleapis.com/auth/spreadsheets",
                  "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_data, scopes=scopes)
        gc = gspread.authorize(creds)
        return gc.open_by_key(st.secrets["SHEET_ID"]).sheet1
    except Exception as e:
        st.error(f"Error conectando con Google Sheets: {e}")
        return None

def write_to_sheet(item):
    sheet = get_sheet()
    if not sheet: return False
    try:
        if sheet.row_count == 0 or not sheet.row_values(1):
            sheet.append_row(["Fecha","Hora","Categoría","Subcategoría",
                              "Descripción","Cantidad","Unidad","Precio Unitario","Total"])
        now = datetime.now()
        sheet.append_row([
            now.strftime("%d/%m/%Y"), now.strftime("%H:%M"),
            item["cat"], item["sub"], item["desc"],
            item["qty"], item["unit"], item["price"],
            round(item["price"] * item["qty"], 2)
        ])
        return True
    except Exception as e:
        st.error(f"Error escribiendo en Sheet: {e}")
        return False

def analyze_image_with_gemini(image_bytes):
    try:
        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        img = Image.open(io.BytesIO(image_bytes))
        img.thumbnail((1024, 1024))
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=85)
        img_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
        cats_str = ", ".join(st.session_state.get("categories", {}).keys())
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
n_items = len(st.session_state.get("items", []))
n_checked = sum(1 for v in st.session_state.get("checked", {}).values() if v)

# Barra inferior fija
st.markdown(f"""
<div class="nav-bar">
  <button class="nav-btn {'active' if page=='cargar' else ''}" onclick="window.location.href='?page=cargar'">
    <span class="nav-icon">🛒</span>Cargar
  </button>
  <button class="nav-btn {'active' if page=='lista' else ''}" onclick="window.location.href='?page=lista'">
    <span class="nav-icon">📋</span>Lista ({n_items})
  </button>
  <button class="nav-btn {'active' if page=='config' else ''}" onclick="window.location.href='?page=config'">
    <span class="nav-icon">⚙️</span>Config
  </button>
</div>
""", unsafe_allow_html=True)

# Leer page desde query params
params = st.query_params
if "page" in params:
    st.session_state["page"] = params["page"]
    page = params["page"]

# Botones de navegación reales (visibles arriba)
col1, col2, col3 = st.columns(3)
with col1:
    if st.button("🛒 Cargar", use_container_width=True,
                 type="primary" if page=="cargar" else "secondary"):
        st.session_state["page"] = "cargar"
        st.rerun()
with col2:
    if st.button(f"📋 Lista ({n_items})", use_container_width=True,
                 type="primary" if page=="lista" else "secondary"):
        st.session_state["page"] = "lista"
        st.rerun()
with col3:
    if st.button("⚙️ Config", use_container_width=True,
                 type="primary" if page=="config" else "secondary"):
        st.session_state["page"] = "config"
        st.rerun()

st.markdown("---")

# ════════════════════════════════════════════════════════════
# PÁGINA: CARGAR
# ════════════════════════════════════════════════════════════
if page == "cargar":

    st.markdown(f"""
    <div class="total-box">
      <div>
        <div class="total-label">Total estimado</div>
        <div class="total-amount">{fmt_price(total())}</div>
      </div>
      <div style="color:#555;font-size:13px">{n_items} productos · {n_checked} ✓</div>
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
    cats = list(st.session_state.get("categories", {}).keys())
    if not cats:
        st.warning("No hay categorías. Agregá una en Config.")
        st.stop()

    default_cat = ai.get("categoria") if ai.get("categoria") in cats else cats[0]
    cat = st.selectbox("Categoría", cats, index=cats.index(default_cat))

    subs = st.session_state.get("categories", {}).get(cat, ["General"])
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
                "id": int(datetime.now().timestamp() * 1000),
                "desc": desc, "price": price, "qty": qty,
                "unit": unit, "cat": cat, "sub": sub,
                "ts": datetime.now().isoformat()
            }
            st.session_state["items"].append(item)
            st.session_state["ai_data"] = {}
            st.session_state["last_photo"] = None
            ok = write_to_sheet(item)
            subtotal = price * qty
            msg = f"✓ {desc} — {fmt_price(subtotal)}"
            if ok: msg += " · Guardado en Sheets ✓"
            st.success(msg)
            st.rerun()

# ════════════════════════════════════════════════════════════
# PÁGINA: LISTA
# ════════════════════════════════════════════════════════════
elif page == "lista":

    items = st.session_state.get("items", [])
    checked = st.session_state.get("checked", {})

    if not items:
        st.markdown("""
        <div style="text-align:center;padding:60px 0;color:#555">
          <div style="font-size:40px;margin-bottom:12px">🛒</div>
          <div>Todavía no cargaste productos.</div>
        </div>
        """, unsafe_allow_html=True)
    else:
        # Totales
        total_val = total()
        total_check = sum(
            float(i.get("price",0)) * float(i.get("qty",1))
            for i in items if checked.get(str(i["id"]), False)
        )
        total_pend = total_val - total_check

        col_t1, col_t2, col_t3 = st.columns(3)
        with col_t1:
            st.metric("Total", fmt_price(total_val))
        with col_t2:
            st.metric("✓ En changuito", fmt_price(total_check))
        with col_t3:
            st.metric("⏳ Pendiente", fmt_price(total_pend))

        st.markdown("---")
        st.caption(f"{n_checked} de {n_items} productos tildados")

        # Lista con checkboxes
        for item in items:
            item_id = str(item["id"])
            is_checked = checked.get(item_id, False)

            col_chk, col_info, col_del = st.columns([1, 6, 1])

            with col_chk:
                new_val = st.checkbox("", value=is_checked, key=f"chk_{item_id}")
                if new_val != is_checked:
                    st.session_state["checked"][item_id] = new_val
                    st.rerun()

            with col_info:
                name_style = "item-done" if is_checked else ""
                st.markdown(f"""
                <div class="item-row" style="margin-bottom:2px">
                  <div class="item-name {name_style}">{item['desc']}</div>
                  <div style="display:flex;justify-content:space-between;align-items:center">
                    <div class="item-meta">
                      <span class="tag">{item['cat']}</span>
                      <span class="tag">{item['qty']} {item['unit']}</span>
                    </div>
                    <div class="item-price">{fmt_price(item['price'] * item['qty'])}</div>
                  </div>
                </div>
                """, unsafe_allow_html=True)

            with col_del:
                if st.button("✕", key=f"del_{item['id']}"):
                    st.session_state["items"] = [i for i in items if i["id"] != item["id"]]
                    if item_id in st.session_state["checked"]:
                        del st.session_state["checked"][item_id]
                    st.rerun()

        st.markdown("---")
        if st.button("🗑️ Limpiar todo", use_container_width=True):
            st.session_state["items"] = []
            st.session_state["checked"] = {}
            st.rerun()

# ════════════════════════════════════════════════════════════
# PÁGINA: CONFIG
# ════════════════════════════════════════════════════════════
elif page == "config":
    st.markdown("### ⚙️ Categorías y subcategorías")
    st.caption("Los cambios aplican en el formulario de carga inmediatamente.")

    cats_dict = st.session_state.get("categories", {})

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
                st.session_state["categories"][nc] = []
                st.success(f"✓ Categoría '{nc}' creada.")
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
                                st.session_state["categories"][cat_name].remove(sub)
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
                            st.session_state["categories"][cat_name].append(ns)
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
                            st.session_state["categories"] = new_cats
                            st.rerun()

                st.markdown("")

                # Eliminar categoría
                if st.button(f"🗑️ Eliminar '{cat_name}'", key=f"dc_{cat_name}",
                             use_container_width=True):
                    del st.session_state["categories"][cat_name]
                    st.rerun()
